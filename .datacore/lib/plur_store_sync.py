#!/usr/bin/env python3
"""Converge this machine's PLUR store (~/.plur) with the shared plur-engrams
remote by merging ENGRAMS, not lines -- then let `plur sync` reindex.

Why this exists (SPC-7, 2026-09-29): `plur sync` falls back to a git line merge
of the 16 MB engrams.yaml. Two machines that both wrote engrams since they last
met always conflict there, `plur sync` aborts ("Sync conflict ... manual
resolution") and that machine never receives memory again. Box stopped on
2026-08-10 and nightshift on 2026-07-29 for exactly that reason, and nothing
scheduled the sync anyway.

What it does, per run:
  1. fetch the remote;
  2. three-way merge every record file PLUR syncs (engrams.yaml, episodes.yaml,
     candidates.yaml, tensions.yaml, packs/**/engrams.yaml) BY RECORD ID, and by
     top-level field inside a record: a field only one side changed takes that
     side; a field both changed takes the remote (first to reach the shared
     store wins, deterministically); a record one side deleted and the other
     left alone is deleted; a record one side deleted and the other changed is
     kept. Other files take the side that changed; .gitignore unions its lines;
  3. commit the merge with both histories as parents -- exactly what PLUR
     would commit: `scope: local` engrams stay in this machine's working file
     and are stripped from the commit (PLUR's personal-remote rule). A store
     configured `sync.remote_type: shared` is refused, not pushed as personal;
  4. write the merged files while holding PLUR's own engrams.yaml.lock (same
     token protocol, never stolen from a live holder) and only if nothing
     wrote them since they were read -- otherwise start over;
  5. push, then run `plur sync` so PLUR reindexes and confirms "up to date";
  6. write a status file (~/.datacore/state/plur-sync.json) holding commit ids
     and counts only -- never engram content -- which the job manifest checks.

Usage: plur_store_sync.py [--store ~/.plur] [--state FILE] [--no-index]
Exit 0 when this machine's store holds the remote and the remote holds it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

RECORD_ROOT_FILES = ("engrams.yaml", "episodes.yaml", "candidates.yaml", "tensions.yaml")
PACK_ALLOW_NAMES = ("SKILL.md", "engrams.yaml", "INTEGRITY", "metadata.json")
LOCK_WAIT_S = 30.0
LOCK_STALE_S = 60.0
ATTEMPTS = 3
GIT_TIMEOUT = 180
_MISSING = object()


# ── YAML (timestamps stay strings, so a round trip never retypes a value) ─────
_BaseLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_BaseDumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


class _Loader(_BaseLoader):
    pass


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in _BaseLoader.yaml_implicit_resolvers.items()
}


def _load(text: bytes | None):
    if text is None:
        return None
    return yaml.load(text.decode("utf-8"), Loader=_Loader)


def _dump(data) -> bytes:
    return yaml.dump(data, Dumper=_BaseDumper, sort_keys=False, allow_unicode=True,
                     width=120, default_flow_style=False).encode("utf-8")


# ── git ───────────────────────────────────────────────────────────────────────
class SyncError(Exception):
    pass


def _git(store: Path, *args, input: bytes | None = None, check=True, env=None) -> bytes:
    r = subprocess.run(["git", "-C", str(store), *args], input=input, capture_output=True,
                       timeout=GIT_TIMEOUT, env=env)
    if check and r.returncode != 0:
        raise SyncError(f"git {args[0]} failed: {r.stderr.decode(errors='replace').strip()[:300]}")
    return r.stdout


def _rev(store, ref):
    return _git(store, "rev-parse", "--verify", "--quiet", ref, check=False).decode().strip() or None


def _is_ancestor(store, a, b) -> bool:
    return subprocess.run(["git", "-C", str(store), "merge-base", "--is-ancestor", a, b],
                          capture_output=True, timeout=GIT_TIMEOUT).returncode == 0


def _tree_blobs(store, rev) -> dict[str, str]:
    if not rev:
        return {}
    out = _git(store, "ls-tree", "-r", "-z", rev)
    blobs = {}
    for entry in out.split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        _mode, kind, sha = meta.split()
        if kind == b"blob":
            blobs[path.decode()] = sha.decode()
    return blobs


def _blob(store, sha):
    return None if sha is None else _git(store, "cat-file", "blob", sha)


def _hash(store, content: bytes, write=False) -> str:
    args = ["hash-object", "--stdin"] + (["-w"] if write else [])
    return _git(store, *args, input=content).decode().strip()


# ── which paths PLUR itself syncs from the working tree ──────────────────────
def _is_record_file(path: str) -> bool:
    return path in RECORD_ROOT_FILES or (path.startswith("packs/") and path.endswith("/engrams.yaml"))


def _is_sync_path(path: str) -> bool:
    if path == ".gitignore" or path in RECORD_ROOT_FILES:
        return True
    return path.startswith("packs/") and path.rsplit("/", 1)[-1] in PACK_ALLOW_NAMES


def _worktree_sync_paths(store: Path) -> set[str]:
    found = {p for p in (".gitignore", *RECORD_ROOT_FILES) if (store / p).is_file()}
    packs = store / "packs"
    if packs.is_dir():
        for f in packs.rglob("*"):
            if f.is_file() and f.name in PACK_ALLOW_NAMES:
                found.add(f.relative_to(store).as_posix())
    return found


def _read_work(store: Path, path: str) -> bytes | None:
    try:
        return (store / path).read_bytes()
    except FileNotFoundError:
        return None


# ── the merge ─────────────────────────────────────────────────────────────────
def _key(rec) -> str:
    if isinstance(rec, dict) and rec.get("id"):
        return str(rec["id"])
    return "#" + hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()


def _index(records) -> dict:
    out = {}
    for r in records or []:
        out.setdefault(_key(r), r)  # a duplicate id keeps its first occurrence
    return out


def _merge_fields(base, ours, theirs):
    """Three-way merge of one record (or a top-level mapping) field by field."""
    if ours == theirs:
        return ours
    if not (isinstance(ours, dict) and isinstance(theirs, dict)):
        if ours == base:
            return theirs
        if theirs == base:
            return ours
        return theirs
    base = base if isinstance(base, dict) else {}
    out = {}
    for k in list(theirs) + [k for k in ours if k not in theirs]:
        b, o, t = base.get(k, _MISSING), ours.get(k, _MISSING), theirs.get(k, _MISSING)
        if o == t:
            v = o
        elif o == b:
            v = t
        elif t == b:
            v = o
        else:
            v = t
        if v is not _MISSING:
            out[k] = v
    return out


def merge_records(base, ours, theirs) -> list:
    b, o, t = _index(base), _index(ours), _index(theirs)
    order = list(t) + [k for k in o if k not in t]
    merged = []
    for k in order:
        bv, ov, tv = b.get(k), o.get(k), t.get(k)
        if tv is None:
            if bv is not None and ov == bv:
                continue  # the remote deleted it and this machine left it alone
            merged.append(ov)
        elif ov is None:
            if bv is not None and tv == bv:
                continue  # this machine deleted it and the remote left it alone
            merged.append(tv)
        else:
            merged.append(_merge_fields(bv, ov, tv))
    return merged


def _split(doc):
    """(records, container) for a list file or a {engrams: [...]} mapping."""
    if doc is None:
        return None, None
    if isinstance(doc, list):
        return doc, None
    if isinstance(doc, dict) and isinstance(doc.get("engrams"), list):
        return doc["engrams"], {k: v for k, v in doc.items() if k != "engrams"}
    raise SyncError("unrecognised record file shape")


def _same_engram(a: dict, b: dict) -> bool:
    if a.get("statement") is not None and a.get("statement") == b.get("statement"):
        return True
    return bool(a.get("content_hash")) and a.get("content_hash") == b.get("content_hash")


HOST_TAG: str | None = None


def _host_tag() -> str:
    if HOST_TAG:
        return HOST_TAG
    tag = "".join(c for c in socket.gethostname().split(".")[0].lower() if c.isalnum())
    return tag or "host"


def find_collisions(base_doc, ours_doc, theirs_doc, tag: str) -> dict[str, str]:
    """{id: new id} for this side's engrams whose id the remote minted too, for a
    different engram, since the two last shared history."""
    b = _index(_split(base_doc)[0] if base_doc is not None else [])
    o = _index(_split(ours_doc)[0] if ours_doc is not None else [])
    t = _index(_split(theirs_doc)[0] if theirs_doc is not None else [])
    taken = set(o) | set(t)
    renames = {}
    for k, ov in o.items():
        tv = t.get(k)
        if k in b or tv is None or k.startswith("#") or not isinstance(ov, dict) or not isinstance(tv, dict):
            continue
        if _same_engram(ov, tv):
            continue
        new, n = f"{k}-{tag}", 2
        while new in taken:
            new, n = f"{k}-{tag}{n}", n + 1
        taken.add(new)
        renames[k] = new
    return renames


def _rename_refs(doc, renames: dict[str, str]):
    """Every exact id token of a renamed engram, in every string of `doc`."""
    if not renames:
        return doc
    import re
    rx = re.compile(r"(?<![A-Za-z0-9-])(" + "|".join(re.escape(k) for k in sorted(renames, key=len, reverse=True))
                    + r")(?![A-Za-z0-9-])")

    def walk(v):
        if isinstance(v, str):
            return rx.sub(lambda m: renames[m.group(1)], v) if rx.search(v) else v
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {walk(k) if isinstance(k, str) else k: walk(x) for k, x in v.items()}
        return v
    return walk(doc)


def merge_record_file(base_doc, ours_doc, theirs_doc):
    if ours_doc is None and theirs_doc is None:
        return None
    if ours_doc is None:
        return None if base_doc is not None and theirs_doc == base_doc else theirs_doc
    if theirs_doc is None:
        return None if base_doc is not None and ours_doc == base_doc else ours_doc
    (br, bc), (orr, oc), (tr, tc) = _split(base_doc), _split(ours_doc), _split(theirs_doc)
    records = merge_records(br, orr, tr)
    if oc is None and tc is None:
        return records
    top = _merge_fields(bc or {}, oc or {}, tc or {})
    return {**top, "engrams": records}


def _strip_local(doc):
    """PLUR's personal-remote push rule: `scope: local` never leaves the machine."""
    if isinstance(doc, list):
        return [e for e in doc if not (isinstance(e, dict) and e.get("scope") == "local")]
    if isinstance(doc, dict) and isinstance(doc.get("engrams"), list):
        return {**doc, "engrams": _strip_local(doc["engrams"])}
    return doc


