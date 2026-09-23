#!/usr/bin/env python3
"""Which facts exist here but not on the remote — the drain metric (DIP-0046 A1).

Every actor's log is an append-only chain whose events carry a monotonic `seq`.
Comparing the local head seq against the remote's answers, per actor, "how many
facts has this machine written that nobody else can see?"

That number is the one this installation kept failing to know. 610 commits sat
on a parked branch for two months; 645 more accumulated across 74 unmerged
branches five days running; a `git push … || true` reported "synced clean" while
pushing nothing. Each was invisible for the same reason: the question "is my work
anywhere but this disk?" had no cheap answer, so nothing asked it.

This is deliberately the CHEAPEST possible form of that question. It reads
`git show <remote-ref>:<path>` and the local file — no fold, no chain
verification, no network beyond what a fetch already did. It works under the
current design and under DIP-0046's target design unchanged, which is why it is
built first: a detector that outlives the mechanism it watches is worth more than
one that has to be rewritten alongside it.

What it deliberately does NOT do:

  - It does not fetch. A detector that mutates refs changes the thing it
    measures, and a stale fetch understates a gap rather than inventing one.
    Freshness is the caller's job (`--fetch` if you want it).
  - It does not verify the chain. `ledger_cli.py verify` owns that. Conflating
    "unpushed" with "corrupt" would make one alarm mean two things.
  - It does not treat a missing remote file as zero. A log the remote has never
    seen is a gap of its entire length, which is the loudest case, not the
    quietest.

Exit 0 when every actor is fully published, 1 when any gap exists, 2 on error.

    seq_gap.py [--root DIR] [--space NAME] [--fetch] [--json]
"""
from __future__ import annotations

import argparse
import os
import json
import time
import subprocess
import sys
from pathlib import Path


# AN UNREACHABLE HOST MUST FAIL IN SECONDS, NOT MINUTES. Measured on the mac
# with a work VPN capturing the route to Gitea on 2026-09-08: a single fetch
# took 75 s to give up. Eleven spaces, several of them Gitea-backed, and the
# detector blew its own runtime budget and was killed — so the job failed on
# a TIMEOUT while the artifact it should have written never appeared, and
# `mac-seq-gap` alerted for a third day running.
#
# ssh's default ConnectTimeout is the OS TCP timeout, which is the 75 s. Five
# seconds is far longer than any reachable host needs and turns a hung sweep
# into a fast, honest "unverifiable".
SSH_FAIL_FAST = "ssh -o ConnectTimeout=5 -o BatchMode=yes"


def git(repo: Path, *args: str, timeout: int = 30) -> tuple[int, str]:
    """(returncode, stdout). Never raises — a git failure is data here."""
    env = {**os.environ}
    env.setdefault("GIT_SSH_COMMAND", SSH_FAIL_FAST)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")      # never block on credentials
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                           text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def default_branch(repo: Path) -> str:
    """The repo's default branch from origin/HEAD, falling back to main.

    Same resolution as git_fleet_audit.default_branch. The fallback matters: an
    archive repo with origin/HEAD unset is master-based, and assuming 'main'
    there makes every ref lookup fail — which must read as an ERROR, never as
    'no gap'. See `head_seq` below.
    """
    rc, out = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    out = out.strip()
    return out.split("/", 1)[1] if rc == 0 and out.startswith("origin/") else "main"


def _offline(err: str) -> bool:
    """Is this fetch failure a condition rather than a fault?

    Mirrors ledger_transport._fetch_reason: an auth rejection, an untrusted
    host key or a missing repo is something an operator must fix and stays an
    error. Anything else -- no route, DNS, timeout -- is offline."""
    e = (err or "").lower()
    for fault in ("permission denied", "authentication failed",
                  "host key verification failed", "repository not found",
                  "does not appear to be a git repo"):
        if fault in e:
            return False
    return True


def head_seq(text: str) -> int | None:
    """Highest `seq` in a JSONL log, or None if the log has no readable events.

    THE HIGHEST, NOT THE LAST. This used to scan from the end and stop at the
    first parseable line. On a log whose last complete line is not its highest
    seq (a re-appended older event, a union merge), that understated the local
    head, and `max(0, local - remote)` then reported unpublished events as
    published: local 0..6 then a stray 3, remote at 4, read "ok" with seqs 5
    and 6 on this disk only (DatacoreSpec/Detectors.lean, `sg_head_is_max`).

    Torn or corrupt lines are skipped, so a torn final line — the hazard atomic
    publish (DIP-0046 §10) exists to remove — degrades to the complete events
    instead of crashing the detector.
    """
    best: int | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue          # torn or corrupt line: skip it
        seq = ev.get("seq") if isinstance(ev, dict) else None
        if isinstance(seq, int) and not isinstance(seq, bool) and (best is None or seq > best):
            best = seq
    return best


