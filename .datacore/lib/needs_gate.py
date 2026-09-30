"""Which tests this checkout can run: the ones whose needs it has.

Some tests check an INSTALLATION rather than the code: the install's principal
registry, its fleet over ssh, a module repository that is not part of this one,
the PLUR CLI, a real agent session. A bare checkout -- a CI runner -- has none
of those, so those tests failed there on every pull request for reasons that
said nothing about the change, and a red that is always red is read by nobody.

`.datacore/config/test-needs.yaml` declares what such a test needs. A need is
checked, never assumed: on a machine that has it (a full install), the test is
collected exactly as before; only where the need is missing is it left out,
and every run says what it left out and why. Nothing here judges a test's
result, only whether this machine can run it.

PROMISE_EVALS_ALL=1 (the promise scoreboard) collects everything regardless:
the scoreboard is the authority and must see a missing need as a red.

Need vocabulary:
    module:<name>   .datacore/modules/<name> is installed (a separate repository)
    file:<path>     that file or directory exists; relative to the repository
                    root, or ~/... for this user's home (install-local state
                    that never ships in the repository)
    role:<role>/<path>  that path exists inside the space this install gives the
                    role (install.yaml `roles:`); the space's folder is the
                    install's own and never ships (INS-3)
    cli:<name>      the command is on PATH
    python:<module> an optional Python package is importable (one that the
                    audited requirements deliberately leave out)
    fleet           this machine reads the fleet over ssh: it is the machine the
                    roster (infrastructure.yaml) names as `roles.console`. Having
                    the roster is not enough -- the overnight host has it and
                    cannot ssh to the others (2026-09-30); a roster that names
                    no console declares no machine that reads the fleet
    agent           real agent sessions are allowed (DATACORE_AGENT_EVALS=1)
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECLARED = ROOT / ".datacore" / "config" / "test-needs.yaml"
KINDS = ("module", "file", "role", "cli", "python", "fleet", "agent")


def load(path: Path = DECLARED) -> list[dict]:
    """[{test, only: [names]?, needs: [..], why}] -- a missing file declares nothing."""
    if not path.exists():
        return []
    import yaml
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(doc.get("tests") or [])


def kind(need: str) -> str:
    return str(need).split(":", 1)[0]


def met(need: str, root: Path = ROOT, env=None) -> bool:
    env = os.environ if env is None else env
    k, _, arg = str(need).partition(":")
    if k == "module":
        return (root / ".datacore" / "modules" / arg).is_dir()
    if k == "file":
        p = Path(arg).expanduser() if arg.startswith("~") else root / arg
        return p.exists()
    if k == "role":
        import spaces
        role, _, sub = arg.partition("/")
        space = spaces.space_for(role, root=root)
        return bool(space) and (root / space / sub).exists()
    if k == "cli":
        return shutil.which(arg) is not None
    if k == "python":
        import importlib.util
        try:
            return importlib.util.find_spec(arg) is not None
        except (ImportError, ValueError):
            return False
    if k == "fleet":
        return is_console(root, env)
    if k == "agent":
        return env.get("DATACORE_AGENT_EVALS") == "1"
    raise ValueError(f"unknown need {need!r}; one of {', '.join(KINDS)}")


def is_console(root: Path = ROOT, env=None) -> bool:
    """Is this machine the one the roster names as the fleet's console?

    Declared, never probed: an ssh probe would turn a host that is really down
    into "could not run" on the one machine whose job is to notice it, and one
    host (box) answers a non-interactive ssh with an interactive check. This
    machine is known by its declared actor (DATACORE_ACTOR, else
    actor_identity: ~/.datacore/identity.env, then the roster's hostname row).
    """
    env = os.environ if env is None else env
    roster = root / ".datacore" / "registry" / "infrastructure.yaml"
    try:
        import yaml
        doc = yaml.safe_load(roster.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 -- no readable roster: no fleet to read
        return False
    roles = doc.get("roles") if isinstance(doc, dict) else None
    console = str((roles or {}).get("console") or "") if isinstance(roles, dict) else ""
    cfg = (doc.get("servers") or {}).get(console) if console else None
    if not isinstance(cfg, dict):
        return False
    actor = str(env.get("DATACORE_ACTOR") or "").strip().lower()
    if not actor:
        try:
            import actor_identity
            actor = str(actor_identity.resolve(infra=roster)[0] or "").lower()
        except Exception:  # noqa: BLE001 -- an unknown machine is not the console
            return False
    names = {console.lower(), str((cfg.get("access") or {}).get("actor") or "").lower(),
             *(str(a).lower() for a in cfg.get("ledger_actors") or [])}
    return bool(actor) and actor in names - {""}


def unmet_by_test(root: Path = ROOT, env=None, entries=None) -> dict[str, list[str]]:
    """{'<repo-relative file>[::<test name>]': [unmet needs]} for this machine.

    Empty under PROMISE_EVALS_ALL=1: the scoreboard collects everything.
    """
    env = os.environ if env is None else env
    if env.get("PROMISE_EVALS_ALL") == "1":
        return {}
    out: dict[str, list[str]] = {}
    for e in (load() if entries is None else entries):
        missing = [n for n in e.get("needs") or [] if not met(n, root, env)]
        if missing:
            for key in keys(e):
                out[key] = missing
    return out


def keys(entry: dict) -> list[str]:
    """An entry names a whole file, or (with `only:`) some tests in it."""
    only = entry.get("only") or []
    return [f"{entry['test']}::{name}" for name in only] if only else [str(entry["test"])]


def matches(nodeid: str, declared: str) -> bool:
    """A file entry covers every test in it; `file::name` covers that test and its parameters."""
    if "::" not in declared:
        return nodeid == declared or nodeid.startswith(declared + "::")
    return nodeid == declared or nodeid.startswith(declared + "[") or nodeid.startswith(declared + "::")
