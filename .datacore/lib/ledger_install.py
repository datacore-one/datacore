"""Install a ledger space for a team, and say what is missing (profile A).

Profile A (ledger upgrade PLAN.md, Phase 3; audit C1, C4, C18): one host or
one shared git remote, humans only -- the ledger, verify and the CLI. No
agents, no org projection, no fleet. Three operations, all reached through
`ledger_cli.py`:

  init        declare this machine's writer, register them as a principal and
              make the space ledger-ready, so it appends, verifies and folds
              with no file edited by hand;
  principals  add a person to principals.yaml (the generator; init uses it);
  doctor      name every missing prerequisite, each with its fix, and exit
              non-zero when anything is missing.

Nothing here guesses. The writer is whoever `--actor` names; the hostname is
never a default (owner decisions L10/Q2, DIP-0044). An identity already declared
on this machine is never overwritten. No key is generated: signing is Phase 6
(owner decision 7, 2026-10-04), and while it is off a missing key is not a gap.

This module imports only the standard library at load time, so `doctor` runs
and answers on a machine where PyYAML or cryptography is not installed yet --
the case it exists to report.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

LIB = Path(__file__).resolve().parent
CLI = "python3 .datacore/lib/ledger_cli.py"
#: Datacore's git hooks; their pre-commit and pre-push run the ledger write gate (LED-3).
GITHOOKS = LIB.parent / "githooks"
_HOOKS_KEY = "core." + "hooksPath"

#: Packages the ledger imports, by import name -> install name.
PACKAGES = {"yaml": "PyYAML", "cryptography": "cryptography"}
#: The placeholder INSTALL.md writes into identity.env; not a declared person.
PLACEHOLDERS = {"your-name"}
_ACTOR = re.compile(r"[a-z0-9][a-z0-9_-]*")


@dataclass
class Check:
    name: str
    ok: bool | None          # True ok, False missing, None note (not a gap)
    detail: str
    fix: str = ""

    def line(self) -> str:
        if self.ok is True:
            return f"ok       {self.name}: {self.detail}"
        if self.ok is None:
            return f"note     {self.name}: {self.detail}" + (f" -- to add it: {self.fix}" if self.fix else "")
        return f"MISSING  {self.name}: {self.detail} -- fix: {self.fix}"


class InstallRefused(Exception):
    """init/principals refused; the message says why and what to do."""


def _identity():
    import actor_identity
    return actor_identity


def missing_packages() -> list[str]:
    return [mod for mod in PACKAGES if importlib.util.find_spec(mod) is None]


def registry_dir() -> Path:
    """Where principals.yaml is read from (the resolver's own answer)."""
    return _identity()._registry_dir()


def principals_path() -> Path:
    return registry_dir() / "principals.yaml"


def identity_file() -> Path:
    return Path(os.environ.get("DATACORE_IDENTITY_FILE",
                               str(Path.home() / ".datacore" / "identity.env")))


def declared_actor() -> tuple[str | None, str]:
    """(actor, where) as the ledger writer resolves it; placeholders are not an actor."""
    ai = _identity()
    actor, source = ai.resolve(identity_file=identity_file())
    if actor in PLACEHOLDERS:
        return None, f"placeholder {actor!r} in {identity_file()}"
    where = {"env": "DATACORE_ACTOR in the environment", "identity.env": str(identity_file()),
             "registry": "the infrastructure registry"}.get(source, source)
    return actor, where


def signing_on() -> bool:
    value = os.environ.get("DATACORE_LEDGER_SIGN")
    if value is None:
        value = _identity()._parse_env_file(identity_file()).get("DATACORE_LEDGER_SIGN")
    return (value or "").strip() == "1"


def validate_actor(actor: str) -> str:
    actor = (actor or "").strip()
    if not _ACTOR.fullmatch(actor) or actor in PLACEHOLDERS:
        raise InstallRefused(f"invalid writer name {actor!r}: use a person's name in lowercase letters, "
                             "digits, '-' or '_' (it becomes the log file <name>.jsonl)")
    return actor


# ---------------------------------------------------------------- identity

def declare_identity(actor: str) -> str:
    """Write DATACORE_ACTOR=<actor> to this machine's identity file.

    Never overwrites a declared identity: the same actor is a no-op, another
    one is refused. INSTALL.md's placeholder line is replaced.
    """
    env = (os.environ.get("DATACORE_ACTOR") or "").strip().lower()
    if env and env != actor:
        raise InstallRefused(f"this shell declares DATACORE_ACTOR={env}; this machine's identity is not "
                             f"{actor!r}. Unset it, or run init as {env}")
    path = identity_file()
    held = _identity()._parse_env_file(path).get("DATACORE_ACTOR", "").strip().lower()
    if held == actor:
        return f"identity: {path} already declares {actor}"
    if held and held not in PLACEHOLDERS:
        raise InstallRefused(f"this machine's identity is already declared as {held!r} in {path}; init "
                             f"never overwrites it. Each person runs init under their own login (their own "
                             f"HOME); to change this machine's writer, edit that file yourself")
    from file_utils import atomic_write_text
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    kept = [l for l in lines if not re.match(r"\s*(export\s+)?DATACORE_ACTOR\s*=", l)]
    atomic_write_text(path, "\n".join(kept + [f"DATACORE_ACTOR={actor}"]) + "\n")
    return f"identity: declared {actor} in {path}"


# ---------------------------------------------------------------- principals

_HEADER = """\
# Principals: who writes to this installation's ledger (DIP-0044).
# Written by `ledger_cli.py principals add` / `init`. Private to this
# installation: keep it out of any public repository.
#
# email_sha256 binds a git author email to a writer without writing the address
# down: the first 16 hex of sha256(lowercased email). Add one with
#   ledger_cli.py principals add --actor <name> --email <address>
"""


def _entry(actor: str, kind: str, email_hash: str | None) -> str:
    emails = f"[{email_hash}]" if email_hash else "[]"
    return (f"  {actor}:\n"
            f"    kind: {kind}\n"
            f"    display: {actor}\n"
            f"    email_sha256: {emails}\n"
            f"    hosts: []\n"
            f"    writes_as: [{actor}]\n"
            f"    permission_mode: {'human' if kind == 'human' else 'propose'}\n")


def add_principal(actor: str, *, kind: str = "human", email: str | None = None) -> str:
    """Declare `actor` in principals.yaml, creating the file if there is none.

    Text is inserted, never re-dumped, so an existing file keeps its comments
    and layout; the result is re-read through the resolver and the old text
    restored if it does not validate.
    """
    actor = validate_actor(actor)
    if kind not in ("human", "agent"):
        raise InstallRefused(f"unknown principal kind {kind!r}: human or agent")
    ai = _identity()
    hashed = ai.email_hash(email) if email else None
    path = principals_path()
    from file_utils import atomic_write_text
    old = path.read_text(encoding="utf-8") if path.exists() else None
    current = ai.principals(path) if old is not None else {}
    owner = next((n for n, e in current.items()
                  if n == actor or actor in (e.get("writes_as") or [])), None)
    if owner is not None:
        have = list(current[owner].get("email_sha256") or [])
        if not hashed or hashed in have:
            return f"principals: {actor} is already declared ({path})"
        block = re.search(rf"(?m)^  {re.escape(owner)}:\n((?:    .*\n|\s*\n)*)", old)
        line = block and re.search(r"(?m)^    email_sha256:\s*\[([^\]\n]*)\]\s*$", block.group(1))
        if not line:
            raise InstallRefused(f"{owner} in {path} has no one-line email_sha256 list to extend; add "
                                 f"{hashed} to it by hand")
        start = block.start(1) + line.start()
        end = block.start(1) + line.end()
        items = [x.strip() for x in line.group(1).split(",") if x.strip()] + [hashed]
        new = old[:start] + f"    email_sha256: [{', '.join(items)}]" + old[end:]
        msg = f"principals: bound a new author email to {owner} ({path})"
    elif old is None:
        new = _HEADER + "version: 1\nprincipals:\n" + _entry(actor, kind, hashed)
        msg = f"principals: created {path} with {actor} ({kind})"
    else:
        m = re.search(r"(?m)^principals:[ \t]*\n", old)
        if not m:
            raise InstallRefused(f"{path} has no top-level 'principals:' block to add {actor} to")
        new = old[:m.end()] + _entry(actor, kind, hashed) + old[m.end():]
        msg = f"principals: added {actor} ({kind}) to {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, new)
    try:
        if actor not in {n for n, e in ai.principals(path).items()} | {
                w for e in ai.principals(path).values() for w in (e.get("writes_as") or [])}:
            raise ValueError("not declared after the write")
    except ValueError as exc:
        if old is None:
            path.unlink()
        else:
            atomic_write_text(path, old)
        raise InstallRefused(f"{path} would not validate after adding {actor} ({exc}); left unchanged") from None
    return msg


# ---------------------------------------------------------------- space

def _git(repo: Path, *args: str):
    import subprocess
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)