def _union_lines(*texts: bytes | None) -> bytes:
    seen, out = set(), []
    for t in texts:
        for line in (t or b"").decode().splitlines():
            if line not in seen:
                seen.add(line)
                out.append(line)
    return ("\n".join(out) + "\n").encode()


# ── PLUR's lock protocol (host:pid:ms:n in <file>.lock, created O_EXCL) ──────
def _holder_alive(token: str):
    parts = token.split(":")
    if len(parts) < 2 or parts[0] != socket.gethostname():
        return None
    try:
        pid = int(parts[1])
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ValueError, ProcessLookupError, OSError):
        return False


class PlurLock:
    def __init__(self, target: Path):
        self.path = Path(str(target) + ".lock")
        self.token = f"{socket.gethostname()}:{os.getpid()}:{int(time.time() * 1000)}:0"

    def __enter__(self):
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                with os.fdopen(fd, "w") as f:
                    f.write(self.token)
                return self
            except FileExistsError:
                try:
                    holder = self.path.read_text().strip()
                    age = time.time() - self.path.stat().st_mtime
                except FileNotFoundError:
                    continue
                alive = _holder_alive(holder)
                if alive is False or (alive is None and age > LOCK_STALE_S):
                    try:
                        if self.path.read_text().strip() == holder:
                            self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise SyncError(f"PLUR store lock {self.path.name} held by a live writer "
                                    f"for {LOCK_WAIT_S:.0f}s; will retry next run")
                time.sleep(min(0.2, LOCK_WAIT_S / 10))

    def __exit__(self, *exc):
        try:
            if self.path.read_text().strip() == self.token:
                self.path.unlink()
        except FileNotFoundError:
            pass


