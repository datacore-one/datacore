#!/usr/bin/env python3
"""install_doctor.py — what this installation is missing, and how to fix each (INS-4).

`datacore doctor` (the CLI) checks dependencies; this checks the installation
itself: the gaps a new team really has after following INSTALL.md, which no
dependency check can see.

    identity      which actor this machine writes as (DIP-0044)
    principals    the registry that binds writers to principals
    ledger        every space carries an event log (.datacore/events)
    inbox         the personal inbox the GTD tools write to
    jobs          the job manifest names only machines this install declares

Every item is {name, ok, detail, fix}: ok is True, False, or None (could not
tell), and every item that is not ok carries a `fix` -- a command or a one-line
instruction. A doctor that names a problem without saying what to do about it
has not finished its job.

Usage:
    install_doctor.py [--root DIR] [--json]
Exit 0 when nothing is False, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))


def _item(name: str, ok: bool | None, detail: str, fix: str = "") -> dict:
    out = {"name": name, "ok": ok, "detail": detail}
    if ok is not True:
        out["fix"] = fix or "see INSTALL.md"
    return out


def _infra(root: Path) -> Path:
    return root / ".datacore" / "registry" / "infrastructure.yaml"


def check_identity(root: Path) -> dict:
    try:
        import actor_identity as ai
        actor, source = ai.resolve(infra=_infra(root))
    except Exception as exc:  # noqa: BLE001 - a broken helper is its own finding
        return _item("identity", None, f"could not resolve the actor: {type(exc).__name__}",
                     "python3 .datacore/lib/actor_identity.py  (and fix the error it prints)")
    if actor:
        return _item("identity", True, f"writes as '{actor}' (from {source})")
    return _item("identity", False,
                 "no actor declared for this machine: every ledger write is refused",
                 "mkdir -p ~/.datacore && echo 'DATACORE_ACTOR=<your-name>' >> ~/.datacore/identity.env")


def check_principals(root: Path) -> dict:
    reg = root / ".datacore" / "registry" / "principals.yaml"
    fix = ("cp .datacore/registry/principals.yaml.example .datacore/registry/principals.yaml"
           " and add this machine's actor under principals:")
    if not reg.is_file():
        return _item("principals", False, "no principal registry (.datacore/registry/principals.yaml)", fix)
    try:
        import actor_identity as ai
        entries = ai.principals(reg)
        actor, _ = ai.resolve(infra=_infra(root))
    except ValueError as exc:
        return _item("principals", False, f"principal registry unusable: {exc}", fix)
    except Exception as exc:  # noqa: BLE001
        return _item("principals", None, f"could not read the principal registry: {type(exc).__name__}", fix)
    if not entries:
        return _item("principals", False, "principal registry declares no principal", fix)
    if actor and ai.principal_of(actor, reg)[0] is None:
        return _item("principals", False, f"actor '{actor}' belongs to no principal", fix)
    return _item("principals", True, f"{len(entries)} principal(s)")


def _spaces(root: Path) -> list[Path]:
    try:
        from spaces import discover_spaces
        # Personal and team spaces keep a ledger; a client space nested in a
        # team space is recorded by that team's log.
        return [s.path for s in discover_spaces(root)
                if s.path != root and getattr(s, "type", None) in (None, "", "personal", "team")]
    except Exception:  # noqa: BLE001 - fall back to the numbered-directory convention
        return sorted(p for p in root.glob("[0-9]-*") if p.is_dir())


def check_ledger(root: Path) -> dict:
    spaces = _spaces(root)
    if not spaces:
        return _item("ledger", False, "no space: not even 0-personal exists, so there is no event log",
                     "mkdir -p 0-personal/org 0-personal/.datacore/events")
    missing = [s for s in spaces if not (s / ".datacore" / "events").is_dir()]
    if missing:
        rel = " ".join(f"{s.name}/.datacore/events" for s in missing)
        return _item("ledger", False,
                     f"no event log in {', '.join(s.name for s in missing)}: tasks there never reach the history",
                     f"mkdir -p {rel}")
    return _item("ledger", True, f"{len(spaces)} space(s) carry an event log")


def check_inbox(root: Path) -> dict:
    inbox = root / "0-personal" / "org" / "inbox.org"
    if inbox.is_file():
        return _item("personal inbox", True, "0-personal/org/inbox.org")
    return _item("personal inbox", False, "no personal inbox (0-personal/org/inbox.org): tasks cannot be captured",
                 "mkdir -p 0-personal/org && cp .datacore/templates/org/inbox.org.example 0-personal/org/inbox.org")


def check_jobs(root: Path) -> dict:
    manifest = root / ".datacore" / "lib" / "jobs" / "manifest.yaml"
    if not manifest.is_file():
        return _item("jobs", True, "no job manifest: nothing scheduled to verify")
    try:
        import yaml
        jobs = (yaml.safe_load(manifest.read_text()) or {}).get("jobs") or []
        machines = sorted({str(j.get("machine")) for j in jobs if isinstance(j, dict) and j.get("machine")})
        infra = _infra(root)
        servers = set(((yaml.safe_load(infra.read_text()) or {}).get("servers") or {}).keys()) \
            if infra.is_file() else set()
    except Exception as exc:  # noqa: BLE001
        return _item("jobs", None, f"could not read the job manifest or roster: {type(exc).__name__}",
                     "python3 .datacore/lib/job_verify.py --help  (the manifest must parse)")
    foreign = [m for m in machines if m not in servers]
    if foreign:
        return _item("jobs", False,
                     f"the job manifest schedules jobs on machine(s) this install does not declare: "
                     f"{', '.join(foreign)} -- they can never run or be verified here",
                     "declare each machine under servers: in .datacore/registry/infrastructure.yaml "
                     "(cp .datacore/registry/infrastructure.yaml.example ...), or keep only this "
                     "install's jobs in .datacore/lib/jobs/manifest.yaml")
    return _item("jobs", True, f"{len(jobs)} job(s) on {len(machines)} declared machine(s)")


CHECKS = (check_identity, check_principals, check_ledger, check_inbox, check_jobs)


def run(root: Path) -> list[dict]:
    return [c(root) for c in CHECKS]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    items = run(Path(a.root))
    if a.json:
        print(json.dumps({"install": items}, indent=2))
    else:
        for it in items:
            mark = {True: "ok  ", False: "FAIL", None: "n-a "}[it["ok"]]
            print(f"  {mark} {it['name']:15} {it['detail']}")
            if it["ok"] is not True:
                print(f"       fix: {it['fix']}")
    return 1 if any(it["ok"] is False for it in items) else 0


if __name__ == "__main__":
    sys.exit(main())
