#!/usr/bin/env python3
"""Move credential-index entries to a project scope, and optionally set hosts.

--project NAME  sets `scope: project` and replaces the entry's `space:` line
                with `project: NAME` (the values must already sit in
                secrets/projects/NAME.env).
--hosts A B     sets `hosts:`. Careful: hosts RESTRICTS the credential to the
                named instances (names from secrets/instances/*.yaml plus
                `local`, never ssh aliases); every instance not listed drops it,
                this Mac included unless `local` is named.

Edits lines in place so comments and layout survive; never reads a value
(the index holds metadata only). Dry run by default: prints the diff.

    python3 .datacore/lib/creds_index_scope.py --project datafund-x \
        datafund-x-api-secret datafund-x-access-token datafund-x-access-token-secret
    ... --apply        write it
"""
import argparse
import difflib
import sys
from pathlib import Path

SECRETS = Path(__file__).resolve().parent.parent / "secrets"
INDEX = SECRETS / "credential-index.yaml"


def known_instances() -> set[str]:
    names = {"local"}
    for f in (SECRETS / "instances").glob("*.yaml"):
        for line in f.read_text().splitlines():
            if line.startswith("name:"):
                names.add(line.split(":", 1)[1].split("#")[0].strip())
    return names


def edit_entry(lines: list[str], cred_id: str, project: str | None, hosts: list[str] | None) -> list[str]:
    start = next((i for i, l in enumerate(lines) if l.rstrip() == f"- id: {cred_id}"), None)
    if start is None:
        raise SystemExit(f"no entry '- id: {cred_id}' in {INDEX}")
    end = next((i for i in range(start + 1, len(lines)) if not lines[i].startswith("  ")), len(lines))
    out, skipping = [lines[start]], False
    for l in lines[start + 1:end]:
        key = l.strip().split(":", 1)[0]
        if hosts is not None and l.rstrip() == "  hosts:":
            skipping = True
            continue
        if skipping and l.startswith("  - "):
            continue
        skipping = False
        if project and l.startswith("  ") and not l.startswith("   ") and key in ("scope", "space", "project"):
            if key == "scope":
                out.append("  scope: project")
            continue  # space/project line re-added once below
        out.append(l)
    if project:
        out.append(f"  project: {project}")
    if hosts is not None:
        out += ["  hosts:"] + [f"  - {h}" for h in hosts]
    return lines[:start] + out + lines[end:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ids", nargs="+", help="credential ids to change")
    ap.add_argument("--project", help="move the entries to this project scope")
    ap.add_argument("--hosts", nargs="+", help="instance names (restricts!)")
    ap.add_argument("--apply", action="store_true", help="write the change")
    a = ap.parse_args()
    if not a.project and not a.hosts:
        ap.error("give --project and/or --hosts")
    if a.project and not (SECRETS / "projects" / f"{a.project}.env").exists():
        print(f"secrets/projects/{a.project}.env does not exist", file=sys.stderr)
        return 2
    if a.hosts:
        unknown = set(a.hosts) - known_instances()
        if unknown:
            print(f"unknown instance(s): {', '.join(sorted(unknown))} "
                  f"(known: {', '.join(sorted(known_instances()))})", file=sys.stderr)
            return 2

    old = INDEX.read_text().splitlines()
    new = old
    for cred_id in a.ids:
        new = edit_entry(new, cred_id, a.project, a.hosts)
    diff = list(difflib.unified_diff(old, new, "credential-index.yaml", "credential-index.yaml", lineterm=""))
    print("\n".join(diff) if diff else "no change")
    if a.apply and diff:
        INDEX.write_text("\n".join(new) + "\n")
        print(f"written: {INDEX}")
    elif diff:
        print("dry run — rerun with --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