def _is_checkout(space: Path) -> bool:
    return (space / ".git").exists()


def _has_hooks(space: Path) -> bool:
    """Does this checkout already run a pre-commit hook (Datacore's or its own)?"""
    configured = _git(space, "config", _HOOKS_KEY).stdout.strip()
    if configured:
        base = Path(configured) if Path(configured).is_absolute() else space / configured
        if os.access(base / "pre-commit", os.X_OK):
            return True
    return (space / ".git" / "hooks" / "pre-commit").exists()


def _gate_fix(space: Path) -> str:
    return f"git -C {space} config {_HOOKS_KEY} {GITHOOKS}"


def prepare_space(space: Path) -> list[str]:
    """Make `space` ledger-ready. Adds what is missing; changes nothing present."""
    from file_utils import atomic_write_text
    done = []
    events = space / ".datacore" / "events"
    if not events.is_dir():
        events.mkdir(parents=True)
        done.append(f"space: created {events}")
    proto = space / ".datacore" / "ledger-edit-protocol"
    if not proto.exists():
        atomic_write_text(proto, "2\n")
        done.append("space: conditional edits enabled (.datacore/ledger-edit-protocol = 2)")
    marker = space / ".datacore" / "config.yaml"
    if not marker.exists():
        atomic_write_text(marker, f"space:\n  name: {space.resolve().name}\n  type: team\n")
        done.append("space: marked as a space (.datacore/config.yaml), so sync needs no registry entry")
    ignore = space / ".gitignore"
    held = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    if ".datacore/state/" not in [l.strip() for l in held]:
        atomic_write_text(ignore, "\n".join(held + ["# machine-local ledger state (sequence marks, index)",
                                                    ".datacore/state/"]) + "\n")
        done.append("space: .datacore/state/ kept out of git (machine-local)")
    # The write gate is the trust layer until signing (owner decision 7): a
    # shared checkout runs Datacore's hooks, unless it already runs its own.
    if _is_checkout(space) and not _has_hooks(space):
        if os.access(GITHOOKS / "pre-commit", os.X_OK):
            _git(space, "config", _HOOKS_KEY, str(GITHOOKS))
            done.append(f"space: commits and pushes now pass the ledger write gate ({GITHOOKS})")
        else:
            done.append(f"space: the ledger write gate could not be wired -- {GITHOOKS} is missing")
    return done or [f"space: {space} was already ledger-ready"]