def _atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".sync.tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        except FileNotFoundError:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ── one converge attempt ──────────────────────────────────────────────────────
def _remote_type(store: Path) -> str:
    cfg = store / "config.yaml"
    if not cfg.is_file():
        return "personal"
    try:
        data = yaml.safe_load(cfg.read_text()) or {}
    except yaml.YAMLError:
        raise SyncError("config.yaml is unreadable; cannot tell whether the remote is shared")
    sync = data.get("sync") if isinstance(data, dict) else None
    return (sync or {}).get("remote_type", "personal") if isinstance(sync, dict) else "personal"


def _attempt(store: Path, branch: str, state: dict) -> None:
    upstream = f"origin/{branch}"
    head = _rev(store, "HEAD")
    theirs = _rev(store, upstream)
    if not theirs:
        raise SyncError(f"{upstream} does not exist")
    base = _git(store, "merge-base", head, theirs, check=False).decode().strip() or None
    if not base:
        raise SyncError("this store and the remote share no history")
    state["pulled"] = int(_git(store, "rev-list", "--count", f"{head}..{theirs}").decode().strip())

    B, H, T = _tree_blobs(store, base), _tree_blobs(store, head), _tree_blobs(store, theirs)
    paths = sorted(set(B) | set(H) | set(T) | _worktree_sync_paths(store))
    read_work: dict[str, bytes | None] = {}     # what the working tree held when read
    result: dict[str, str | None] = {}          # path -> blob sha in the commit
    work_out: dict[str, bytes | None] = {}      # path -> new working-tree content

    # Ids minted on both sides for different engrams: this side's are renamed
    # before anything merges, and every reference on this side follows them.
    renames: dict[str, str] = {}
    if "engrams.yaml" in paths:
        w = _read_work(store, "engrams.yaml")
        renames = find_collisions(_load(_blob(store, B.get("engrams.yaml"))), _load(w),
                                  _load(_blob(store, T.get("engrams.yaml"))), _host_tag())
    state["renamed"] = len(renames)

    for p in paths:
        b, h, t = B.get(p), H.get(p), T.get(p)
        if _is_sync_path(p):
            w = _read_work(store, p)
            read_work[p] = w
            o = None if w is None else _hash(store, w)
        else:
            o = h
        if _is_record_file(p):
            touched = bool(renames) and p in RECORD_ROOT_FILES
            if o == t and not touched:
                result[p] = t
                continue
            ours_doc, theirs_doc = _load(read_work[p]), _load(_blob(store, t))
            ours_in = _rename_refs(ours_doc, renames) if touched else ours_doc
            merged = merge_record_file(_load(_blob(store, b)), ours_in, theirs_doc)
            commit_doc = _strip_local(merged) if p == "engrams.yaml" else merged
            if merged is None:
                result[p], work_out[p] = None, None
                continue
            if commit_doc == theirs_doc:
                result[p] = t
            elif commit_doc == ours_doc and read_work[p] is not None:
                # Only this machine changed it: commit its bytes, not a re-dump,
                # so the next `plur sync` finds nothing to reformat.
                result[p] = _hash(store, read_work[p], write=True)
            elif h and commit_doc == _load(_blob(store, h)):
                result[p] = h
            else:
                result[p] = _hash(store, _dump(commit_doc), write=True)
            if merged != ours_doc:
                work_out[p] = _dump(merged)
            continue
        if o == t or o == b:
            result[p] = t
        elif t == b:
            result[p] = o
        elif p == ".gitignore":
            result[p] = _hash(store, _union_lines(_blob(store, t), read_work.get(p)), write=True)
        else:
            result[p] = t  # both changed a non-record file: the remote wins
        if o is not None and _is_sync_path(p) and result[p] == o and read_work.get(p) is not None:
            _hash(store, read_work[p], write=True)  # a working-tree blob the commit uses
        if _is_sync_path(p) and result[p] != o:
            work_out[p] = _blob(store, result[p])

    # The commit: theirs' tree with this machine's side applied.
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(tmp, "index")}
        _git(store, "read-tree", theirs, env=env)
        for p, sha in result.items():
            if sha == T.get(p):
                continue
            if sha is None:
                _git(store, "update-index", "--force-remove", "--", p, env=env)
            else:
                _git(store, "update-index", "--add", "--cacheinfo", f"100644,{sha},{p}", env=env)
        tree = _git(store, "write-tree", env=env).decode().strip()

    head_tree = _rev(store, f"{head}^{{tree}}")
    theirs_tree = _rev(store, f"{theirs}^{{tree}}")
    if tree == theirs_tree and _is_ancestor(store, head, theirs):
        new = theirs
    elif tree == head_tree and _is_ancestor(store, theirs, head):
        new = head
    else:
        parents = [head] if _is_ancestor(store, theirs, head) else (
            [theirs] if _is_ancestor(store, head, theirs) else [head, theirs])
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        msg = f"plur sync {stamp} (record merge on {socket.gethostname()})"
        args = ["commit-tree", tree]
        for par in parents:
            args += ["-p", par]
        new = _git(store, *args, "-m", msg).decode().strip()
    state["merged"] = new not in (head, theirs)

    # Apply under PLUR's lock, only if nobody wrote the files since we read them.
    with PlurLock(store / "engrams.yaml"):
        for p, before in read_work.items():
            if _read_work(store, p) != before:
                raise _Changed(p)
        old_blobs = H
        if new != head:
            _git(store, "update-ref", f"refs/heads/{branch}", new, head)
            _git(store, "read-tree", "HEAD")
        for p, content in work_out.items():
            if content is None:
                if (store / p).exists():
                    (store / p).unlink()
            else:
                _atomic_write(store / p, content)
        if new != head:  # files PLUR does not sync: follow the commit when untouched here
            N = _tree_blobs(store, new)
            for p in set(old_blobs) | set(N):
                if _is_sync_path(p) or old_blobs.get(p) == N.get(p):
                    continue
                w = _read_work(store, p)
                if (w is None and p not in old_blobs) or (w is not None and old_blobs.get(p) == _hash(store, w)):
                    if N.get(p) is None:
                        (store / p).unlink(missing_ok=True)
                    else:
                        _atomic_write(store / p, _blob(store, N[p]))

    if new != theirs and not _is_ancestor(store, new, theirs):
        r = subprocess.run(["git", "-C", str(store), "push", "origin", f"HEAD:refs/heads/{branch}"],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT)
        if r.returncode != 0:
            raise SyncError(f"push rejected: {r.stderr.strip()[:200]}")
        state["pushed"] = True


