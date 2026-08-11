#!/usr/bin/env python3
"""The one writer. Publish facts, receive facts, report what is unpublished.

Sixteen call sites each invented their own commit/push/error handling, which is
why `git push … || true` looked reasonable in three of them and why the same
defect had to be fixed four times and in practice was not. This is the single
place any of that happens.

    append(space, actor, type, payload) -> Result   publish a fact
    converge(space)                     -> Result   receive others' facts
    gaps(space)                         -> Result   what exists only here

THREE PROPERTIES, each traceable to a specific incident.

**Expected failures return; they never raise.** A non-fast-forward push, an
offline remote, a missing ref — outcomes, not exceptions. Every one carries
`ok`, a `reason`, and `context` enough to act. Callers branch on `ok` instead of
wrapping every call, which is what made sixteen partial error handlers. Only
*unexpected* failures raise, because a caller that cannot tell "the remote is
down" from "the repository is corrupt" retries both, and retrying a corrupt
repository in a loop is worse than stopping.

**Merge, never rebase.** Per-writer logs are disjoint files, so a merge is a
union and cannot conflict. Rebase buys nothing here and is the operation that
stranded 610 commits on a parked branch and 645 across 74 run branches.

**The lock is not the whole story, and pretending otherwise is the trap.**
`flock` serialises writers on THIS machine. It cannot serialise two machines
pushing to one remote — the race the architecture exists to make safe. That one
is handled by an explicit bounded fetch-merge-retry on non-fast-forward
rejection. A test using two local processes passes without ever exercising it.

Appending does NOT change `log.py`. Its `EventLog.append()` already takes an
exclusive lock, truncates a torn tail and appends inside one critical section;
replacing that with whole-file rewrite-and-rename would cost O(n) per append and
reintroduce lost updates (DIP-0046 §10).
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger.log import EventLog  # noqa: E402

PUSH_ATTEMPTS = 3
LOCK_DIR = Path.home() / ".datacore" / "state" / "locks"


@dataclass
class Result:
    """An outcome. `ok` is the only thing callers branch on."""
    ok: bool
    reason: str = ""
    context: dict = field(default_factory=dict)

    def __bool__(self) -> bool:      # `if not converge(space):` reads correctly
        return self.ok


def _git(repo: Path, *args: str, timeout: int = 120) -> tuple[int, str, str]:
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"


@contextmanager
def _repo_lock(space: Path):
    """Exclusive, per-repo, SAME-MACHINE ONLY. See the module docstring."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock = LOCK_DIR / f"{space.name}.lock"
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _registry(root: Path) -> dict:
    import yaml
    p = root / ".datacore" / "registry" / "repositories.yaml"
    return (yaml.safe_load(p.read_text()) or {}).get("repositories", {})


def classify(space: Path, root: Path | None = None) -> Result:
    """The repo's category, or a refusal.

    An unregistered repository is REFUSED, never defaulted. Defaulting would
    hand a production repo the knowledge rules — direct pushes to a default
    branch — which is the single mistake the two categories exist to prevent
    (DIP-0046 §1).
    """
    root = root or Path(__file__).resolve().parents[2]
    key = "<root>" if space.resolve() == root.resolve() else space.name
    entry = _registry(root).get(key)
    if not entry:
        return Result(False, "repository not in registry/repositories.yaml",
                      {"repo": key, "fix": "classify it as knowledge, code or agent-personal"})
    return Result(True, entry.get("category", ""), {"entry": entry})