def init(space: Path, actor: str, *, email: str | None = None) -> list[str]:
    actor = validate_actor(actor)
    gaps = missing_packages()
    if gaps:
        raise InstallRefused("install the ledger's packages first: pip install "
                             + " ".join(PACKAGES[g] for g in gaps) + f" (then run {CLI} doctor)")
    out = [declare_identity(actor), add_principal(actor, kind="human", email=email)]
    out += prepare_space(space)
    return out


# ---------------------------------------------------------------- doctor

def doctor(space: Path | None) -> list[Check]:
    checks: list[Check] = []
    v = sys.version_info
    checks.append(Check("python", v >= (3, 10), f"{v.major}.{v.minor}",
                        "install Python 3.10 or newer and run the ledger with it"))
    missing = missing_packages()
    for mod, pip in PACKAGES.items():
        checks.append(Check(f"package {pip}", mod not in missing,
                            "importable" if mod not in missing else f"the {pip} package is not installed for {sys.executable}",
                            f"{sys.executable} -m pip install {pip}"))

    actor, where = declared_actor()
    space_arg = str(space) if space else "<space>"
    checks.append(Check("identity", actor is not None,
                        f"this machine writes as {actor} ({where})" if actor else
                        f"no writer declared for this machine ({where if 'placeholder' in where else identity_file()} "
                        "has no DATACORE_ACTOR)",
                        f"{CLI} init --space {space_arg} --actor <your-name>"))

    who = actor or "<your-name>"
    path = principals_path()
    if not path.exists():
        checks.append(Check("principals", False, f"no principals registry at {path}",
                            f"{CLI} principals add --actor {who} --kind human"))
    elif "yaml" in missing:
        checks.append(Check("principals", False, f"{path} cannot be read without PyYAML",
                            f"{sys.executable} -m pip install PyYAML"))
    else:
        try:
            owner, entry = _identity().principal_of(actor, path) if actor else (None, {})
        except ValueError as exc:
            checks.append(Check("principals", False, f"{path} is invalid: {exc}",
                                f"correct {path} (or move it aside and run {CLI} principals add --actor {who})"))
        else:
            if actor and owner is None:
                checks.append(Check("principals", False, f"{actor} is not declared in {path}",
                                    f"{CLI} principals add --actor {actor} --kind human"))
            elif actor:
                checks.append(Check("principals", True, f"{actor} belongs to principal {owner} ({path})"))
                if not entry.get("email_sha256"):
                    checks.append(Check("author email", None,
                                        f"no git author email is bound to {owner}, so commit authorship of "
                                        f"{actor}.jsonl cannot be checked",
                                        f"{CLI} principals add --actor {actor} --email <your git email>"))
            else:
                checks.append(Check("principals", False, f"cannot check {path} without a declared writer",
                                    f"{CLI} init --space {space_arg} --actor <your-name>"))

    if space is not None:
        events = space / ".datacore" / "events"
        proto = space / ".datacore" / "ledger-edit-protocol"
        if not events.is_dir():
            checks.append(Check("space", False, f"{space} has no ledger yet (no .datacore/events)",
                                f"{CLI} init --space {space} --actor {who}"))
        elif not proto.exists() or proto.read_text().strip() != "2":
            checks.append(Check("space", False, f"{space}: conditional edits are not enabled for the ledger "
                                                "(.datacore/ledger-edit-protocol is not 2)",
                                f"{CLI} init --space {space} --actor {who}"))
        else:
            checks.append(Check("space", True, f"{space} has a ledger"))
        if _is_checkout(space):
            gated = _has_hooks(space)
            checks.append(Check("write gate", gated,
                                f"{space} runs a pre-commit hook" if gated else
                                f"commits in {space} skip the ledger write gate (no hooks configured)",
                                _gate_fix(space)))

    if signing_on():
        key = Path.home() / ".datacore" / "keys" / f"{who}.key"
        checks.append(Check("signing key", key.exists(),
                            f"signing is on and {who}'s key is at {key}" if key.exists() else
                            f"signing is on (DATACORE_LEDGER_SIGN=1) but {who} has no key at {key}",
                            f"if {who} never signed before, the first signed append creates the key; if {who} "
                            "did, the key was lost: follow 'Lost or rotated key' in .datacore/docs/ledger-team-install.md "
                            "(the owner approves a new key; never copy one from another machine)"))
    else:
        checks.append(Check("signing", True, "off (signing comes later, as one step; no key is needed now)"))
    return checks