class _Changed(Exception):
    pass


def _reindex(store: Path, cli: str, state: dict) -> None:
    env = dict(os.environ)
    if os.sep in cli:  # an nvm plur needs its own node next to it
        env["PATH"] = str(Path(cli).parent) + os.pathsep + env.get("PATH", "")
    r = subprocess.run([cli, "--path", str(store), "--json", "sync"], capture_output=True,
                       text=True, timeout=900, env=env)
    if r.returncode != 0:
        state["index"] = "failed"
        raise SyncError(f"plur sync (reindex) failed rc={r.returncode}: {r.stderr.strip()[-200:]}")
    try:
        state["index"] = "ok: " + str(json.loads(r.stdout).get("action"))
    except (ValueError, AttributeError):
        state["index"] = "ok"


def converge(store: Path, *, index: bool, cli: str, state: dict) -> None:
    if not (store / ".git").is_dir():
        raise SyncError(f"{store} is not a git-backed PLUR store")
    if _remote_type(store) == "shared":
        raise SyncError("sync.remote_type is shared; this job only merges a personal remote")
    if not _rev(store, "origin/HEAD") and not _git(store, "remote", check=False).strip():
        raise SyncError("the store has no origin remote")
    for marker in ("MERGE_HEAD", "rebase-merge", "rebase-apply"):
        if (store / ".git" / marker).exists():
            raise SyncError(f"a git {marker} is in progress in the store; resolve it first")
    branch = _git(store, "symbolic-ref", "--short", "HEAD").decode().strip()
    state["branch"] = branch
    for attempt in range(ATTEMPTS):
        _git(store, "fetch", "--quiet", "origin")
        try:
            _attempt(store, branch, state)
            break
        except _Changed as c:
            state["retries"] = attempt + 1
            if attempt == ATTEMPTS - 1:
                raise SyncError(f"{c} kept changing while merging; will retry next run")
        except SyncError as e:
            if "push rejected" in str(e) and attempt < ATTEMPTS - 1:
                state["retries"] = attempt + 1
                continue
            raise
    if index:
        _reindex(store, cli, state)


