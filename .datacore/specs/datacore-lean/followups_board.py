#!/usr/bin/env python3
"""Render FOLLOWUPS.md (after applying the core-verification decisions) as a
local decision board. Same renderer as decisions_board.py.

Usage: python3 followups_board.py [--open]
"""
import sys
from decisions_board import o, render

SLUG = "core-followups"


def row(area, title, context, options, suggested, ref):
    return {"area": area, "title": title, "context": context, "options": options,
            "suggested": suggested, "meta": [ref]}


GO = lambda what: o("go", "Go", what)
HOLD = o("hold", "Hold", "Nothing runs; stays listed in FOLLOWUPS.md.")

SECTIONS = [
 ("actions", "A", "Actions that leave this machine",
  "Each runs only with a Go. Order matters: A2 before A1, A1 before A6/A9.", [
  row("Rollout", "Compare .env vs local.env on every host by value fingerprint (C5)",
      "Read-only. Prints key names and 12-char sha256 fingerprints, never values. Needed before host-wins precedence rolls out.",
      [GO("ssh each host with env_overlap_report.py; report overlaps and differences."), HOLD], "go", "FOLLOWUPS A2"),
  row("Rollout", "Distribute the new creds.py to all hosts in one run (C4)",
      "Its lock file name changed, so old and new must not run side by side. Runs .datacore/secrets/scripts/distribute.sh; it only copies code, and rotates no secrets.",
      [GO("One distribute run after A2 shows no surprising overlaps."), HOLD], "go", "FOLLOWUPS A1"),
  row("Rollout", "Confirm every satellite resolves a declared actor (L10)",
      "After rollout, a host whose this_actor(strict=True) fails refuses to append to the ledger. The registry declares all servers; only the mac is checked.",
      [GO("ssh each host, run this_actor(strict=True) read-only, report."), HOLD], "go", "FOLLOWUPS A9"),
  row("Rollout", "Check which actor the nightshift host's dispatcher runs as (L4)",
      "5 open items are addressed to miles; since L4 only a dispatcher running as miles takes them.",
      [GO("Read DATACORE_ACTOR from ~/.config/datacore/dispatcher.env on the host (name only), report."), HOLD], "go", "FOLLOWUPS A8"),
  row("Baselines", "Run id_churn --acknowledge on each host (D1)",
      "The legacy count baseline hides up to 360 (0-personal) and 271 (2-datacore) new orphans until re-acknowledged. Every space has 0 orphans today, so the new baseline records empty sets.",
      [GO("Run it on the mac now and on each host after rollout."), HOLD], "go", "FOLLOWUPS A3"),
  row("Ledger", "Switch spaces to edit protocol 2 (type-strict merges, L7)",
      "Only safe once every host runs the new code: an older reader treats a version-2 edit as a conflict and its state_root diverges.",
      [o("after", "Go after rollout is confirmed", "Write 2 to .datacore/ledger-edit-protocol in all spaces once A1+A9 are done."),
       HOLD], "after", "FOLLOWUPS A6"),
  row("Research", "Migrate research queue properties into the drawer (D9)",
      "A dry run on a copy moved 31 properties on 17 items, created no duplicates, and running it twice changed nothing. It writes under the org transaction lock and refuses if the file changed.",
      [GO("Run migrate_research_props.py --apply on 0-personal/org/research_learning.org."), HOLD], "go", "FOLLOWUPS A7"),
  row("Specs", "Apply the three DIP text edits (D5, G2, P7)",
      "DIP-0002: strike 'Override values'. DIP-0009: add DEFERRED→CANCELLED. DIP-0010: mark ConflictDetector/Resolver removed. The dips repo has your uncommitted work.",
      [o("edit", "Edit, don't commit", "Change only those lines; leave your pending work and commits to you."),
       HOLD], "edit", "FOLLOWUPS A5"),
  row("Release", "org-workspace 0.6.0 (G5)",
      "Blocked: the new default would make default-config readers treat headerless QUEUED/WORKING/FAILED as heading text. Needs intent_sources ported to v2.0 and a scan of live org files first; versions also disagree (0.5.1 / 0.5.2 / 0.5.4).",
      [o("prep", "Prepare only", "Port intent_sources + its test, scan live org files read-only for headerless retired states, check PyPI's version; no release."),
       o("release", "Prepare and release", "Same, then build and upload to PyPI if the scan is clean."),
       HOLD], "prep", "FOLLOWUPS A4"),
 ]),
 ("exposure", "X", "Exposure found", "A privacy leak path found while applying D6.", [
  row("Context", "2-datacore/SCAFFOLDING.md is tracked in a repo with a public upstream",
      "It contains SPACE-layer content and now refuses to rebuild. The repo's `upstream` remote is datacore-one/datacore-org (protected). SCAFFOLDING.space.md is already tracked there too.",
      [o("untrack", "Untrack + gitignore both", "git rm --cached the composed file and the .space.md, add ignore rules; no push."),
       o("intended", "Public upstream is intended", "Keep tracked; allow SPACE content for this repo explicitly."),
       HOLD], "untrack", "findings/knowledge.md"),
 ]),
 ("questions", "Q", "Questions raised while applying", "Smaller policy calls; each has a suggestion.", [
  row("Ledger", "Nightly invariants runs that can't tell now page (L2). Keep paging?",
      "Exit 2 is outside exit_ok [0,1], so the job alerts, which is what 'nightly check fails' meant.",
      [o("page", "Keep paging", "An unknown night is a real alarm."),
       o("quiet", "Add 2 to exit_ok, match SOUND|UNKNOWN", "Recorded, no page.")], "page", "findings/ledger-seal.md"),
  row("Ledger", "Move non-strict identity callers to this_actor(strict=True)?",
      "They fail at append instead of at start. Nightshift claim.py and ledger_hooks.py use the raw hostname.",
      [o("move", "Move them all", "Undeclared hosts fail fast, everywhere."), o("keep", "Keep", "Append is the single gate.")],
      "move", "findings/git-fleet.md"),
  row("Ledger", "Make projection_state and ledger_phase1_prepare detect changes type-strictly?",
      "They still use Python !=, so an Org edit from 1 to True never becomes a proposed edit even under protocol 2.",
      [o("strict", "Yes, with protocol 2", "Consistent with L7."), o("keep", "Keep", "Leave it.")], "strict", "findings/ledger-policy.md"),
  row("Guards", "Exclude the Bash `description` field from effect matching?",
      "Since S1 the whole input is matched, so a description that quotes a Stripe URL pauses an innocent command.",
      [o("exclude", "Exclude description", "It is prose, not an effect."), o("keep", "Keep matching it", "Maximum coverage.")],
      "exclude", "findings/guards.md"),
  row("Guards", "Repos with no remote-tracking refs: judge the whole push range (S3)?",
      "Never-fetched repos have every commit judged; old edits to another actor's log would refuse the push.",
      [o("accept", "Accept", "Rare, and the refusal names the commit."), o("scope", "Scope to commits since last push", "Needs a record of the last push.")],
      "accept", "findings/guards.md"),
  row("Sync", "Run the fleet sweep's git pull under the repo lock too?",
      "P1 locks stage/commit/push. Locking the pull makes the transport wait during network time.",
      [o("lock", "Lock the pull", "No interleaving with transport merges."), o("keep", "Keep outside", "Less waiting.")], "lock", "findings/publication.md"),
  row("Sync", "Should a deletion refusal (P2) fail the sweep run?",
      "Today it exits 1 like a ledger fork, so the timer goes red.",
      [o("red", "Fail the run", "Deletions need attention."), o("report", "Report only", "Keep the timer green.")], "red", "findings/git-fleet.md"),
  row("Sync", "Fetch before the deletion check even without --pull?",
      "A stale origin can refuse commits that already landed; it never wrongly pushes.",
      [o("fetch", "Fetch first", "Fewer false refusals."), o("keep", "Keep", "No extra network call.")], "fetch", "findings/git-fleet.md"),
  row("GitHub", "For issues closed as duplicate, look up the canonical issue?",
      "One extra API call per duplicate; :CANCEL_REASON: would name the canonical issue.",
      [o("lookup", "Look it up", "Better trail."), o("keep", "Name the duplicate only", "No extra calls.")], "keep", "findings/reconcile.md"),
  row("Nightshift", "Should reconcile_attempt --reopen emit the ledger event itself in phase-1 spaces?",
      "It refuses REVIEW tasks there today, like GC.",
      [o("emit", "Emit the ledger event", "Reopen works everywhere."), o("keep", "Keep refusing", "Reopen phase-1 tasks through the ledger tools.")],
      "emit", "findings/nightshift-lifecycle.md"),
  row("Nightshift", "If execute ever hits an exhausted budget, treat it as a deferral in run.py?",
      "Unreachable today (checked before claim); if reached it would record a failed attempt.",
      [o("defer", "Treat as deferral", "Defense in depth."), o("keep", "Keep", "Unreachable.")], "defer", "findings/nightshift-lifecycle.md"),
  row("Org", "Move the manual org repair tools under the transaction lock now?",
      "org_union_merge --apply, org_dedup_within_file, org_resolve_id_conflicts, inbox_dedup, stamp_seq_todo, validate_org_dates --fix; plus a timeout= on serialized.",
      [o("now", "Now", "No unlocked writers left."), o("later", "Later", "Manual tools, rare use.")], "now", "findings/org-transaction.md"),
  row("Org", "Dedup in a generated next_actions.org: dismiss the dropped id via the ledger?",
      "It now refuses to drop a copy with a distinct id there.",
      [o("ledger", "Dismiss via the ledger", "Uses the conditional-payload precondition."), o("keep", "Keep refusing", "Human handles it.")],
      "keep", "findings/org-tools.md"),
  row("Org", "Union merge: create a heading for a parentless theirs item deeper than level 1?",
      "It still goes to end of file under the last heading.",
      [o("create", "Create a heading", "Same rule as G12."), o("keep", "Keep", "Rare; files that start at level 2.")],
      "create", "findings/org-tools.md"),
  row("Docs", "Update router.py's 'artifact-landed signal' comment for P5?",
      "Since P5 a Review task with a tracked PR closes only when the PR merges.",
      [o("fix", "Fix the comment", "Docs match behaviour."), o("keep", "Leave", "")], "fix", "findings/reconcile.md"),
 ]),
]


if __name__ == "__main__":
    render(SLUG, "Core verification follow-ups", "Datacore core · after applying decisions · 2026-09-23",
           "Actions that leave this machine, one exposure, and the smaller questions raised while applying your decisions. Nothing runs until you say “apply the decisions”.",
           [("Decided", "59 of 59"), ("Applied", "local changes done")],
           ["specs/datacore-lean/FOLLOWUPS.md"], SECTIONS,
           noted=["The chief-of-staff environment tests need an installed core to run here; 4 fail at HEAD too. Nothing to decide."],
           open_page="--open" in sys.argv)
