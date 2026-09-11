"""Recoverable transactions around the Org adapter's multi-file mutations.

The dependency writes a refile's source before its destination. A durable
undo journal makes that sequence recoverable after failure or process death.
All adapter commands serialize through one local lock, including recovery.
Uncooperative external edits are never overwritten during recovery.
"""
from contextvars import ContextVar
from functools import wraps
import hashlib
import json
import os
import re
from pathlib import Path

from file_utils import atomic_write_json, atomic_write_text, file_lock, fsync_directory
from org_workspace import OrgWorkspace
from org_workspace.workspace import CatastrophicShrinkError

_current = ContextVar("org_transaction", default=None)


def watch_file(path):
    transaction = _current.get()
    if transaction is None:
        raise RuntimeError("Org reads for mutation require a serialized transaction")
    return transaction.watch(path)


def write_org_text(path, text):
    transaction = _current.get()
    if transaction is None:
        raise RuntimeError("Org writes require a serialized transaction")
    transaction.write(Path(path).resolve(), text)


def move_file(source, destination):
    transaction = _current.get()
    if transaction is None:
        raise RuntimeError("file moves require a serialized transaction")
    transaction.move(Path(source).absolute(), Path(destination).absolute())


def journal_path():
    state = Path(os.environ.get("DATACORE_STATE", Path.home() / ".datacore" / "state"))
    return state / "org-transaction.json"


def read_text(path):
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


class RecoveryRequired(RuntimeError):
    pass


def recover(path):
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    if not isinstance(document, dict) or document.get("version") != 1 or not isinstance(document.get("files"), dict):
        raise RecoveryRequired("invalid Org transaction journal; preserve it for recovery")
    files = document["files"]
    # Check EVERY file before restoring any; a later conflict must not leave
    # an otherwise untouched transaction half rolled back.
    for name, entry in files.items():
        target = Path(name)
        if not target.is_absolute() or not isinstance(entry, dict):
            raise RecoveryRequired("invalid Org transaction entry")
        if ("before" not in entry or not isinstance(entry.get("versions"), list)
                or (entry["before"] is not None and not isinstance(entry["before"], str))
                or digest(entry["before"]) not in entry["versions"]):
            raise RecoveryRequired("invalid Org transaction backup")
        if target.is_symlink() or digest(read_text(target)) not in entry["versions"]:
            raise RecoveryRequired("Org file changed outside its transaction; journal retained for manual recovery")
    for name, entry in files.items():
        target = Path(name)
        before = entry["before"]
        if before is None:
            if target.exists():
                target.unlink()
                fsync_directory(target.parent)
        elif read_text(target) != before:
            atomic_write_text(target, before)
    path.unlink()
    fsync_directory(path.parent)


class Transaction:
    def __init__(self, path):
        self.path = path
        self.files = {}
        self.failed = False
        self.started = False

    def watch(self, path):
        path = Path(path).resolve()
        name = str(path)
        if name not in self.files:
            text = read_text(path)
            self.files[name] = {"before": text, "versions": [digest(text)], "current": digest(text)}
        return self.files[name]

    def write(self, path, content):
        try:
            entry = self.watch(path)
            if digest(read_text(path)) != entry["current"]:
                raise RecoveryRequired("Org file changed after loading; refusing stale overwrite")
            entry["versions"].append(digest(content))
            # Durable journal BEFORE the file can change, including on a
            # write that replaces successfully but then fails directory fsync.
            atomic_write_json(self.path, {"version": 1, "files": self.files})
            self.started = True
            atomic_write_text(path, content)
            entry["current"] = digest(content)
        except BaseException:
            self.failed = True
            raise

    def move(self, source, destination):
        from safe_move import rename_noreplace
        if source.is_symlink() or destination.is_symlink():
            raise RecoveryRequired("refusing symbolic-link move")
        destination_key = str(destination.resolve())
        destination_was_watched = destination_key in self.files
        self.watch(source)
        self.watch(destination)
        content = read_text(source)
        if content is None or read_text(destination) is not None:
            raise RecoveryRequired("move requires an existing source and absent destination")
        src, dst = self.files[str(source.resolve())], self.files[str(destination.resolve())]
        if digest(read_text(source)) != src["current"] or digest(read_text(destination)) != dst["current"]:
            raise RecoveryRequired("file changed before move")
        try:
            src["versions"].append(None)
            dst["versions"].append(digest(content))
            atomic_write_json(self.path, {"version": 1, "files": self.files})
            self.started = True
            try:
                rename_noreplace(source, destination)
            except FileExistsError:
                # Native NOREPLACE guarantees no mutation. A competing file
                # is not ours to roll back; do not strand the global journal.
                if not destination_was_watched:
                    del self.files[destination_key]
                    src["versions"].pop()
                    atomic_write_json(self.path, {"version": 1, "files": self.files})
                raise
            src["current"], dst["current"] = None, digest(content)
        except BaseException:
            self.failed = True
            raise

    def commit(self):
        if self.failed:
            raise RecoveryRequired("an Org write failed inside the operation")
        if self.started:
            self.path.unlink()
            fsync_directory(self.path.parent)


def serialized(function):
    @wraps(function)
    def run(*args, **kwargs):
        if _current.get() is not None:
            return function(*args, **kwargs)
        path = journal_path()
        with file_lock(path, timeout=30):
            recover(path)
            transaction = Transaction(path)
            token = _current.set(transaction)
            try:
                result = function(*args, **kwargs)
                if isinstance(result, dict) and result.get("error"):
                    if transaction.started:
                        recover(path)
                else:
                    transaction.commit()
                return result
            except BaseException:
                if path.exists():
                    recover(path)
                raise
            finally:
                _current.reset(token)
    return run


class SafeOrgWorkspace(OrgWorkspace):
    @staticmethod
    def _heading(value):
        if not isinstance(value, str) or len(value.splitlines()) != 1 or "\x00" in value:
            raise ValueError("an Org heading must be one nonempty line")

    @staticmethod
    def _property_key(key):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_@#%+.-]+", key):
            raise ValueError("invalid Org property name")

    def create_node(self, file, heading, *args, **kwargs):
        self._heading(heading)
        # Keyword properties follow the dependency's create_node signature;
        # structural arguments are not property keys.
        for key in set(kwargs) - {"state", "parent", "level", "tags", "body", "dedup"}:
            self._property_key(key)
        return super().create_node(file, heading, *args, **kwargs)

    def set_heading(self, node, value):
        self._heading(value)
        return super().set_heading(node, value)

    def set_property(self, node, key, value):
        self._property_key(key)
        return super().set_property(node, key, value)

    def load(self, path):
        transaction = _current.get()
        if transaction:
            transaction.watch(path)
        return super().load(path)

    def _safe_write(self, path, content):
        path = Path(path).resolve()
        transaction = _current.get()
        if transaction is None:
            raise RuntimeError("Org mutations require a serialized transaction")
        try:
            previous = read_text(path)
            if previous is not None:
                old_lines = previous.count("\n")
                if old_lines > 20 and content.count("\n") < int(old_lines * (1 - self._MAX_SHRINK_FRACTION)):
                    raise CatastrophicShrinkError("serialized Org output exceeds the dependency's shrink guard")
            transaction.write(path, content)
        except BaseException:
            transaction.failed = True
            raise
