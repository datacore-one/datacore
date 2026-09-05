#!/usr/bin/env python3
"""Bring org-created tasks into the ledger. The other half of the migration.

`genesis.import_space()` was written as a one-shot migration and run once, so
the ledger holds a snapshot of the org files as they were on 2026-08-10. Every
task captured since -- by `/process-inbox`, by the GTD MCP tools, by hand --
exists only in org. The ledger drifts a little further from reality each day
and nothing reports it.

The importer is already idempotent: `scan()` folds the ledger first and only
returns items it has never seen, keyed on the org `:ID:`. So the fix is not new
import logic, it is RUNNING the existing logic on a schedule.

Two things this does that a bare import_space() loop would not:

  IDS FIRST. A heading with no `:ID:` is invisible to the ledger -- there is
  nothing stable to key on, so it can neither be imported nor deduped. Capture
  does not always assign one, so ensure-ids runs before every scan. Without
  this the sweep silently ignores exactly the newest tasks.

  DRIFT IS REPORTED, NOT JUST FIXED. The count of items that had gone missing
  is the interesting number: a sweep that quietly imports 40 tasks looks
  identical to one that imports 0, and the difference is whether capture is
  reaching the ledger at all.

Usage:
    ledger_ingest_org.py [--root DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import os
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger.fold import fold  # noqa: E402
from ledger.genesis import import_space, scan  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger_dismiss_orphans import confirm_and_dismiss  # noqa: E402

ACTIVE = ("TODO", "NEXT", "WAITING", "DEFERRED", "QUEUED", "WORKING", "REVIEW", "FAILED")
LIVE = ("created", "claimed", "granted")

#: Org states that close an item IN THE LEDGER, and the dismiss `kind` each
#: one means. DIP-0009 v2.0's transition table rules `DONE, CANCELLED ->
#: terminal`, so both must dismiss; leaving CANCELLED out is what made 33
#: cancelled tasks sit in the projection as permanent drift across four
#: spaces, holding `all_clean` false forever and turning box-projection-drift
#: into an alert that could never go green.
#:
#: DEFERRED IS DELIBERATELY ABSENT. The same table calls it "closed but
#: wakeable, i.e. done-class, non-terminal": a benched task wakes on a
#: past-due SCHEDULED: or when its intent lane switches back on, and
#: `item.dismiss` is terminal (DIP-0034). Closed-in-org is not
#: dismissed-in-ledger. Dismissing it would make the wake impossible.
#:
#: `dropped`, not `done`, for CANCELLED: `fold.was_finished()` counts only
#: "done", and cancelled work must not inflate the completion stats -- the
#: exact direction `fold.closure_kind` records every historical
#: misclassification as falling.
TERMINAL_KINDS = {"DONE": "done", "CANCELLED": "dropped"}


def _this_actor() -> str:
    """Resolve THIS machine's actor. Never a hardcoded default.

    This defaulted to "mac". Run anywhere else — and it is run on the
    chief-of-staff box every night — it appends to mac.jsonl AS mac, so two
    machines write one per-writer log with independent sequences. That is a
    guaranteed fork of the exact kind the whole design exists to prevent, and
    it produced one overnight in 1-datafund and 5-plur (same payload, different
    hash, at the same seq) which the 06:00 checklist caught.

    A per-writer log is only disjoint if exactly one writer writes it. A
    default actor silently breaks that for every machine except the one the
    default names.
    """
    try:
        from actor_identity import this_actor
    except ImportError:
        import importlib.util as _ilu, pathlib as _pl
        _spec = _ilu.spec_from_file_location("actor_identity", _pl.Path(__file__).resolve().parent / "actor_identity.py")
        _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)
        this_actor = _m.this_actor
    return this_actor()

def sync_state(space: Path, actor: str | None = None, dry_run: bool = False) -> dict:
    """Reconcile an already-imported task with what org says about it NOW.

    Importing new tasks is a third of the job. Org keeps moving afterwards — a
    task is closed, rescheduled, or picked up as REVIEW by nightshift — and none
    of that reached the ledger, so the projection drifted from the file it is
    meant to reproduce. Two reconciliations, each a different event because they
    mean different things:

      CLOSED  org says DONE or CANCELLED -> item.dismiss (see TERMINAL_KINDS;
              DEFERRED is closed-but-wakeable and deliberately excluded).
              NOT item.complete: the fold requires
              status == claimed before completing, so completing an unclaimed
              item is a SILENT no-op — two full passes over org-DONE tasks did
              nothing and reported success. Fabricating a claim that never
              happened to satisfy the state machine would put a lie in the audit
              trail. Dismiss is what "a human closed this" means.

      FIELDS  scheduled/deadline/state changed -> item.update carrying only the
              keys that actually differ.
    """
    from org_workspace import OrgWorkspace
    org_file = space / "org" / "next_actions.org"
    if not org_file.exists():
        return {"dismissed": 0, "updated": 0}
    # File-level tags, parsed the way genesis does. Items created through the
    # adapter/ingest never recorded `filetags`, while genesis-imported ones
    # do — checkpoint-verify then compares unlike data and reports the same
    # constant as an alteration (2 items in 0-personal, 2026-08-29, differing
    # by exactly the file's :gtd:). Fill-only, same philosophy as tags below.
    file_filetags: list = []
    try:
        for _l in org_file.read_text(encoding="utf-8").splitlines()[:10]:
            if _l.startswith("#+FILETAGS:"):
                file_filetags = sorted(
                    t for t in _l.split(":", 1)[1].split(":") if t.strip())
                break
    except OSError:
        pass
    ws = OrgWorkspace(); ws.load(str(org_file))
    state = fold(read_events(space))
    log = None
    dismissed = updated = 0
    for node in ws.all_nodes():
        nid = node.get_property("ID")
        if not nid:
            continue
        item = state.items.get(nid)
        # A node with no ledger item is NOT this function's problem. Admission
        # is scan() + import_space(), called just above this in main(). It was
        # tempting to create here too -- an org task can sit outside the ledger
        # for hours, which is very visible now the app reads the ledger -- but
        # that would make two code paths responsible for admitting items, with
        # two ideas of which nodes qualify. The latency is a SCHEDULING problem;
        # solving it with a second creator would trade a delay for a divergence.
        if not item or item.status not in LIVE:
            continue
        if node.todo in TERMINAL_KINDS:
            if not dry_run:
                log = log or EventLog(space, actor or _this_actor())
                log.append("item.dismiss",
                           {"id": nid, "kind": TERMINAL_KINDS[node.todo],
                            "reason": f"closed as {node.todo} in next_actions.org"})
            dismissed += 1
            continue
        if node.todo not in ACTIVE:
            continue
        cur = item.payload or {}
        # title too: a heading edited in org left the projection rendering the
        # imported wording forever, which is a diff no amount of state syncing
        # would ever close.
        # TAGS SYNC FROM `shallow_tags` — the node's OWN tags — and never from
        # `node.tags`, which is the inherited set.
        #
        # The 2026-08-12 attempt used node.tags and went badly: 569 items
        # updated and 0-personal from clean to changed=46, because the projector
        # files each item under its own sections, which contribute those
        # ancestor tags AGAIN. Writing inherited tags into the payload
        # double-applies inheritance.
        #
        # That attempt concluded own tags were unavailable. They are:
        # org_workspace exposes `shallow_tags`, and genesis.py has used it since
        # the migration. Not syncing them at all left a real hole — an item
        # created through the adapter without tags could never acquire the ones
        # org shows, so 5-plur held two items tagged in org and untagged in the
        # ledger with no path to converge.
        #
        # `or None` normalises empty to absent, matching how the adapter emits
        # tags on create; without it every untagged item would diff forever
        # between [] and None.
        # ADDITIVE ONLY: fill tags in when the ledger has NONE, never rewrite
        # or remove them.
        #
        # A dry run over the real corpus found two distinct classes. Items
        # created through the adapter without tags, where org has them and the
        # ledger has None — a genuine hole, since nothing could ever fill it.
        # And items whose ledger tags are a SUPERSET of the heading's own,
        # because genesis ran under an org_workspace without `shallow_tags` and
        # fell back to the inherited set, baking ancestor tags into the payload.
        #
        # Syncing both directions would have stripped tags from 41 items to
        # "correct" that history. Removing data to satisfy a comparison is the
        # wrong trade: those tags are what the projector renders today, the
        # promoted-orphan path depends on them, and a diff is not a mandate.
        # Filling a hole is safe; rewriting history to match a checker is not.
        own = getattr(node, "shallow_tags", None)
        want = {"state": node.todo,
                "title": node.heading,
                "scheduled": str(node.scheduled or "") or None,
                "deadline": str(node.deadline or "") or None}

        # STRUCTURE. `parent`/`level` are reconciled from org like every other
        # field, because DIP-0034 settles which side wins: "org-mode (DIP-0009)
        # remains the sole source of truth for GTD task state". A ledger parent
        # that disagrees with org is stale, not authoritative.
        #
        # This was held back once for lack of that ruling. The doubt was that
        # `node.parent` is the immediate heading while genesis records the
        # nearest section, so the two might mean different things — but the
        # measured cases show genesis recording ordinary TASK parents too
        # (0-personal's org-20260702-063403 is filed under a task titled
        # "Continue: PLUR Enterprise …"), so they are the same relation, and
        # org's is the current one.
        #
        # The cost of leaving it: a stale parent makes the projector nest the
        # item under an unrelated heading, where it inherits that heading's
        # tags. 116 items in 0-personal came back carrying
        # continuation/demo/enterprise instead of their own review/strategic —
        # the same neighbour-inheritance the projector's own comment records
        # from 2026-08-10.
        parent_node = getattr(node, "parent", None)
        parent_id = (parent_node.get_property("ID")
                     if parent_node is not None else None)
        # Only ever set a parent org actually shows. Absent means top-level,
        # and clearing a recorded parent is exactly what promotes the item back
        # out of a section it no longer belongs to.
        want["parent"] = parent_id
        if node.level:
            want["level"] = node.level
        # UNION, not fill-when-empty. Filling only an absent tag list left the
        # commoner hole open: an item that HAS tags in the ledger and gains one
        # in org (a human marking something `:urgent:`) could never converge,
        # and at the Phase 1 flip — when the projection replaces the authored
        # file — that tag would simply disappear.
        #
        # Union is not the both-directions sync this deliberately rejected.
        # That one REMOVED tags, which would have stripped 39 items whose
        # ledger payloads legitimately carry ancestor tags baked in by a
        # genesis run predating `shallow_tags`. Union only adds, so those 39
        # keep every tag they hold and stay exactly as they are.
        #
        # Measured over the live corpus before changing it: 1396 items
        # identical, 39 ledger-superset (untouched), 4 org-superset (fixed),
        # 0 diverging both ways. Four items, zero collateral.
        if own is not None:
            merged = sorted({t for t in own if t} | set(cur.get("tags") or []))
            if merged and merged != sorted(cur.get("tags") or []):
                want["tags"] = merged
            # `effective_tags` MUST MOVE WITH `tags`. ledger_checkpoint's
            # fingerprint prefers effective_tags over tags, so updating one and
            # not the other makes the round-trip compare a fresh value against
            # a stale one: the restored side re-derives effective tags from the
            # projection it just parsed, the live side still reports what
            # genesis recorded, and four spaces flipped to "would NOT restore"
            # on tags that had in fact been preserved exactly.
            #
            # Sourced from `node.tags` — the INHERITED set — but ONLY when the
            # ledger records the structure that produces those tags. The
            # projector reproduces a section for an item that has a `parent`;
            # for one that does not, it renders the heading at top level with
            # its own tags and the inherited ones simply do not exist in the
            # file. Recording them anyway writes a promise the round-trip
            # cannot keep: 0-personal's org-20260814-145711 sits under
            # `* Inbox Processed … :inbox:routed:` in the authored file but has
            # parent=None level=None in the ledger (adapter-created, never
            # genesis-imported), and claiming its inherited tags flipped that
            # space to "would NOT restore" on tags the restore handled fine.
            #
            # AUTHORITATIVE, not additive — unlike `tags`. This one is derived:
            # it must equal what the projection will render, so it has to be
            # able to shrink when the previous value overreached. `tags` stays
            # additive because removing there would destroy the ancestor tags
            # 39 items legitimately carry.
            # FILETAGS ARE EXCLUDED. `node.tags` includes the file's
            # `#+FILETAGS:`, but ledger_checkpoint's fingerprint subtracts
            # filetags from both sides — so recording them here puts a tag in
            # `effective_tags` that the restored side removes, and the
            # round-trip reports an alteration on a value that matched.
            # 0-personal's org-20260831-203052 differed by exactly `gtd`, the
            # file's own filetag, on a task created minutes earlier.
            #
            # They are also per-FILE, not per-item, so they carry no
            # information about this task — which is the same reason the
            # fingerprint drops them.
            eff_now = sorted(cur.get("effective_tags") or [])
            inherited = {t for t in (node.tags or []) if t} if cur.get("parent") else set()
            eff = sorted((inherited | set(merged)) - set(file_filetags))
            if eff and eff != eff_now:
                want["effective_tags"] = eff
        if file_filetags and not (cur.get("filetags") or None):
            want["filetags"] = file_filetags
        diff = {k: v for k, v in want.items() if (cur.get(k) or None) != v}
        if not diff:
            continue
        if not dry_run:
            log = log or EventLog(space, actor or _this_actor())
            log.append("item.update", {"id": nid, **diff})
        updated += 1

    # ARCHIVED OUT OF ORG -> dismissed in the ledger, on POSITIVE EVIDENCE.
    #
    # `org-archive-subtree` (Emacs, and the GTD archive tools) moves a heading
    # into `<file>_archive.org` — a plain file move this process cannot hook,
    # and the ledger never hears about it. The item stays live in the
    # projection forever, which is drift that accumulates with every archive
    # pass and holds the Phase 1 gate shut.
    #
    # Deliberately NOT "dismiss anything missing from next_actions.org". Mere
    # absence is far too weak, and dismiss is terminal (DIP-0034). Requiring
    # the id to actually appear in an archive file makes this evidence-based —
    # we close it because we can see where it went, not because we cannot see
    # where it is. (Not a defence against half-written files: org_workspace
    # writes atomically, so those do not occur. It is a defence against a
    # mid-merge tree and against a scan bug, both of which have happened.)
    #
    # An id still present in ANY live org file is left alone: an item can be
    # archived out of next_actions.org and still be authored in inbox.org or
    # nightshift.org, and it is live work while any of them holds it.
    dismissed += _dismiss_archived(space, ws, state, log, actor, dry_run)
    return {"dismissed": dismissed, "updated": updated}


def _dismiss_archived(space, ws, state, log, actor, dry_run) -> int:
    """Close ledger items whose heading now lives in an archive file."""
    import re
    org_dir = space / "org"
    live_ids: set[str] = set()
    archived_ids: set[str] = set()
    for f in sorted(org_dir.glob("*.org")):
        try:
            ids = set(re.findall(r":ID:\s*(\S+)", f.read_text(errors="replace")))
        except OSError:
            # Unreadable file: treat its ids as LIVE, never as archived. The
            # cautious direction is the one that declines to close things.
            continue
        (archived_ids if "archive" in f.name.lower() else live_ids).update(ids)

    closed = 0
    for nid in sorted(archived_ids - live_ids):
        item = state.items.get(nid)
        if not item or item.status not in LIVE:
            continue
        if not dry_run:
            log = log or EventLog(space, actor or _this_actor())
            log.append("item.dismiss", {
                "id": nid, "kind": "housekeeping",
                "reason": "archived out of next_actions.org"})
        closed += 1
    return closed

ORG_FILES = ("inbox.org", "next_actions.org")


def ensure_ids(space: Path, adapter: Path) -> str:
    """Give every heading a stable :ID:. Returns a short status string."""
    touched = []
    for name in ORG_FILES:
        f = space / "org" / name
        if not f.exists():
            continue
        r = subprocess.run(
            [sys.executable, str(adapter), "ensure-ids", "--file", str(f)],
            capture_output=True, text=True, timeout=120,
        )
        touched.append(f"{name}:{'ok' if r.returncode == 0 else 'FAILED'}")
    return " ".join(touched) or "no org files"


def _default_root() -> Path:
    """Root from DATACORE_ROOT, then ~/Data — NEVER from this file's location.

    Scheduled runs execute from a second checkout (~/.datacore/v2-runner) that
    holds no spaces. Derived from __file__, this swept zero spaces and printed
    "imported 0 task(s) across 0 space(s); 0 space(s) failed" — exit 0, contract
    green, and the org->ledger reconciliation silently not happening. Caught by
    running it in a cron-like environment instead of from a shell in ~/Data.
    """
    import os
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def _notify_daemon(root: Path) -> None:
    """POST ledger.sweep.complete to the daemon (best-effort).

    If the daemon is not running, the port file is absent, or the POST fails
    for any reason, the error is logged but the sweep exit code is unaffected.
    Callers never see an exception from this function.
    """
    import urllib.error
    import urllib.request
    port_file = Path.home() / ".datacore" / "app" / "datacored.port"
    token_file = Path.home() / ".datacore" / "app" / "datacored.token"
    try:
        port = int(port_file.read_text().strip())
        token = token_file.read_text().strip()
    except Exception:
        return  # daemon not running or files absent; nothing to notify
    url = f"http://127.0.0.1:{port}/org/ledger-sweep/notify"
    req = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
        print("notified daemon: ledger.sweep.complete")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # The daemon is running but has no such route — the live-refresh
            # feature simply is not deployed in this build. Saying "skipped:
            # 404" on every sweep reads like a fault; it is an absent
            # optional feature, and the sweep itself succeeded.
            print("daemon has no ledger-sweep notify route (optional "
                  "live-refresh not deployed) — sweep unaffected")
        else:
            print(f"daemon notify skipped: HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        print(f"daemon notify skipped: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=_default_root())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    adapter = args.root / ".datacore" / "lib" / "org_workspace_adapter.py"
    spaces = sorted(p for p in args.root.glob("[0-9]-*") if (p / "org").is_dir())
    # Sweeping nothing is not a successful sweep.
    if not spaces:
        print(f"ERROR: no spaces with org/ under {args.root} — refusing to report success")
        return 2

    total_new = 0
    failures = 0
    for space in spaces:
        try:
            ids = "skipped (dry run)" if args.dry_run else ensure_ids(space, adapter)
            before = scan(space)
            new = len(before.importable)
            if new and not args.dry_run:
                # ADMIT UNDER THIS HOST'S ACTOR, never the shared `genesis`.
                #
                # DIP-0046 §151: "One writer per file. Enforced by filename
                # (<actor>.jsonl)." That is what makes (actor, seq) identify
                # exactly one event forever. `genesis` was the right actor for
                # a one-shot migration run once, on one machine; this sweep
                # runs HOURLY ON EVERY HOST, so leaving the default made two
                # machines increment independent counters into the same
                # genesis.jsonl and produce different events under the same
                # seq the moment they both saw a new task before syncing.
                #
                # That is the fork this session resolved three times by hand
                # (5-plur seq 561, 0-personal seq 918-927, 5-plur seq 568) —
                # not bad luck, a shared writer. Per-host actors cannot
                # collide, so the class ends here rather than being detected
                # again by resolve_ledger_conflicts' ForkError guard.
                import_space(space, actor=_this_actor())
            total_new += new
            sy = sync_state(space, dry_run=args.dry_run)
            # ORPHANS ARE A LEDGER FAILURE, so the sweep closes them rather
            # than letting them accumulate for a human to find. Confirmation
            # needs TWO sweeps an hour apart (see confirm_and_dismiss): one
            # scan cannot tell a deleted heading from a tree mid-merge, two
            # can, because transients do not survive the gap.
            orph = confirm_and_dismiss(space, time.time(),
                                       execute=not args.dry_run)
            sy["dismissed"] += orph.get("dismissed", 0)
            if orph.get("refused"):
                print(f"{space.name:14} ORPHAN SWEEP REFUSED — {orph['refused']}")
            drift = new or sy["dismissed"] or sy["updated"]
            flag = "  <-- DRIFT" if drift else ""
            print(f"{space.name:14} new={new:4d} closed={sy['dismissed']:3d} "
                  f"updated={sy['updated']:3d} known={len(before.already_present):4d}{flag}")
        except Exception as exc:  # noqa: BLE001 - one bad space must not stop the sweep
            failures += 1
            print(f"{space.name:14} FAILED: {type(exc).__name__}: {exc}")

    verb = "would import" if args.dry_run else "imported"
    print(f"\n{verb} {total_new} task(s) across {len(spaces)} space(s); {failures} space(s) failed")
    if not args.dry_run:
        _notify_daemon(args.root)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
