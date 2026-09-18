#!/usr/bin/env python3
"""Step 4 of 5: can this host, from this directory, find what it needs?

Three separate path defects on 2026-09-18, each of which produced SILENT WRONG
BEHAVIOUR rather than an error:

  registry   `principals.yaml` is a gitignored private overlay, so a satellite
             running from `~/.datacore/v2-runner` resolved no principals at all
             and refused every claim as "unregistered writer 'data'" -- about a
             writer that IS declared, two directories away.
  policy     `approvals_policy.yaml` is tracked and lives in the code checkout,
             which on that same host is a DIFFERENT tree from the data root. No
             single DATACORE_ROOT gave a working configuration: one way the
             delegation allowlist was silently inactive, the other every writer
             was unregistered.
  manifest   `jobs/manifest.yaml` resolved against the data root rather than the
             installation, so every unattended `job_verify` on the satellites
             died with "cannot read manifest".

Each was found by tripping over it. Each would have been found in one pass by
asking, on every host: from the directory your cron actually runs in, which of
these can you resolve, and to what?

This is the answer as a check. It resolves, it does not repair -- and it prints
the PATH it resolved to, because "found it" and "found the right one" are
different answers and only the second is useful.

    config_resolution_probe.py [--json]

Exit 0 when every required input resolves, 1 when one does not.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))


def probe() -> list[dict]:
    """One row per thing this host must be able to find."""
    rows: list[dict] = []

    def row(name, path, ok, detail=""):
        rows.append({"input": name, "path": str(path) if path else None,
                     "ok": bool(ok), "detail": detail})

    # -- identity -------------------------------------------------------
    try:
        import actor_identity
        actor, source = actor_identity.resolve()
        row("actor", None, bool(actor), f"{actor or 'UNDECLARED'} (from {source})")
        reg = actor_identity.PRINCIPALS
        try:
            declared = actor_identity.principals()
            row("registry", reg, bool(declared),
                f"{len(declared)} principal(s)" if declared
                else "no principals declared — the identity gate cannot answer")
        except ValueError as exc:
            row("registry", reg, False, f"unreadable: {exc}")
        # The question that actually matters: is THIS host's own writer known?
        if actor:
            principal, _ = actor_identity.principal_of(actor)
            row("actor-is-declared", reg, principal is not None,
                f"{actor} -> {principal}" if principal
                else f"{actor} belongs to no declared principal")
    except Exception as exc:  # noqa: BLE001
        row("actor", None, False, f"{type(exc).__name__}: {exc}")

    # -- policy ---------------------------------------------------------
    try:
        from ledger.policy import load_policy, DEFAULT_POLICY_PATH
        policy = load_policy()
        has_principals = getattr(policy, "principals", None) is not None
        used = DEFAULT_POLICY_PATH if Path(DEFAULT_POLICY_PATH).exists() else (
            LIB.parent / "config" / "approvals_policy.yaml")
        row("policy", used, has_principals,
            f"approver={policy.approver}, "
            + ("principals declared — stage 4 active" if has_principals
               else "NO principals — the delegation allowlist is inactive"))
    except Exception as exc:  # noqa: BLE001
        row("policy", None, False, f"{type(exc).__name__}: {exc}")

    # -- job manifest ---------------------------------------------------
    try:
        import job_verify
        from jobs.manifest import load_manifest
        path = job_verify._default_manifest_path()
        manifest = load_manifest(str(path))
        jobs = getattr(manifest, "jobs", manifest)      # loader returns a list
        machine = os.environ.get("DATACORE_ACTOR") or socket.gethostname().split(".")[0].lower()
        mine = [j for j in jobs if getattr(j, "machine", None) == machine]
        row("manifest", path, path.exists(),
            f"{len(jobs)} job(s); {len(mine)} for '{machine}'"
            + ("" if mine else " — this host is named differently in the manifest"))
    except Exception as exc:  # noqa: BLE001
        row("manifest", None, False, f"{type(exc).__name__}: {exc}")

    # -- data root ------------------------------------------------------
    root = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
    spaces = sorted(p.name for p in root.glob("[0-9]-*") if (p / ".datacore" / "events").is_dir())
    row("data-root", root, bool(spaces),
        f"{len(spaces)} space(s) with an event log" if spaces
        else "no space with an event log under this root")

    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    rows = probe()
    bad = [r for r in rows if not r["ok"]]
    if a.json:
        print(json.dumps({"ok": not bad, "cwd": os.getcwd(), "rows": rows}, indent=2))
    else:
        print(f"config resolution from {os.getcwd()}")
        width = max(len(r["input"]) for r in rows)
        for r in rows:
            print(f"  {'ok  ' if r['ok'] else 'FAIL'} {r['input']:<{width}}  {r['detail']}")
            if r["path"]:
                print(f"       {r['path']}")
        print(f"\nconfig-resolution: {'OK' if not bad else 'BROKEN'} — "
              f"{len(rows) - len(bad)}/{len(rows)} resolved")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