def default_branch(space: Path) -> str:
    rc, out, _ = _git(space, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    out = out.strip()
    return out.split("/", 1)[1] if rc == 0 and out.startswith("origin/") else "main"


def converge(space: Path) -> Result:
    """Receive others' facts: fetch, then MERGE. Never rebase, never reset."""
    cat = classify(space)
    if not cat:
        return cat
    with _repo_lock(space):
        return _converge_locked(space)


def _fetch_reason(err: str) -> str:
    """Name the failure the operator has to act on.

    Ordered most-specific first: a denied key and an unknown host both mention
    the host, so matching on the host alone would swallow the auth case.
    """
    e = err.lower()
    if "permission denied" in e or "authentication failed" in e:
        return "auth denied (key rejected by remote)"
    if "host key verification failed" in e:
        return "host key not trusted"
    if "repository not found" in e or "does not appear to be a git repo" in e:
        return "remote repo missing"
    return "fetch failed (offline?)"


def _converge_locked(space: Path) -> Result:
    """converge() with the repo lock ALREADY HELD.

    The split is not stylistic. `append()` holds the lock and, on a
    non-fast-forward push, must converge before retrying — calling the public
    `converge()` there re-enters `flock` on a second file descriptor for the
    same file and deadlocks the process against itself. It hung for two minutes
    on the first end-to-end smoke test and had to be killed, which is a better
    place to find it than a nightly run.
    """
    rc, _, err = _git(space, "fetch", "--prune", "origin")
    if rc != 0:
        # Offline is not an error state — it is a condition. Report it and
        # let the caller decide; a sweep that treats offline as failure
        # stops working on a train.
        #
        # But DENIED IS NOT OFFLINE, and collapsing them is the exact defect
        # this module exists to remove. Offline says "wait, you are on a
        # train"; denied says "your key stopped working, four spaces are not
        # syncing and will not start on their own". On 2026-08-11 Gitea began
        # rejecting the Mac's ed25519 key mid-afternoon and all four Gitea
        # spaces reported `offline` — indistinguishable, in a sweep summary,
        # from a closed laptop lid.
        return Result(False, _fetch_reason(err), {"stderr": err.strip()[:200]})
    db = default_branch(space)

    # Autosave BEFORE merging. A dirty working file makes git refuse the
    # merge, and refusing forever means never converging — 2-datacore was
    # 45 behind when this was written. Commit, never stash: ENG-2026-0729-009
    # cost 10 orphaned stashes over six weeks because a stash is invisible
    # while a commit is on a branch, findable and pushable.
    rc, out, _ = _git(space, "status", "--porcelain")
    autosaved = bool(out.strip())
    if autosaved:
        _git(space, "add", "-A")
        crc, cout, cerr = _git(space, "commit", "-m", "ledger: autosave before converge")
        if crc != 0:
            # A REFUSED AUTOSAVE MUST STOP THE CONVERGE. This return used to be
            # absent: a pre-commit hook rejected the commit, `add -A` had already
            # staged everything, and the merge then failed with "your local
            # changes would be overwritten" — an error naming the merge, in a
            # repo whose actual problem was one invalid org tag on line 6042.
            # Swallowing a non-zero rc from git is the precise defect this
            # module was written to remove, and it was sitting inside it.
            #
            # Not --no-verify: the hook is a guard doing its job. The operator
            # has to see what it said, so its own output is the reason.
            detail = (cout + cerr).strip()
            return Result(False, "autosave refused by pre-commit hook",
                          {"detail": detail[:400]})

    rc, mout, err = _git(space, "merge", "--no-edit", f"origin/{db}")
    if rc != 0:
        # BOTH streams. git reports conflicts on STDOUT ("CONFLICT (content):
        # Merge conflict in ...") and leaves stderr empty, so capturing only
        # stderr yields a failure with a blank reason — the identical defect
        # that hid a nine-day auth outage behind `claude -p failed: `.
        err = "\n".join(x for x in (mout.strip(), err.strip()) if x)
        _git(space, "merge", "--abort")
        # Never reset, never rescue-branch, never discard. A conflict here
        # is genuine disagreement about content and belongs to a human; the
        # autosave above guarantees their work is already committed.
        return Result(False, "merge conflict — human needed",
                      {"branch": db, "autosaved": autosaved,
                       "detail": err[:400]})
    return Result(True, "converged", {"branch": db, "autosaved": autosaved})


def _push_with_retry(space: Path, db: str) -> Result:
    """Push, converging and retrying on non-fast-forward.

    This is the cross-machine race. `flock` cannot help: the competing writer is
    on another host. Bounded because an unbounded retry against a genuinely
    diverged remote is a spin, not a recovery.
    """
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        rc, _, err = _git(space, "push", "origin", f"HEAD:{db}")
        if rc == 0:
            return Result(True, "pushed", {"attempts": attempt})
        low = err.lower()
        if "non-fast-forward" in low or "fetch first" in low or "rejected" in low:
            c = _converge_locked(space)
            if not c:
                return Result(False, "push rejected and converge failed",
                              {"attempt": attempt, "converge": c.reason})
            continue
        return Result(False, "push failed", {"attempt": attempt, "stderr": err.strip()[:300]})
    return Result(False, f"push still rejected after {PUSH_ATTEMPTS} attempts",
                  {"hint": "remote is moving faster than we can converge"})


def append(space: Path, actor: str, type: str, payload: dict,
           *, push: bool = True) -> Result:
    """Publish one fact.

    Order matters and is the point: the event is appended, committed, and only
    then pushed — and a FAILED PUSH IS REPORTED, never swallowed. The caller
    learns that the fact exists only on this disk, which is precisely what
    `git push … || true` hid while printing "synced clean".
    """
    cat = classify(space)
    if not cat:
        return cat
    with _repo_lock(space):
        try:
            event = EventLog(space, actor).append(type, payload)
        except Exception as exc:  # noqa: BLE001 — a bad event type is the caller's bug
            return Result(False, "append rejected", {"error": f"{type(exc).__name__}: {exc}"})

        rel = f".datacore/events/{actor}.jsonl"
        rc, _, err = _git(space, "add", "--", rel)
        if rc != 0:
            return Result(False, "git add failed", {"stderr": err.strip()[:200]})
        rc, _, err = _git(space, "commit", "-m",
                          f"ledger: {type} by {actor}",
                          "--only", "--", rel)
        if rc != 0 and "nothing to commit" not in (err or "").lower():
            return Result(False, "commit failed", {"stderr": err.strip()[:200]})

        if not push:
            return Result(True, "appended (push deferred)",
                          {"event": event.hlc, "pushed": False})
        db = default_branch(space)
        pushed = _push_with_retry(space, db)

    ctx = {"event": event.hlc, "pushed": pushed.ok}
    if not pushed:
        # The fact is real and durable locally; it is simply not published yet.
        # seq-gap will keep reporting it until it is, which is the safety net.
        return Result(False, f"appended but NOT published: {pushed.reason}",
                      {**ctx, **pushed.context})
    return Result(True, "appended and published", ctx)


def gaps(space: Path) -> Result:
    """What exists here and nowhere else. Delegates to the detector so there is
    one definition of the question."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "detectors"))
    from seq_gap import scan_space  # noqa: E402
    rows = scan_space(space)
    ungapped = [r for r in rows if r.get("gap")]
    return Result(not ungapped,
                  "all published" if not ungapped else f"{len(ungapped)} log(s) unpublished",
                  {"rows": rows})


def sync_repo(repo: Path, quiet: bool = False) -> str:
    """Converge one repo, reported in the operator's vocabulary.

    This was `space_sync.py`: first 142 lines reimplementing the algorithm
    `cos_sync.sh` already had for the box, then an 80-line shim over converge.
    A shim is still a file, still a name to remember, and still a place for the
    next fix to land on one side only — which is the defect DIP-0046 exists to
    remove, not a smaller instance of it worth keeping.

    'clean' | 'offline' | 'blocked' | 'conflict' | 'skipped'. The distinction
    that earns its keep is offline vs blocked: offline clears itself when the
    laptop reopens, blocked never does.
    """
    res = converge(Path(repo))
    if res.ok:
        outcome = "clean"
    elif "not in registry" in res.reason:
        # Refused, not failed: an unregistered repo has no category, so there is
        # no rule to apply. Defaulting silently is what DIP-0046 §1 forbids.
        outcome = "skipped"
    elif any(s in res.reason for s in ("auth denied", "host key", "repo missing",
                                       "autosave refused")):
        outcome = "blocked"
    elif "offline" in res.reason or "fetch failed" in res.reason:
        outcome = "offline"
    else:
        outcome = "conflict"
    if not quiet:
        detail = res.context.get("detail", "")
        print(f"{Path(repo).name}: {outcome}"
              + (f" — {res.reason}" if not res.ok else "")
              + (f"\n  {detail.splitlines()[0]}" if detail else ""))
    return outcome


def sync_all(root: Path, only: str | None = None, quiet: bool = False) -> int:
    repos = [d for d in sorted(root.glob("[0-9]-*"))
             if (d / ".git").exists() and (not only or d.name == only)]
    outcomes = [sync_repo(r, quiet=quiet) for r in repos]
    bad = [o for o in outcomes if o in ("conflict", "blocked")]
    if not quiet:
        print(f"\nsync: {len(outcomes)} repo(s), {len(bad)} needing a human")
    return 1 if bad else 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ledger transport")
    ap.add_argument("op", choices=["converge", "gaps", "classify", "sync"])
    ap.add_argument("--space", type=Path, help="required for all ops except sync")
    ap.add_argument("--repo", help="sync: limit to one space by directory name")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if a.op == "sync":
        raise SystemExit(sync_all(a.root, only=a.repo, quiet=a.quiet))
    if a.space is None:
        ap.error(f"--space is required for {a.op}")
    fn = {"converge": converge, "gaps": gaps, "classify": classify}[a.op]
    res = fn(a.space)
    print(json.dumps({"ok": res.ok, "reason": res.reason, "context": res.context},
                     indent=2, default=str))
    raise SystemExit(0 if res.ok else 1)
