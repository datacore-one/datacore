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
    reg = _registry(root)
    key = "<root>" if space.resolve() == root.resolve() else space.name
    entry = reg.get(key)

    # FALL BACK TO THE REMOTE'S NAME, because the directory name is a LOCAL
    # fact. Tris on hermes clones the same repos under different names —
    # `2-plur` there is `5-plur` here, `1-datacore` is `2-datacore` — so a
    # name-keyed registry refuses every one of its spaces and the transport
    # cannot be used on that machine at all. The remote's basename is the same
    # everywhere. (Basename only: this registry is tracked in a PUBLIC repo and
    # must carry no host or address.)
    if not entry:
        rc, out, _ = _git(space, "remote", "get-url", "origin")
        if rc == 0 and out.strip():
            import re as _re
            name = _re.sub(r"\.git$", "", out.strip().rstrip("/").split("/")[-1])
            entry = next((v for v in reg.values() if v.get("repo") == name), None)

    if not entry:
        return Result(False, "repository not in registry/repositories.yaml",
                      {"repo": key, "fix": "classify it as knowledge, code or agent-personal"})
    return Result(True, entry.get("category", ""), {"entry": entry})


def default_branch(space: Path) -> str:
    rc, out, _ = _git(space, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    out = out.strip()
    return out.split("/", 1)[1] if rc == 0 and out.startswith("origin/") else "main"


def _in_progress(space: Path) -> str:
    """What is half-finished in this repo: 'merge' | 'rebase' | 'cherry-pick' |
    'revert' | 'conflict markers' | ''.

    Both signals matter. MERGE_HEAD says git is mid-merge; leftover markers
    with no MERGE_HEAD say a person aborted the merge and left the file — a
    tree the space pre-commit hook refuses where it is installed, and nothing
    refused where it is not.
    """
    rc, gitdir, _ = _git(space, "rev-parse", "--git-dir")
    if rc == 0 and gitdir.strip():
        g = Path(gitdir.strip())
        if not g.is_absolute():
            g = space / g
        for marker, name in (("MERGE_HEAD", "merge"), ("rebase-merge", "rebase"),
                             ("rebase-apply", "rebase"), ("CHERRY_PICK_HEAD", "cherry-pick"),
                             ("REVERT_HEAD", "revert")):
            if (g / marker).exists():
                return name
    for args in (("diff", "--check"), ("diff", "--cached", "--check")):
        _, out, _ = _git(space, *args)
        if "leftover conflict marker" in out:
            return "conflict markers"
    return ""


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
        # "check your key OR your route": a VPN or exit node can put a different
        # host on the far end of the same address, which answers and rejects the
        # key — identical symptom, completely different fix. Naming only the key
        # sends the operator to regenerate credentials that were never wrong.
        return "auth denied (key rejected — check the key, or a VPN/exit node)"
    if "host key verification failed" in e:
        return "host key not trusted"
    if "repository not found" in e or "does not appear to be a git repo" in e:
        return "remote repo missing"
    return "fetch failed (offline?)"


def _converge_locked(space: Path, *, publish: bool = True) -> Result:
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

    # Never autosave a half-finished merge. A converge that reaches a repo
    # whose previous merge stopped on a conflict — markers in the tree,
    # MERGE_HEAD in .git — would `add -A` the markers, commit them as an
    # autosave and push them to the shared remote. `sync push` did exactly
    # that (datacore#28). An in-progress merge, rebase or cherry-pick belongs
    # to whoever started it; the converge steps back and names what it found.
    busy = _in_progress(space)
    if busy:
        return Result(False, f"{busy} in progress — finish or abort it by hand, then converge",
                      {"branch": db, "in_progress": busy})

    # Autosave BEFORE merging. A dirty working file makes git refuse the
    # merge, and refusing forever means never converging — 2-datacore was
    # 45 behind when this was written. Commit, never stash: ENG-2026-0729-009
    # cost 10 orphaned stashes over six weeks because a stash is invisible
    # while a commit is on a branch, findable and pushable.
    rc, out, _ = _git(space, "status", "--porcelain")
    autosaved = bool(out.strip())
    if autosaved:
        _git(space, "add", "-A")
        # NEVER autosave a submodule pointer. `add -A` stages a changed
        # gitlink, so an unattended converge would silently move `.datacore/dips`
        # to whatever commit happens to be checked out locally — publishing a
        # DIP revision nobody chose to publish, as a side effect of syncing
        # something else. Bumping a pointer is a deliberate act; unstage them and
        # leave the change in the working tree where it stays visible.
        # Read GITLINKS FROM THE INDEX, not `git submodule foreach`.
        #
        # foreach ABORTS ON THE FIRST ERROR. Hermes has a gitlink whose path has
        # no url in .gitmodules, so foreach emitted one entry, died, and this
        # guard unstaged only that one — then committed three space pointers
        # (1-datacore, 2-plur, 3-firm) exactly as if the guard were not there.
        # A protection that depends on unrelated config being well-formed is not
        # a protection.
        #
        # Mode 160000 in the index IS a gitlink, by definition, whether or not
        # .gitmodules knows about it. Orphan gitlinks — committed without
        # submodule config — are precisely the ones nothing else would catch.
        rc, staged, _ = _git(space, "diff", "--cached", "--raw")
        for line in (staged or "").splitlines():
            # ":100644 160000 <sha> <sha> M\tpath"
            if line.startswith(":") and " 160000 " in line[:40]:
                path = line.split("\t", 1)[-1].strip()
                if path:
                    _git(space, "restore", "--staged", "--", path)

        # Unstaging the submodules may have emptied the index. `git commit` then
        # exits non-zero for "nothing to commit", which the check below would
        # report as a REFUSED autosave and abort the whole converge — so a repo
        # whose only change is a submodule pointer could never sync again.
        # Observed on nightshift: 2 commits ahead, 7 behind, dirty only in
        # `.datacore/dips`, unable to converge at all.
        rc_staged, _, _ = _git(space, "diff", "--cached", "--quiet")
        if rc_staged == 0:                      # 0 = no staged changes remain
            autosaved = False
            crc, cout, cerr = 0, "", ""
        else:
            crc, cout, cerr = _git(space, "commit",
                                   "-m", "ledger: autosave before converge")
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
    # PER-ACTOR LEDGER REFS. A satellite writer publishes its claims on its
    # own ref (refs/heads/ledger/<actor>: plur-claw's dispatcher since
    # 2026-08-10) because only that writer pushes there and a push can never
    # race. But nothing ever folded those refs back into the default branch:
    # Data's last claims sat on ledger/data from 2026-08-11 and reached main
    # only by hand. Every converge now merges each origin/ledger/* ref into the
    # branch before publishing. Per-writer logs are disjoint files, so this is
    # a union; a conflict here is a genuine one and stops, like any other.
    rc, refs_out, _ = _git(space, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/ledger/")
    merged_refs = []
    for ref in (refs_out.split() if rc == 0 else []):
        rc, ahead, _ = _git(space, "rev-list", "--count", f"HEAD..{ref}")
        if rc != 0 or ahead.strip() == "0":
            continue
        rc, mout, err = _git(space, "merge", "--no-edit", ref)
        if rc != 0:
            err = "\n".join(x for x in (mout.strip(), err.strip()) if x)
            _git(space, "merge", "--abort")
            return Result(False, "merge conflict on a ledger ref — human needed",
                          {"branch": db, "ref": ref, "autosaved": autosaved, "detail": err[:400]})
        merged_refs.append(ref)
    # PUBLISH. Converge previously stopped here, which made it a one-way
    # operation: it pulled and never pushed. Every caller means both — `sync`,
    # `./sync pull`, and cos_sync on winston's 15-minute cron all report
    # "synced clean" from this Result. Measured before the fix: 5-plur sat 2
    # commits ahead of a reachable GitHub remote, silently, and nightshift held
    # 4 including a learning-classifier fix and a 140-line audit script.
    #
    # `publish=False` only for _push_with_retry, which calls this to resolve a
    # non-fast-forward and would otherwise recurse into pushing.
    if not publish:
        return Result(True, "converged", {"branch": db, "autosaved": autosaved, "ledger_refs": merged_refs})
    pr = _push_with_retry(space, db)
    if not pr.ok:
        return Result(False, f"converged but not published: {pr.reason}",
                      {"branch": db, "autosaved": autosaved, **pr.context})
    return Result(True, "converged", {"branch": db, "autosaved": autosaved,
                                      "pushed": pr.context.get("attempts", 1), "ledger_refs": merged_refs})


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
            c = _converge_locked(space, publish=False)
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


def _code_update(repo: Path) -> str:
    """Bring a CODE repo forward without ever committing for it.

    The retired `./sync` pulled these with `--autostash` and swallowed the
    result (datacore#31). A code repo is a person's working tree: fetch, then
    fast-forward the default branch when it is checked out and clean enough
    to move. Anything else is reported in the operator's vocabulary and left
    exactly as found.

    'clean' | 'dirty' | 'parked' | 'diverged' | 'offline' | 'blocked'
    """
    rc, out, _ = _git(repo, "status", "--porcelain")
    dirty = bool(out.strip())
    rc, _, err = _git(repo, "fetch", "-q", "origin")
    if rc != 0:
        reason = _fetch_reason(err)
        return "offline" if "offline" in reason else "blocked"
    db = default_branch(repo)
    _, cur, _ = _git(repo, "branch", "--show-current")
    if cur.strip() != db:
        return "parked"          # on a work branch — theirs, untouched
    rc, _, err = _git(repo, "merge", "--ff-only", "-q", f"origin/{db}")
    if rc != 0:
        # Either the branch has its own commits (diverged: needs a PR or a
        # merge by a person) or local edits sit on files the remote moved.
        return "dirty" if dirty else "diverged"
    return "dirty" if dirty else "clean"


def sync_outcomes(root: Path, only: str | None = None,
                  include_code: bool = True) -> list[tuple[str, str, str]]:
    """Every registered repo under `root`, converged or fast-forwarded.

    The registry, not a glob, says what is synced: `[0-9]-*` found only the
    spaces, and the modules, DIPs and project repos that `./sync` used to
    pull were left to a script this replaces. Knowledge and agent-personal
    repos converge (autosave, merge, push); code repos are fast-forwarded and
    never committed. Returns (name, category, outcome) per repo.
    """
    out: list[tuple[str, str, str]] = []
    for key, entry in _registry(root).items():
        path = root if key == "<root>" else root / key
        name = "<root>" if key == "<root>" else key
        if only and Path(key).name != only and key != only:
            continue
        if not (path / ".git").exists():
            continue
        cat = str(entry.get("category") or "")
        if cat == "code":
            if include_code:
                out.append((name, cat, _code_update(path)))
            continue
        out.append((name, cat, sync_repo(path, quiet=True)))
    return out


HUMAN_NEEDED = ("conflict", "blocked", "diverged")


def sync_all(root: Path, only: str | None = None, quiet: bool = False,
             include_code: bool = True) -> int:
    outcomes = sync_outcomes(root, only=only, include_code=include_code)
    bad = [(n, o) for n, _, o in outcomes if o in HUMAN_NEEDED]
    if not quiet:
        for name, cat, o in outcomes:
            print(f"{name}: {o}" + (f"  [{cat}]" if cat == "code" else ""))
        print(f"\nsync: {len(outcomes)} repo(s), {len(bad)} needing a human")
    return 1 if bad else 0


def status_lines(root: Path) -> list[str]:
    """One line per registered repo: branch, dirty count, ahead/behind. Touches nothing."""
    lines = []
    for key, entry in _registry(root).items():
        path = root if key == "<root>" else root / key
        if not (path / ".git").exists():
            continue
        _, cur, _ = _git(path, "branch", "--show-current")
        _, st, _ = _git(path, "status", "--porcelain")
        db = default_branch(path)
        rc, ab, _ = _git(path, "rev-list", "--left-right", "--count", f"origin/{db}...HEAD")
        behind, ahead = (ab.split() + ["?", "?"])[:2] if rc == 0 else ("?", "?")
        dirty = len([l for l in st.splitlines() if l.strip()])
        lines.append(f"{key if key != '<root>' else '<root>'}: {cur.strip() or '?'} "
                     f"dirty={dirty} ahead={ahead} behind={behind} [{entry.get('category', '')}]")
    return lines


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ledger transport")
    ap.add_argument("op", choices=["converge", "gaps", "classify", "sync", "status"])
    ap.add_argument("--space", type=Path, help="required for all ops except sync")
    ap.add_argument("--repo", help="sync: limit to one space by directory name")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-code", action="store_true",
                    help="sync: knowledge repos only, leave code repos alone")
    a = ap.parse_args()

    if a.op == "sync":
        raise SystemExit(sync_all(a.root, only=a.repo, quiet=a.quiet,
                                  include_code=not a.no_code))
    if a.op == "status":
        print("\n".join(status_lines(a.root)))
        raise SystemExit(0)
    if a.space is None:
        ap.error(f"--space is required for {a.op}")
    fn = {"converge": converge, "gaps": gaps, "classify": classify}[a.op]
    res = fn(a.space)
    print(json.dumps({"ok": res.ok, "reason": res.reason, "context": res.context},
                     indent=2, default=str))
    raise SystemExit(0 if res.ok else 1)
