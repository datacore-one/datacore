#!/usr/bin/env python3
"""Render DECISIONS.md (core verification, 2026-09-23) as a local decision board.

Reuses the GTD board's renderer pieces (lib/gtd_decision_board.py helpers and
lib/decision_board/{board.css,board.js}), so the page behaves identically:
choices persist in localStorage, Save downloads <slug>.decisions.json, and
nothing leaves the machine. The page is written to the private state dir.

Usage: python3 decisions_board.py [--open]
"""
import base64, hashlib, subprocess, sys
from datetime import datetime
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "lib"
sys.path.insert(0, str(LIB))
from gtd_decision_board import ASSETS, _json_block, _private_path, _build_id  # noqa: E402
from file_utils import atomic_write_text, private_state_directory  # noqa: E402

SLUG = "core-verification"


def o(value, label, consequence):
    return {"value": value, "label": label, "consequence": consequence}


def row(area, title, context, options, suggested, finding, preview=None):
    r = {"area": area, "title": title, "context": context, "options": options,
         "suggested": suggested, "meta": [f"findings/{finding}.md"]}
    if preview:
        r["preview"] = preview
    return r


KEEP = o("keep", "Keep as is", "No change; the behaviour stays documented in findings.")

SECTIONS = [
 ("ledger", "L", "Ledger", "Seals, dispatch, delegation, publication of the event log.", [
  row("Seals", "Accept only seals written by the designated sequencer?",
      "Any writer's seal that verifies and doesn't regress is accepted today, so `emit --force` from any host can become the latest seal. Every existing seal is by winston.",
      [o("sequencer", "Sequencer only", "Readers ignore seals not written by DATACORE_SEQUENCER (default winston); forced seals from other writers become inert."),
       KEEP], "sequencer", "ledger-seal"),
  row("Invariants", "When ledger_invariants can only answer 'could not tell', stop saying SOUND?",
      "An unreadable space yields only unknown findings, and the run prints SOUND with exit 0, which passes the nightly check.",
      [o("exit2", "Print UNKNOWN, exit 2", "The nightly check fails and says the run could not judge; one existing test is updated."),
       o("exit1", "Print UNKNOWN, exit 1", "exit_ok [0,1] still passes, but the SOUND-line check flags it."),
       KEEP], "exit2", "ledger-seal"),
  row("Ingest", "Count nested archive files as evidence for dismissing an item?",
      "Only top-level *archive*.org files count. Dismissal is terminal, so wider evidence also widens the blast radius of a wrong match.",
      [o("toplevel", "Top-level only", "Current rule; nested archives never dismiss anything."),
       o("nested", "Accept nested archives", "org/.archive/* and similar also count as proof an item was archived.")],
      "toplevel", "ledger-seal"),
  row("Dispatch", "How should two hosts of one principal be kept from running the same item?",
      "`addressed_to` accepts any writer of a principal, and the policy lock is per host, so miles and nightshift can both claim and both run the work; only one completion survives the fold.",
      [o("exact", "Require the exact writer", "Items addressed to a principal are claimed only by the writer named in assignee; others skip them."),
       o("pin", "One dispatcher per principal per space", "Configuration pins each principal's dispatch to one host."),
       o("accept", "Accept duplicate runs", "Keep today's behaviour; duplicate spend is the cost.")], "exact", "ledger-policy"),
  row("Delegation", "Export DATACORE_HOPS to executed tasks so adapter-created follow-ups inherit depth?",
      "chain_follow_up now enforces depth, but items an agent creates through the org adapter still start at hops 0.",
      [o("export", "Export parent hops + 1", "executors/base.py sets DATACORE_HOPS for every run; the hop limit covers adapter creates too."),
       KEEP], "export", "ledger-policy"),
  row("Delegation", "Is max_creates_per_day per principal per space, or across all spaces?",
      "It is counted per space today, so the real cap is N × spaces.",
      [o("perspace", "Per space (document it)", "Keep the count; state the per-space meaning in the policy file."),
       o("global", "Across all spaces", "Count creates in every space on this machine; slower, but a true daily cap.")],
      "perspace", "ledger-policy"),
  row("Edits", "Make three-way edit merging compare types strictly (so 1 → True is an edit)?",
      "Python treats True == 1 == 1.0, so such an edit is silently dropped. Changing it can change fold results and state roots of existing logs.",
      [o("strict", "Type-strict from now on", "New events compare strictly; needs a versioned protocol flag so history folds unchanged."),
       KEEP], "keep", "ledger-policy"),
  row("Edits", "Refuse NaN in newly appended event payloads?",
      "NaN never equals itself, so a dismissal of an item holding NaN always records a conflict.",
      [o("append", "Refuse at append", "EventLog.append rejects NaN; existing events keep hashing as before."),
       KEEP], "append", "ledger-policy"),
  row("Publication", "Gate ledger_transport and knowledge_commit pushes with the new fork check?",
      "The relay and the fleet sweep now refuse to push a forked ledger. These two pushers still send a clean merge of a rewritten log straight to origin. All ten spaces are fork-free today.",
      [o("gate", "Gate both", "Both call git_relay.publication_forks before pushing and refuse with a named recovery."),
       o("warn", "Warn only for a week, then gate", "Log what would be refused before turning it on."),
       KEEP], "gate", "git-fleet",
      preview="A refusal is correct but can stall publication; the message must name how to recover."),
  row("Identity", "Make ledger writers require a declared actor (this_actor(strict=True))?",
      "Two undeclared hosts sharing a short hostname still write one log and fork it.",
      [o("strict", "Require a declared actor", "A host with no identity.env or registry entry refuses to append instead of guessing."),
       KEEP], "strict", "git-fleet"),
 ]),
 ("gtd", "G", "GTD and org files", "State machine and the tools that edit org files.", [
  row("States", "Refuse state changes that DIP-0009 v2.0 forbids?",
      "The code allows 49 moves the spec forbids, for example REVIEW→TODO and DEFERRED→DONE. The decision board's 'someday' for a REVIEW row relies on REVIEW→TODO.",
      [o("enforce", "Enforce; board uses DEFERRED", "The adapter refuses illegal moves; the board's someday path for REVIEW rows becomes DEFERRED."),
       o("warn", "Warn only", "Illegal moves are logged but allowed."),
       KEEP], "enforce", "gtd-state"),
  row("States", "Allow DEFERRED → CANCELLED directly?",
      "The v2.0 table says DEFERRED can only wake to TODO; the older table allows cancelling it.",
      [o("allow", "Allow it", "Add DEFERRED→CANCELLED to the table; dropping deferred work needs no wake first."),
       o("wake", "Wake first", "Keep DEFERRED→TODO only.")], "allow", "gtd-state"),
  row("States", "Enforce 'only the owner may move a task out of REVIEW'?",
      "Nothing can enforce it today: a host identity covers the owner and agents alike, and nothing reads :OWNER:.",
      [o("later", "Not until principals exist", "Record it as a requirement for the identity work."),
       o("owner", "Enforce via :OWNER: + principal", "Needs a declared owner principal; agents lose REVIEW exits.")],
      "later", "gtd-state"),
  row("Ids", "Should task_cleanup leave shared ids in generated next_actions.org alone?",
      "inbox.org and the generated next_actions.org share ids by design; reassigning them breaks the projection.",
      [o("skip", "Skip generated pairs", "dup-ids never rewrites ids in a generated file or its source pair."),
       KEEP], "skip", "gtd-state"),
  row("Ids", "Fix org_workspace's default states in its own repo?",
      "Its default still includes retired QUEUED/WORKING/FAILED and puts DEFERRED in the todo class.",
      [o("fix", "Fix and release", "Drop retired states and move DEFERRED to the done class in org-workspace; release a patch."),
       KEEP], "fix", "gtd-state"),
  row("Ledger", "Make nightshift_parser close items in the ledger the way the adapter now does?",
      "DONE/CANCELLED on nightshift.org sends only item.update, so the ledger item stays open — the defect already fixed in the adapter.",
      [o("reuse", "Reuse _ledger_emit_close", "Same close semantics everywhere."), KEEP], "reuse", "gtd-state"),
  row("Writers", "Require a file to be read (watched) before org_transaction overwrites it?",
      "Otherwise the stale-write check is vacuous for a first write. Most callers already watch first; ledger_project_org.py writes blind.",
      [o("doc", "Document; fix the blind caller", "Keep the API; make ledger_project_org watch first."),
       o("enforce", "Enforce in write_org_text", "Unwatched overwrites are refused; some tests change.")], "doc", "org-transaction"),
  row("Writers", "Route the live date hook and triage_utils through the org transaction lock?",
      "Both write org files without the lock. A lost adapter commit was replayed with the real hook.",
      [o("live", "Live writers now, manual tools later", "org_date_hook and triage_utils take the lock and write atomically."),
       o("all", "All bypassing writers now", "Also org_union_merge, org_dedup_within_file, org_resolve_id_conflicts, inbox_dedup, stamp_seq_todo, validate_org_dates."),
       KEEP], "live", "org-transaction"),
  row("Dedup", "When two copies of a subtree differ only in :ID:, may dedup delete one?",
      "Ignoring :ID: is deliberate (2026-08-15), but the ledger may track both ids as separate items.",
      [o("refuse", "Refuse when ids differ", "Both copies stay; the report names them for a human."),
       o("dismiss", "Delete and dismiss the dropped id", "Removes the copy and closes its ledger item as housekeeping."),
       KEEP], "dismiss", "org-tools"),
  row("Dedup", "When an id conflict is resolved, what happens to the discarded id's ledger item?",
      "If the discarded id already has an item.create, it becomes an orphaned live item.",
      [o("dismiss", "Dismiss as housekeeping", "item.dismiss kind=housekeeping, reason names the kept id."),
       o("manual", "Report for manual reconcile", "Resolver prints the id; nothing is emitted.")], "dismiss", "org-tools"),
  row("Triage", "Stop appending triage context to DEFERRED tasks?",
      "DONE and CANCELLED are skipped; DEFERRED still gets appended to.",
      [o("skip", "Skip DEFERRED too", "Deferred tasks are left untouched until woken."), KEEP], "skip", "org-tools"),
  row("Merge", "If a merged file skips a heading level, may the union merge place the item under the nearest earlier sibling?",
      "The parent guarantee is proved only for exactly-one-level children.",
      [o("place", "Place under nearest sibling", "Current fixed behaviour; no item lost, parent approximated."),
       o("refuse", "Refuse the merge", "Such files need a human merge.")], "place", "org-tools"),
 ]),
 ("nightshift", "N", "Nightshift", "Task lifecycle, retries, gates and budgets.", [
  row("Lifecycle", "Escalate tasks stuck NEXT behind a closed attempt fence?",
      "A timeout, crash or post-run write failure leaves the fence closed; the task sits NEXT forever and reconcile_attempt refuses to reopen it.",
      [o("escalate", "Escalate to REVIEW + add --reopen", "complete_task escalates when the fence is blocked; reconcile_attempt --reopen resets fence, attempts and state."),
       KEEP], "escalate", "nightshift-lifecycle"),
  row("Lifecycle", "Reset NIGHTSHIFT_REQUEUES on success or a human reopen?",
      "Today it is a lifetime cap of 2.",
      [o("reset", "Reset on success / reopen", "The cap counts consecutive stalls only."), KEEP], "reset", "nightshift-lifecycle"),
  row("Retries", "What should max_retries mean?",
      "The code allows at most one retry per run and escalates at a hard-coded 2; the docs say 'revision attempts before human review'.",
      [o("threshold", "Escalation threshold", "Replace the literal 2 with max_retries."),
       o("inrun", "In-run retry count", "Loop up to max_retries within a run."),
       o("retire", "Retire the setting", "Remove it from settings and docs.")], "threshold", "nightshift-lifecycle"),
  row("Evaluator", "Use the configured quality_threshold and honour a 'reject' recommendation?",
      "0.80 in settings is ignored; bands are hard-coded at 0.70/0.85, and a reject recommendation cannot block an approval.",
      [o("both", "Threshold + reject blocks", "Approval needs mean ≥ quality_threshold and no reject."),
       o("threshold", "Threshold only", "Read the setting; ignore recommendations."), KEEP], "both", "nightshift-gates"),
  row("Canary", "Should a stuck 'blocked' delegation canary eventually fail?",
      "A failing local commit writes 'blocked', exits 0 and passes every day; a test pins 'blocked 99h still passes'.",
      [o("age", "Commit failure = failed; blocked ages out after 48h", "The canary can no longer pass forever."),
       KEEP], "age", "nightshift-gates"),
  row("Budget", "Refuse batch-API spend once a non-zero daily budget is exhausted?",
      "Budget is reporting-only; no effect today because budget_daily_usd is 0 (unlimited).",
      [o("enforce", "Enforce when > 0", "run.py/execute.py stop batch calls past the budget."), KEEP], "enforce", "nightshift-gates"),
  row("Verification", "When the recurrence counter can't lock or save, what should happen?",
      "It silently loses a reset today, so one later failure reads as recurring.",
      [o("warn", "Warn loudly", "Verification continues; the lost reset is reported."),
       o("refuse", "Refuse the run", "No verdict without a durable counter."), KEEP], "warn", "nightshift-gates"),
  row("Gate", "Make the :AI: write-gate and the executor agree on ROADMAP?",
      "The gate requires ROADMAP in roadmap spaces; the executor doesn't, though the gate claims to mirror it exactly.",
      [o("executor", "Executor requires it too", "Both refuse the same tasks."),
       o("drop", "Drop it from the gate", "Both accept tasks without ROADMAP."), KEEP], "executor", "nightshift-gates"),
 ]),
 ("creds", "C", "Credentials and routing", "Broker behaviour and the model privacy floor.", [
  row("Broker", "What should `creds get` do when a value can't be verified (n-a)?",
      "It serves with exit 0 and says n-a on stderr; `--strict` now exists. Refusing by default breaks X=$(creds get …) under set -e for every NO_PROBE credential.",
      [o("keep", "Serve, warn on stderr; --strict opt-in", "Today's fixed behaviour."),
       o("strict", "Strict by default", "n-a exits 3; callers must pass --lenient."),
       o("code", "Serve with a distinct exit code", "Value printed, exit 4 for 'unverified'.")], "keep", "credentials"),
  row("Broker", "Which value wins when a key appears twice in one env file?",
      "The broker reads the first; config_plane and shell `source` take the last; env_utils raises.",
      [o("last", "Last wins everywhere; doctor warns", "Matches shell semantics."),
       o("raise", "Refuse everywhere", "Duplicate keys are an error; fail loud.")], "last", "credentials"),
  row("Routing", "dmcc contract-review and client-report now route to local models. Intended?",
      "The venture floor (internal: never use public models) now beats escalation to a frontier model.",
      [o("local", "Keep local", "The floor wins, as written."),
       o("premium", "Add a private premium class", "Escalate to a stronger model that meets the floor."),
       o("lower", "Lower the floor", "Allow frontier for these two tasks.")], "local", "credentials"),
  row("Rollout", "Roll the new creds.py out to all hosts together?",
      "Its lock file name changed, so old and new versions don't exclude each other during a mixed rollout.",
      [o("together", "Together, one distribute run", "No window where two versions race."), KEEP], "together", "credentials"),
  row("Rollout", "Check other hosts for keys defined in both .env and local.env before rolling out host-wins?",
      "On the mac no key is in both, so nothing changed here; other hosts are unchecked.",
      [o("check", "Compare by truncated hash first", "No value printed; any overlap is listed for review."),
       o("go", "Roll out now", "Accept whatever local.env says on each host.")], "check", "credentials"),
 ]),
 ("guards", "S", "Guards", "Hooks that stand between agents and harm.", [
  row("Tool policy", "Always match tool-call patterns against the whole input JSON?",
      "When a known text key is present, other fields are never matched, so an MCP call can hide an effect in another field.",
      [o("json", "Add the JSON always", "Patterns see every field; one test and the hermes authorship text change."), KEEP], "json", "guards"),
  row("Injection gate", "While the injection gate is armed, allow only read-only commands on the spill file?",
      "Bash is fully allowed while the gate is armed, including curl and git push.",
      [o("readonly", "Read-only on the spill file", "cat/head/sed/grep on that file pass; everything else waits."),
       KEEP], "readonly", "guards"),
  row("Log ownership", "Treat commits that no remote contains as this machine's, whatever the author email?",
      "The author filter can be forged with -c user.email.",
      [o("unpushed", "Judge by 'not on any remote'", "Forged authors no longer bypass the check."), KEEP], "unpushed", "guards"),
  row("Log ownership", "Refuse the push when git rev-list can't list the range?",
      "It now warns 'NOT checked' and lets the push through.",
      [o("refuse", "Refuse", "Unknown means blocked."), KEEP], "refuse", "guards"),
  row("Egress", "Give an all-n-a egress runtime check its own exit code?",
      "Exit 0 today; the only caller parses stdout correctly.",
      [o("code", "Exit 3 when nothing could be checked", "Callers can tell 'clean' from 'unknown'."), KEEP], "code", "guards"),
  row("Hooks", "Fail validation on an unknown hook type, and stop the CLI writing mock errors to real state?",
      "Unknown types are skipped (validation passes), and `python hooks.py <agent>` writes a mock error into hook_state.yaml.",
      [o("both", "Both", "Unknown type fails; the CLI writes to a temp state."), KEEP], "both", "guards"),
  row("Restricted hosts", "Keep refusing network git after `cd $VAR` (directory unknown until run time)?",
      "The fixed guard fails closed, as its docstring says; loops like `for d in */; do (cd $d && git pull); done` are refused.",
      [o("keep", "Keep refusing", "Unknown directory = not provably safe."),
       o("relax", "Allow when no restricted host is configured anywhere", "Refuse only if a restricted host could be reached.")],
      "keep", "guards"),
 ]),
 ("sync", "P", "Sync, publication, reconcile", "Pushes, write-back and GitHub reconciliation.", [
  row("Fleet sync", "Take the repo lock around git_fleet_sync's commit?",
      "Without it a sweep commit can move the branch under a publication — the race behind the 2026-09-16 stranded record.",
      [o("lock", "Take _repo_lock", "Sweep and publication serialize."), KEEP], "lock", "publication"),
  row("Fleet sync", "Refuse to push a range whose earlier commits delete tracked files?",
      "The sweep's own commit never deletes, but it publishes everything since origin.",
      [o("refuse", "Refuse and name the commits", "Deletions need a human push."),
       o("warn", "Warn only", "Push, but report the deletions."), KEEP], "refuse", "git-fleet"),
  row("Write-back", "After a crash and a revert to the original bytes, should a pending write re-apply?",
      "It re-applies today (ABA). Failing closed needs a durable 'applying' mark and changes a pinned test.",
      [KEEP, o("close", "Fail closed", "A retry after that sequence becomes a conflict.")], "keep", "publication"),
  row("GC", "Treat workspaces anchored under publication-captures refs as recoverable?",
      "They are retained until someone inspects them.",
      [o("retain", "Keep retaining", "Conservative; manual cleanup."),
       o("reclaim", "Reclaim when the capture ref holds the tree", "Frees space; the ref keeps the content.")], "retain", "publication"),
  row("GitHub", "Should an archived nightshift output close a task whose PR is still open?",
      "Path B closes it DONE even when the tracked PR is open or its lookup failed.",
      [o("require", "Require refs terminal too", "Archive alone no longer closes a task with a live ref."), KEEP], "require", "reconcile"),
  row("GitHub", "What should an issue closed as duplicate do to its task?",
      "It now leaves the task open (previously DONE).",
      [o("cancel", "CANCELLED with reason", "The task closes as dropped, naming the duplicate."),
       o("follow", "Follow the canonical issue", "The task mirrors the issue it duplicates."),
       KEEP], "cancel", "reconcile"),
  row("Task sync", "Three-way task sync: build the base snapshot, or remove the detector for now?",
      "The two-way detector would reopen human-closed issues; the sync engine is a stub, so this is dormant.",
      [o("remove", "Remove until the engine exists", "Keep the Lean spec (resolve3) for when it's built."),
       o("snapshot", "Add a per-task base snapshot now", "Changes the sync state DB format.")], "remove", "reconcile"),
 ]),
 ("rest", "D", "Detectors, knowledge, research", "Alarms, context layers and the research queue.", [
  row("Id churn", "Run `id_churn.py --acknowledge` once on each host so the set-based check takes effect?",
      "Until then the old count-based check stays and says so.",
      [o("run", "Run once per host", "New churn is judged by id, not count."), KEEP], "run", "detectors"),
  row("Id churn", "Keep the 25% noise floor now that the baseline is a set of ids?",
      "Churn below 25% of live ids is still never reported.",
      [o("keep", "Keep", "Fewer alerts."), o("drop", "Drop it", "Any new churned id is reported.")], "drop", "detectors"),
  row("SLO", "Should one missed probe in a day fail R5 (≥99.5%)?",
      "95/96 now fails, as the target says; before, one miss was always forgiven.",
      [o("strict", "Yes, as written", "The SLO page stays correct."),
       o("allow", "State a one-miss allowance", "Change the SLO page and the check together.")], "strict", "detectors"),
  row("Presence", "Add an explicit acknowledge flag for MISSING/STALLED actors?",
      "They now clear only when the log recovers or state is reset.",
      [o("ack", "Add --acknowledge", "A human can accept a retired actor."), KEEP], "ack", "detectors"),
  row("Context", "Strike 'Override values' from DIP-0002's merge behaviour?",
      "Its Resolved Question 1 says layers only concatenate, which is what the code does.",
      [o("strike", "Strike it", "DIP matches code."), KEEP], "strike", "knowledge"),
  row("Context", "Extend the 'composed file must be gitignored' guard to SPACE/TEAM layers in public repos?",
      "Only the PRIVATE layer is enforced.",
      [o("extend", "Extend to public repos", "Team content never lands in a public composed file."), KEEP], "extend", "knowledge"),
  row("Learning", "Should 'candidate' engrams count as promoted when pruning the learning buffer?",
      "Only active (or unstatused) engrams count now.",
      [o("no", "No", "Candidates can still be rejected; keep the entry."), o("yes", "Yes", "Prune entries matched by a candidate.")],
      "no", "knowledge"),
  row("Research", "During an outage where every analysis fails, should those failures count toward parking?",
      "As fixed, 3 nights of outage park the top items as WAITING and a human must reset them.",
      [o("partial", "Count only if something succeeded that run", "Outages don't park; the drain proof then assumes the model eventually answers."),
       o("always", "Always count", "Guaranteed drain; outages park items."), ], "partial", "research"),
  row("Research", "Move attempt counters and :RESULT: into the properties drawer?",
      "They sit under the heading today, invisible to org-workspace get_property.",
      [o("move", "Move with a migration", "Readers see them as properties."), KEEP], "move", "research"),
  row("Dates", "Widen the 'suspect year' check beyond last year?",
      "It flags last-year dates as likely typos; any earlier year would also flag old completed tasks.",
      [o("keep", "Last year only", "Current meaning of the old literal."), o("any", "Any earlier year", "More flags, more noise.")],
      "keep", "dates"),
 ]),
]