def _has_lines(text: str) -> bool:
    return any(line.strip() for line in text.splitlines())



def _awake_minutes(since_ms: float, now_ms: float, sleep_log: str | None = None) -> float:
    """Minutes the publisher could actually have run in, not minutes elapsed.

    THE GRACE IS A BUDGET FOR THE PUBLISHER, AND A SLEEPING LAPTOP SPENDS NONE
    OF IT. The publish job runs hourly; the grace says "an event younger than
    90 minutes has not had its chance yet". Measured on the wall clock that
    reasoning breaks the moment the lid shuts: an event written at 23:00 is
    "ten hours old" at 09:00, so it is reported as a gap, but the machine was
    asleep for nine and a half of those hours and the publisher was never
    scheduled. Opening the laptop and running it by hand cleared the alert
    until the next night, which is exactly why this was "fixed" repeatedly and
    stayed broken.

    jobs/awake.py already solved this for max_age_hours (its docstring counts
    117 asleep hours out of 472). Same accounting, same reason, one more
    caller. Sleep accounting only ever EXCUSES age -- if it cannot be read, the
    wall-clock age is used, because a missing power log must not become a new
    way to hide a real gap.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from jobs.awake import awake_age
        from actor_identity import this_actor
        return awake_age(since_ms / 1000.0, this_actor(), now=now_ms / 1000.0,
                         log=sleep_log) / 60.0
    except Exception:  # noqa: BLE001 -- unreadable sleep history is not a gap-hider
        return (now_ms - since_ms) / 60000.0


def unpublished_ages_min(text: str, remote_seq: int | None, now_ms: float,
                         sleep_log: str | None = None) -> list[float]:
    """Ages in minutes of the local events the remote has not got yet.

    Counted in the machine's AWAKE time -- see _awake_minutes.
    """
    ages = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        seq = e.get("seq")
        if not isinstance(seq, int) or (remote_seq is not None and seq <= remote_seq):
            continue
        try:
            ms = float(str(e.get("hlc", "0")).split(".")[0])
        except ValueError:
            continue
        ages.append(max(0.0, _awake_minutes(ms, now_ms, sleep_log)))
    return ages

def scan_space(space: Path, *, fetch: bool = False, grace_min: float = 90.0,
               now_ms: float | None = None, sleep_log: str | None = None) -> list[dict]:
    """One row per actor log in this space.

    `sleep_log` is pmset output, injected by tests. Without it the grace is
    measured against THIS machine's real sleep history, so a test pinning a
    historical `now_ms` silently measures whatever the laptop happened to do in
    that window -- which is how two correct tests here began failing the moment
    sleep accounting arrived. Passing "" means "never slept", which is what an
    always-on host looks like and what those tests mean.
    """
    events_dir = space / ".datacore" / "events"
    if not events_dir.is_dir():
        return []
    # A FAILED FETCH IS NOT A CLEAN FETCH. The return code used to be discarded,
    # so when the remote was unreachable this fell back to the stale
    # remote-tracking ref, found it equal to local, and reported "all published"
    # — the detector announcing that work was safely replicated at the exact
    # moment it could not check. Observed live on 2026-08-11: the Gitea host's
    # disk failed, four spaces became unpushable, and this printed
    # "23 log(s), 0 with unpublished events, 0 error(s)".
    #
    # "I could not verify" is its own answer, and it is not "fine".
    #
    # OFFLINE IS NOT AN ERROR, IT IS A CONDITION -- the distinction
    # ledger_transport already draws, and for the same reason. A closed laptop,
    # a VPN that captured the route to the Gitea host, a train: none of these
    # is a fault anyone can act on, and counting them as errors failed
    # `mac-seq-gap` five times in a row on 2026-09-07 because a work VPN was
    # left on. A DENIED KEY IS NOT OFFLINE, though, and must still be an error:
    # that one never clears on its own.
    stale = False
    unreachable = False
    if fetch:
        rc_fetch, ferr = git(space, "fetch", "-q", "--prune")
        stale = rc_fetch != 0
        unreachable = stale and _offline(ferr)

    db = default_branch(space)
    rows = []
    for log in sorted(events_dir.glob("*.jsonl")):
        actor = log.stem
        rel = log.relative_to(space).as_posix()
        text = log.read_text(errors="replace")
        local = head_seq(text)

        # A LOCAL LOG WITH CONTENT BUT NO READABLE EVENT CANNOT BE COUNTED. It
        # used to fall through as local=None and print "ok ... published" with
        # exit 0 whatever the remote held. What is unpublished is unknown, and
        # "I could not verify" is its own answer. (An EMPTY log has written
        # nothing, so 0 unpublished is the true answer there; a log that lost
        # its events is actor_presence's MISSING, not a publication gap.)
        if local is None and _has_lines(text):
            rows.append({"space": space.name, "actor": actor, "local_seq": None,
                         "remote_seq": None, "gap": None,
                         "error": "local log has no readable events — cannot count what is unpublished"})
            continue

        if stale:
            why = ("remote unreachable — comparison would use a stale ref, "
                   "cannot verify")
            rows.append({"space": space.name, "actor": actor, "local_seq": local,
                         "remote_seq": None, "gap": None,
                         "unverifiable": unreachable,
                         "error": None if unreachable else why,
                         "note": why})
            continue

        rc, out = git(space, "show", f"origin/{db}:{rel}")
        if rc != 0:
            # Distinguish the two reasons `show` fails. A log the remote has
            # never seen is the LOUDEST gap; an unresolvable ref is an error we
            # must not report as agreement.
            rc_ref, _ = git(space, "rev-parse", "--verify", f"origin/{db}")
            if rc_ref != 0:
                rows.append({"space": space.name, "actor": actor, "local_seq": local,
                             "remote_seq": None, "gap": None,
                             "error": f"cannot resolve origin/{db}"})
                continue
            remote = None
        else:
            remote = head_seq(out)

        if local is None:
            gap = 0                              # empty log: nothing written here
        elif remote is None:
            gap = local + 1                      # nothing published at all
        else:
            gap = max(0, local - remote)
        # PUBLISHING IS HOURLY, DETECTION IS HOURLY, AND THEY ARE FIVE MINUTES
        # APART. An event written between the :05 publish and the :10 check is
        # not a gap, it is a queue -- but it read as one, and mac-seq-gap
        # flapped red for an hour on every such event (2026-09-04, four times).
        # An event is unpublished only once it has outlived the publish
        # interval with room to spare; younger ones are reported as pending.
        pending = 0
        if gap and remote is not None:
            ages = unpublished_ages_min(log.read_text(errors="replace"), remote,
                                        now_ms if now_ms is not None else time.time() * 1000.0,
                                        sleep_log)
            if ages and max(ages) < grace_min:
                pending, gap = gap, 0
        rows.append({"space": space.name, "actor": actor, "local_seq": local,
                     "remote_seq": remote, "gap": gap, "pending": pending,
                     "why": _why_unpublished(space) if gap else None, "error": None})
    return rows


def _why_unpublished(space: Path) -> str:
    """Why this space is not publishing -- not merely that it is not.

    A gap is a symptom. The cause is almost always upstream of the ledger:
    ledger_publish_safe deliberately HOLDS rather than push when the outgoing
    history contains commits a human wrote, because pushing someone's unreviewed
    work automatically is not a thing an agent should do. That gate is right.

    What was missing is that it holds SILENTLY. On 2026-09-20 a single human
    commit in 2-datacore -- four days old -- had blocked every ledger publish
    behind it, and the only signal anywhere was this detector counting a number.
    The alert said "3 with unpublished events" for two days running and named
    nothing that could be acted on, so it was read as noise and recurred.

    Naming the cause is the whole fix: "blocked by 1 unpushed human commit" is a
    sentence someone can act on; "3 with unpublished events" is not.
    """
    def git(*args: str) -> str:
        try:
            p = subprocess.run(["git", "-C", str(space), *args],
                               capture_output=True, text=True, timeout=30)
            return p.stdout.strip() if p.returncode == 0 else ""
        except Exception:  # noqa: BLE001 -- a cause we cannot read is not a crash
            return ""

    if not (space / ".git").exists():
        return "space is not a git repository"
    ahead = git("rev-list", "--count", "@{u}..HEAD")
    behind = git("rev-list", "--count", "HEAD..@{u}")
    if not ahead:
        return "no upstream configured"
    human = [ln for ln in git("log", "--format=%s", "@{u}..HEAD").splitlines()
             if not ln.startswith(("ledger:", "nightshift:", "chore(ledger)"))]
    if human:
        return (f"blocked: {len(human)} unpushed commit(s) a human wrote — the publisher "
                f"holds rather than push unreviewed work. First: {human[-1][:60]!r}")
    if behind and behind != "0":
        return f"diverged: {ahead} ahead, {behind} behind — publisher merges then pushes"
    if ahead and ahead != "0":
        return f"{ahead} commit(s) committed locally but not pushed"
    return "committed and pushed; remote ref may be stale — re-fetch"


def _default_root() -> Path:
    """Root from DATACORE_ROOT, then ~/Data — NEVER from this file's location.

    A second checkout exists for scheduled runs (~/.datacore/v2-runner). A
    location-derived root would make this scan THAT tree, which holds zero
    spaces, and report "0 findings" — a false green, and the same defect
    seq_gap shipped once already as a parents[] off-by-one.
    """
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def main() -> int:
    ap = argparse.ArgumentParser()
    # parents: detectors -> lib -> .datacore -> <data root>. Off-by-one here
    # silently scans nothing and reports "0 logs, 0 gaps", which is the
    # detector's own version of a green light meaning nothing.
    ap.add_argument("--root", type=Path, default=_default_root())
    ap.add_argument("--space", help="limit to one space by directory name")
    ap.add_argument("--fetch", action="store_true", help="fetch before comparing")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--grace-minutes", type=float, default=90.0,
                    help="an unpublished event younger than this is pending, not a gap (publish is hourly)")
    args = ap.parse_args()

    spaces = [d for d in sorted(args.root.glob("[0-9]-*"))
              if (d / ".git").exists() and (not args.space or d.name == args.space)]

    # SCANNING NOTHING IS NOT A PASS — this detector already shipped that bug
    # once, as a parents[] off-by-one that reported "0 logs, 0 gaps" while
    # examining an empty directory. Pointed at the wrong root it would do it
    # again, silently, and the contract above it would stay green.
    if not spaces:
        print(f"ERROR: no space repos under {args.root} — refusing to report clean")
        return 2

    rows: list[dict] = []
    for sp in spaces:
        rows.extend(scan_space(sp, fetch=args.fetch, grace_min=args.grace_minutes))

    errors = [r for r in rows if r["error"]]
    gaps = [r for r in rows if r["gap"]]

    if args.json:
        print(json.dumps({"rows": rows, "gaps": len(gaps), "errors": len(errors)}, indent=2))
    else:
        for r in rows:
            if r["error"]:
                print(f"  ERROR {r['space']}/{r['actor']}: {r['error']}")
            elif r["gap"]:
                print(f"  GAP   {r['space']}/{r['actor']}: local seq {r['local_seq']}, "
                      f"remote {r['remote_seq']} — {r['gap']} unpublished")
                if r.get("why"):
                    print(f"        why: {r['why']}")
            elif r["local_seq"] is None:
                print(f"  ok    {r['space']}/{r['actor']}: no local events, nothing to publish")
            else:
                print(f"  ok    {r['space']}/{r['actor']}: seq {r['local_seq']} published")
        # Report positively: a count nobody can mistake for "the detector ran and
        # found nothing" versus "the detector did not run" (DIP-0046 §8).
        pend = sum(r.get("pending") or 0 for r in rows)
        pend_txt = f", {pend} pending (younger than {args.grace_minutes:.0f} min)" if pend else ""
        unver = [r for r in rows if r.get("unverifiable")]
        # Named, never hidden: "could not check" is its own answer and must be
        # visible, but it is not a failure the job contract should trip on.
        unver_txt = f", {len(unver)} unverifiable (remote unreachable)" if unver else ""
        print(f"\nseq-gap: {len(rows)} log(s), {len(gaps)} with unpublished events, "
              f"{len(errors)} error(s){unver_txt}{pend_txt}")
        # NAME THE CAUSE, IN THE ARTIFACT. "20 unverifiable" is a fact; "a
        # full-tunnel VPN is capturing the subnet blackpi lives on" is a fact
        # someone can act on. Three days of `mac-seq-gap` alerts said the
        # former and nobody could act on it.
        #
        # And say plainly that the sweep is DEGRADED: an unverifiable log is
        # not a clean one, so a run that checked 31 of 51 must not read as a
        # pass. It is not a failure either -- nothing is broken and nobody is
        # paged -- which is exactly why it needs its own word.
        if unver:
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                import network_context
                print(network_context.explain())
            except Exception as exc:  # noqa: BLE001 — an explanation must never fail a check
                print(f"network: could not diagnose ({type(exc).__name__})")
            print(f"DEGRADED: verified {len(rows) - len(unver)} of {len(rows)} log(s); "
                  f"{len(unver)} could not be checked at all")

    if errors:
        return 2
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
