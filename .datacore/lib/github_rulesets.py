#!/usr/bin/env python3
"""Server-side protection for the GitHub half of the fleet (DIP-0046 D6).

D5 puts a `pre-receive` on the Gitea repos. The GitHub repos have no equivalent
— a hook cannot be installed on github.com — so their server-side layer is
rulesets, and without this task those repos had detection only: every check
lived on a client that an agent can reconfigure or a fresh clone never gets.

WHAT IS DELIBERATELY NOT ENFORCED: pull requests. Space repos are written
directly on `main` by nightshift, miles, data and winston — that is the design
(DIP-0011 claim-by-push), not an oversight, and requiring review would stop
every unattended actor. The operator has also said plainly that plur-space
should carry no protection.

What IS enforced is the pair of operations no legitimate actor performs and
both of which destroy history irrecoverably:

  non_fast_forward   no force-push to the default branch
  deletion           no deleting the default branch

Both are aimed at this installation's own history — destructive resets and
parked branches that stranded 610 commits, then 645 more. Verified before
applying: the only force-push anywhere in the tree targets
`refs/heads/ledger/data`, never a default branch, so nothing breaks.

Dry-run by default. `--apply` writes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

RULESET_NAME = "datacore-dip-0046"

# Space repos, by remote. Deliberately explicit rather than derived: this
# writes to github.com, and a glob that quietly grows is not what should
# decide which repositories get their settings changed.
TARGETS = [
    ("datafund/datafund-space", True),
    ("fairDataSociety/fds-space", True),
    ("datacore-one/datacore-space", True),
    ("plur9/the-firm-space", True),
    # Excluded by explicit operator instruction, not by omission.
    ("plur-ai/plur-space", False),
]


def gh(*args: str, body: dict | None = None) -> tuple[int, str]:
    cmd = ["gh", "api", *args]
    if body is not None:
        cmd += ["--input", "-"]
    r = subprocess.run(cmd, input=json.dumps(body) if body else None,
                       capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def existing(repo: str) -> tuple[int | None, str]:
    """(ruleset id or None, note). A 403 here is a plan limit, not a failure."""
    rc, out = gh(f"repos/{repo}/rulesets")
    if rc != 0:
        if "Upgrade to GitHub" in out:
            return None, "unavailable on this plan (private repo, no rulesets)"
        if "Not Found" in out:
            return None, "repo not found or no access"
        return None, out.strip()[:90]
    try:
        for r in json.loads(out):
            if r.get("name") == RULESET_NAME:
                return r.get("id"), "present"
    except ValueError:
        return None, "unparseable response"
    return None, "absent"


def payload() -> dict:
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        # No pull_request rule: unattended actors write main directly by design.
        "rules": [{"type": "non_fast_forward"}, {"type": "deletion"}],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    applied = skipped = blocked = 0
    for repo, wanted in TARGETS:
        if not wanted:
            print(f"  skip     {repo:<32} excluded by operator instruction")
            skipped += 1
            continue
        rid, note = existing(repo)
        if note.startswith("unavailable") or note.startswith("repo not found"):
            print(f"  BLOCKED  {repo:<32} {note}")
            blocked += 1
            continue
        if rid:
            print(f"  ok       {repo:<32} ruleset already present (id {rid})")
            continue
        if not a.apply:
            print(f"  would    {repo:<32} create '{RULESET_NAME}' "
                  "(block force-push + deletion on default branch)")
            continue
        rc, out = gh("--method", "POST", f"repos/{repo}/rulesets", body=payload())
        if rc == 0:
            print(f"  APPLIED  {repo:<32} force-push and deletion now blocked")
            applied += 1
        else:
            print(f"  FAILED   {repo:<32} {out.strip()[:90]}")
            blocked += 1

    print(f"\ngithub-rulesets: {applied} applied, {skipped} excluded, {blocked} blocked")
    # Blocked is not a pass: a repo we cannot protect is a repo whose
    # enforcement is client-side only, and that must stay visible.
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