def _count(store: Path, state: dict) -> None:
    try:
        doc = _load(_read_work(store, "engrams.yaml"))
        recs, _ = _split(doc)
        state["engrams"] = len(recs or [])
        state["local_only"] = sum(1 for e in recs or [] if isinstance(e, dict) and e.get("scope") == "local")
    except Exception:  # counting is reporting, never a reason to fail the sync
        state["engrams"] = None


def _default_cli() -> str:
    """The job envelope's PATH has no nvm; the mac's plur lives there."""
    import shutil
    if os.environ.get("DATACORE_PLUR_CLI"):
        return os.environ["DATACORE_PLUR_CLI"]
    found = shutil.which("plur") or next(
        (c for c in ("/usr/local/bin/plur", "/opt/homebrew/bin/plur") if os.access(c, os.X_OK)), None)
    if found:
        return found
    nvm = sorted(Path.home().glob(".nvm/versions/node/*/bin/plur"),
                 key=lambda f: [int(x) if x.isdigit() else 0 for x in f.parts[-3].lstrip("v").split(".")])
    return str(nvm[-1]) if nvm else "plur"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", default=str(Path.home() / ".plur"))
    ap.add_argument("--state", default=str(Path.home() / ".datacore" / "state" / "plur-sync.json"))
    ap.add_argument("--plur-cli", default=None)
    ap.add_argument("--no-index", action="store_true", help="skip the `plur sync` reindex step")
    ap.add_argument("--host-tag", help="suffix for this side's renamed ids (default: the host name); "
                    "for merging another machine's store copy on its behalf")
    a = ap.parse_args(argv)
    global HOST_TAG
    HOST_TAG = "".join(c for c in (a.host_tag or "").lower() if c.isalnum()) or None
    store = Path(a.store).expanduser()
    t0 = time.monotonic()
    state = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "host": socket.gethostname(),
             "store": str(store), "branch": None, "head": None, "remote_head": None, "in_sync": False,
             "merged": False, "pulled": 0, "pushed": False, "retries": 0, "renamed": 0, "engrams": None,
             "local_only": None, "index": "skipped", "error": None}
    try:
        converge(store, index=not a.no_index, cli=a.plur_cli or _default_cli(), state=state)
    except (SyncError, subprocess.TimeoutExpired, OSError, yaml.YAMLError) as e:
        state["error"] = f"{type(e).__name__}: {e}" if not isinstance(e, SyncError) else str(e)
    if (store / ".git").is_dir() and state.get("branch"):
        state["head"] = _rev(store, "HEAD")
        state["remote_head"] = _rev(store, f"origin/{state['branch']}")
        state["in_sync"] = bool(state["head"]) and state["head"] == state["remote_head"] and not state["error"]
    _count(store, state)
    state["duration_s"] = round(time.monotonic() - t0, 1)
    out = Path(a.state).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(out, (json.dumps(state, indent=2) + "\n").encode())
    print(f"{state['ts']} plur-store-sync host={state['host']} in_sync={state['in_sync']} "
          f"head={(state['head'] or '-')[:8]} pulled={state['pulled']} pushed={state['pushed']} "
          f"merged={state['merged']} index={state['index']} error={state['error'] or '-'}")
    return 0 if state["in_sync"] else 1


if __name__ == "__main__":
    sys.exit(main())
