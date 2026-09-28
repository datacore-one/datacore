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
    cli:<name>      the command is on PATH
    fleet           this machine is part of a fleet: its infrastructure.yaml
                    roster exists, so hosts can be read over ssh
    agent           real agent sessions are allowed (DATACORE_AGENT_EVALS=1)
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECLARED = ROOT / ".datacore" / "config" / "test-needs.yaml"
KINDS = ("module", "file", "cli", "fleet", "agent")


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
    if k == "cli":
        return shutil.which(arg) is not None
    if k == "fleet":
        return (root / ".datacore" / "registry" / "infrastructure.yaml").exists()
    if k == "agent":
        return env.get("DATACORE_AGENT_EVALS") == "1"
    raise ValueError(f"unknown need {need!r}; one of {', '.join(KINDS)}")


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