def render(slug, title, eyebrow, lede, path, sources, section_defs, noted=None, open_page=False):
    """Validate rows, embed data, and write the board page privately."""
    sections = []
    for key, prefix, label, hint, rows in section_defs:
        for i, r in enumerate(rows, 1):
            r["id"] = f"{prefix}{i}"
            assert r["suggested"] in {x["value"] for x in r["options"]}, r["title"]
            assert 2 <= len(r["options"]) <= 4, r["title"]
        sections.append({"key": key, "label": label, "hint": hint, "rows": rows, "noted": []})
    if noted:
        sections[-1]["noted"] = [{"text": t} for t in noted]
    total = sum(len(s["rows"]) for s in sections)
    meta = {"slug": slug, "title": title, "eyebrow": eyebrow, "h1": title, "lede": lede,
            "asOf": datetime.now().strftime("%Y-%m-%d %H:%M"), "sources": sources,
            "path": [list(p) for p in path] + [["Your calls", f"{total} decisions"],
                                              ["Applied", "after you say “apply the decisions”"]]}
    data = {"meta": meta, "sections": sections, "prefill": {}}
    meta["build"] = _build_id(data)
    css = (ASSETS / "board.css").read_text()
    js = (ASSETS / "board.js").read_text()
    assert "</script" not in js.lower()
    script_hash = base64.b64encode(hashlib.sha256(js.encode()).digest()).decode()
    csp = (f"default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; "
           "base-uri 'none'; form-action 'none'")
    page = ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f'<meta http-equiv="Content-Security-Policy" content="{csp}"><title>{title}</title>'
            f"<style>{css}</style></head><body><div id=\"app\"></div>"
            f"{_json_block('data', data)}<script>{js}</script></body></html>\n")
    out = _private_path(private_state_directory("decision-boards") / f"{datetime.now():%Y-%m-%d}-{slug}.html",
                        create_parent=True)
    atomic_write_text(out, page)
    print(out, total, "decisions")
    if open_page:
        subprocess.run(["open", str(out)], check=False)
    return out


def main():
    render(SLUG, "Core verification decisions", "Datacore core · Lean verification · 2026-09-23",
           "Places where code, docs and tests disagree on policy. Each has a suggestion; choose, add a note where it helps, and save. Nothing changes until you say “apply the decisions”.",
           [("Verified", "17 models · 1,979 theorems"), ("Fixed", "~100 defects")],
           ["specs/datacore-lean/DECISIONS.md", "specs/datacore-lean/findings/"], SECTIONS,
           noted=["About 100 defects were already fixed and verified; they are in findings/ and README.md, not here."],
           open_page="--open" in sys.argv)


if __name__ == "__main__":
    main()
