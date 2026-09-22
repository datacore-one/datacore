#!/usr/bin/env python3
"""The done-condition for a delegated repair, with the boundary beside it.

A task that says "make job X pass verification" has an obvious cheap solution:
loosen X's regex, widen its exit_ok, raise its max_age_hours, or delete the job.
Every one of those makes the check pass and leaves the system worse than before,
and an agent optimising for a green check has no reason to prefer the expensive
fix. This is failure mode 3 of the loop-design gate in CLAUDE.md -- "gates only
on all tests pass, so the agent deletes the tests" -- and it is the reason a
done-criterion needs a boundary stated next to it rather than trusted.

So this asserts two things, and a repair is only done when BOTH hold:

  1. BOUNDARY  the job's contract is byte-for-byte what it was when the repair
               was delegated. Not "looks similar", not "still has a regex":
               the same bytes, compared by hash, recorded before the repairing
               agent was told anything.
  2. DONE      the job's artifact checks pass.

Checking 2 without 1 is not a weaker version of this check. It is a different
check, one that a repair can satisfy by editing the thing that judges it.

    fix_check.py --job mac-seq-gap --machine mac --contract-sha <sha256>

Exit 0 only if the contract is unchanged AND the job verifies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def contract_sha(job_name: str, manifest: Path) -> str | None:
    """A stable hash of one job's manifest entry.

    Hashed from the PARSED entry re-serialised canonically, not from the YAML
    text, so reformatting or a comment change is not a boundary violation while
    any change to a value is. The point is to catch the check being weakened,
    not to freeze the file.
    """
    import yaml

    try:
        data = yaml.safe_load(manifest.read_text()) or {}
    except Exception:  # noqa: BLE001 -- an unreadable manifest is not a hash
        return None
    for job in data.get("jobs", []):
        if job.get("name") == job_name:
            canon = json.dumps(job, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(canon.encode()).hexdigest()
    return None


MANIFEST_PATHS = ("jobs/manifest.yaml",)


def merged_pr(item_id: str, repo: str, *, gh=("gh",)) -> tuple[dict | None, list[str]]:
    """The merged pull request that names `item_id`, and the files it touched.

    Stage "merged" is how a repair is judged when the repairer cannot verify on
    the failing machine: Miles runs on nightshift, fixes the producer in the
    repository, and merges. What proves the stage is a MERGED pull request
    whose title or body carries the item id -- not a branch, not a claim in
    prose. The files matter for the boundary: a merge that touched the jobs
    manifest may have repaired the check instead of the producer, and only a
    person may decide that.
    """
    q = subprocess.run([*gh, "pr", "list", "--repo", repo, "--state", "merged", "--search", item_id,
                        "--json", "number,title,body,mergedAt,url", "--limit", "10"],
                       capture_output=True, text=True, timeout=60)
    if q.returncode != 0:
        raise RuntimeError(f"gh pr list failed: {(q.stderr or q.stdout).strip()[:200]}")
    prs = [p for p in json.loads(q.stdout or "[]")
           if p.get("mergedAt") and item_id in (p.get("title", "") + p.get("body", ""))]
    if not prs:
        return None, []
    pr = sorted(prs, key=lambda p: p["mergedAt"])[-1]
    v = subprocess.run([*gh, "pr", "view", str(pr["number"]), "--repo", repo, "--json", "files"],
                       capture_output=True, text=True, timeout=60)
    files = [x.get("path", "") for x in (json.loads(v.stdout or "{}").get("files") or [])] if v.returncode == 0 else []
    return pr, files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job", required=True)
    ap.add_argument("--machine", required=True)
    ap.add_argument("--contract-sha", required=True,
                    help="the job's contract hash at the moment the repair was delegated")
    ap.add_argument("--manifest", default=str(LIB / "jobs" / "manifest.yaml"))
    ap.add_argument("--stage", choices=("verify", "merged"), default="verify",
                    help="verify: the job passes on --machine (default); merged: a merged PR "
                         "naming --item exists in --repo and did not touch the jobs manifest")
    ap.add_argument("--item", help="stage merged: the repair item id the PR must name")
    ap.add_argument("--repo", help="stage merged: OWNER/REPO the producer lives in")
    a = ap.parse_args()

    if a.stage == "merged":
        if not (a.item and a.repo):
            print("REFUSED: --stage merged needs --item and --repo", file=sys.stderr)
            return 2
        try:
            pr, files = merged_pr(a.item, a.repo)
        except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            print(f"not yet: could not ask GitHub ({exc})", file=sys.stderr)
            return 1
        if pr is None:
            print(f"not yet: no merged pull request in {a.repo} names {a.item}", file=sys.stderr)
            return 1
        touched = [p for p in files if any(p.endswith(m) for m in MANIFEST_PATHS)]
        if touched:
            print(f"REFUSED: {pr['url']} touched the jobs manifest ({', '.join(touched)}). A repair "
                  f"fixes the producer, not the check that caught it; changing a contract needs a "
                  f"human.", file=sys.stderr)
            return 1
        print(f"merged: {pr['url']} names {a.item} and left the jobs manifest alone")
        return 0

    manifest = Path(a.manifest)
    now = contract_sha(a.job, manifest)

    if now is None:
        print(f"REFUSED: job {a.job!r} is no longer in the manifest. Deleting the "
              f"check is not repairing the producer.", file=sys.stderr)
        return 1

    if now != a.contract_sha:
        print(f"REFUSED: the contract for {a.job} changed while it was being repaired.\n"
              f"  was: {a.contract_sha}\n  now: {now}\n"
              f"A repair fixes the producer, not the check that caught it. If the "
              f"contract itself is wrong, that is a separate change and it needs a "
              f"human: it is the thing that decides whether this job is healthy.",
              file=sys.stderr)
        return 1

    # Boundary held. Now the done-condition, run by the ordinary verifier so
    # the repair is judged by the same code that judged the failure.
    proc = subprocess.run(
        [sys.executable, str(LIB / "job_verify.py"), "--machine", a.machine,
         "--no-emit", "--alert", "log"],
        capture_output=True, text=True, timeout=900)
    out = proc.stdout + proc.stderr

    # job_verify reports every job for the machine; this repair is about one.
    failing = [ln for ln in out.splitlines() if ln.startswith(f"job '{a.job}'")]
    if failing:
        print(f"not yet: {a.job} still fails verification", file=sys.stderr)
        for ln in out.splitlines():
            if a.job in ln:
                print(f"  {ln.strip()[:200]}", file=sys.stderr)
        return 1

    print(f"repaired: {a.job} verifies on {a.machine}, and its contract is unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
