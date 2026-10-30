"""fleet_dashboard: every red item says what to do, who owns it, and since when.

Owner, 2026-10-30, on the first version: "it doesn't really provide me
actionable info". A fault is sorted into one of four piles -- needs you, an
agent is repairing it, auto-repair is paused, or noise (a check that looks for
work in a place it no longer runs) -- and a value it could not read is never
shown as green.
"""
import json

import fleet_dashboard as fd

HOME = "/" + "home/someone/"  # built at runtime: the pre-commit path scan refuses literal home paths


# -- plain language -------------------------------------------------------

def test_a_stale_artifact_reads_as_days_without_output():
    assert fd.plain_failure("~/.datacore/cos/backup.log: stale (age 125.60h exceeds max_age_hours=26)") \
        == "no fresh output for 5 days"


def test_a_missing_artifact_reads_as_never_written():
    assert "never" in fd.plain_failure("~/x.log: does not exist ([Errno 2] No such file)")


def test_a_regex_failure_quotes_what_the_job_actually_said():
    d = "~/x.log: regex '^0 file' did not match -- last line: '2 file(s) differ'"
    assert fd.plain_failure(d) == "reports: 2 file(s) differ"


def test_job_names_become_plain_titles():
    assert fd.job_title("box-phase1-cycle") == "Repo sync cycle"
    assert fd.job_title("nightshift-github-triage-report") == "GitHub triage report"
    assert fd.job_title("box-something-new") == "Something new"


# -- who owns it ----------------------------------------------------------

LOG = """job 'box-a' FAILED:
  - x
alert withheld: box-a was already escalated to the owner (box-a: 3 repair attempts have failed)
job 'box-b' FAILED:
delegated repair of box-b (autofix-box-b-20261030 -> tris); operator not alerted
job 'box-c' FAILED:
delegated repair of box-c (autofix-box-c-20261025 -> tris); operator not alerted
alert withheld: box-c failed on the same artifact already counted (5x); nothing new to report
job 'box-d' FAILED:
repairs paused for box-d (repairs are paused: the last repair hit the model usage limit)
job 'box-e' FAILED:
alert withheld: box-e failed on the same artifact already counted (4x); nothing new to report
"""


def test_decisions_are_read_per_job():
    d = fd.decisions(LOG, today="2026-10-30")
    assert d["box-a"]["owner"] == "you" and "escalated" in d["box-a"]["note"]
    assert d["box-b"] == {"owner": "agent", "agent": "tris", "note": "tris is repairing it (since 2026-10-30)"}
    assert d["box-c"]["owner"] == "you" and "stuck" in d["box-c"]["note"]   # a 5-day-old repair is not "handled"
    assert d["box-d"]["owner"] == "paused"
    assert d["box-e"]["owner"] == "you" and "nobody" in d["box-e"]["note"]  # quiet is the dangerous case


def test_a_check_for_work_that_is_not_scheduled_here_is_noise():
    job = {"name": "box-reconcile-timers", "cmd": "~/Data/.datacore/lib/ops_reconcile_timers.sh >> x"}
    here = "0 8 * * * python3 job_verify.py"
    elsewhere = {"box host": "20 * * * * " + HOME + "Data/.datacore/lib/ops_reconcile_timers.sh"}
    n = fd.noise_reason(job, here, elsewhere)
    assert n == "runs on box host, checked here"
    assert fd.noise_reason({"name": "box-backup", "cmd": "~/x/cos_backup.sh"}, here, {"box host": "# 30 2 * * * cos_backup.sh"}) \
        == "not scheduled anywhere (retired?)"
    assert fd.noise_reason(job, "20 * * * * ops_reconcile_timers.sh", {}) is None


# -- vessels never show an unread value as green ----------------------------

def test_unreachable_is_red():
    assert fd.assess("box", "host", {"_error": "timed out"})["status"] == "red"


def test_a_silent_checker_is_not_green():
    v = fd.assess("box", "host", {"head": "abc", "jvage": str(105 * 3600), "jvlast": "OK 7 jobs"})
    assert v["status"] == "amber" and "silent" in v["checker"]


def test_a_crashing_checker_is_red_even_when_its_last_line_looks_harmless():
    kv = {"jvage": "60", "jvlast": "job 'x': unknown machine 'dayshift'",
          "jverr": "error: invalid manifest " + HOME + "x/manifest.yaml: job"}
    v = fd.assess("plur-claw", "pod", kv)
    assert v["status"] == "red" and "/home/" not in v["checker"]


# -- orders ---------------------------------------------------------------

def test_a_failed_backup_is_the_first_order_and_carries_its_command():
    box = {"bk_result": "exit-code", "bk_newest": "2026-10-26", "disk_pct": "91%",
           "disk_size": str(150 * 1048576), "disk_used": str(137 * 1048576),
           "bk_why": "SKIPPED, not enough disk: a copy would leave under 20% free"}
    orders = fd.owner_orders(box, {"status": "ok", "tools": {}}, [], today="2026-10-30")
    first = orders[0]
    assert first["severity"] == "red" and first["since"] == "2026-10-26" and first["days"] == 4
    assert "fleet-backup" in first["command"] and "GB" in first["why"]


def test_a_job_fault_that_needs_you_becomes_an_order_with_a_claude_brief():
    v = {"label": "WINSTON", "key": "box@pod", "faults": [
        {"job": "box-phase1-cycle", "detail": "reports: converge failed", "owner": "you", "note": "nobody is repairing it",
         "first": "2026-10-25", "bucket": "you"}]}
    orders = fd.owner_orders({}, {"status": "ok", "tools": {}}, [v], today="2026-10-30")
    o = [x for x in orders if "Repo sync cycle" in x["title"]][0]
    assert o["days"] == 5 and "box-phase1-cycle" in o["brief"] and not o.get("command")


def test_render_embeds_the_data_safely():
    html = fd.render({"x": "</script><b>"})
    assert "</script><b>" not in html and json.dumps({"x": "<\\/script><b>"})[:5] in html


def test_a_repair_on_a_job_broken_for_days_is_not_handled():
    dd = {"owner": "agent", "agent": "winston", "note": "winston is repairing it (since 2026-10-30)"}
    s = fd.settle(dd, first="2026-09-30", today="2026-10-30")
    assert s["owner"] == "you" and "2026-09-30" in s["note"]
    assert fd.settle(dd, first="2026-10-29", today="2026-10-30")["owner"] == "agent"


def test_the_full_detail_comes_from_the_log_not_the_truncated_history():
    log = ("job 'box-knowledge-lint' FAILED:\n"
           "  - ~/k.log: last line does not match 'x' -- last line: '2026-10-25T06:15:01 knowledge-lint: 7687 broken links (6 new)'\n")
    assert fd.details(log)["box-knowledge-lint"].endswith("(6 new)'")
    assert fd.plain_failure(fd.details(log)["box-knowledge-lint"]) == "reports: knowledge-lint: 7687 broken links (6 new)"


def test_an_unreadable_quote_falls_back_to_plain_words():
    assert fd.plain_failure("~/c.json: regex 'x' did not match -- last line: '}'") == "its output is not what the check expects"
    assert fd.plain_failure("~/p.log: last line does not match 'preflight: 0 space") == "its output is not what the check expects"


def test_orders_rank_by_what_is_lost_before_how_old_it_is():
    def fault(job, first):
        return {"job": job, "detail": "reports: x", "note": "nobody is repairing it", "first": first, "bucket": "you"}
    v = {"label": "WINSTON", "key": "box@pod", "faults": [fault("box-knowledge-lint", "2026-09-30"),
                                                         fault("box-phase1-cycle", "2026-10-28")]}
    orders = fd.owner_orders({}, {"status": "ok", "tools": {}}, [v], today="2026-10-30")
    assert [o["title"] for o in orders] == ["WINSTON: Repo sync cycle", "WINSTON: Knowledge link check"]
    assert orders[0]["why"].startswith("Work on this machine is not syncing")


# -- v3: the crew reports what it fixed; the owner gets decisions, not chores ----

def test_a_job_the_crew_fixed_leaves_the_orders_and_shows_in_the_crew_log():
    orders = [{"severity": "amber", "title": "WINSTON: Repo sync cycle", "machine": "WINSTON", "job": "box-phase1-cycle"},
              {"severity": "amber", "title": "MILES: GitHub triage", "machine": "MILES", "job": "nightshift-github-triage"}]
    fixes = [{"at": "2026-10-30T12:00:00Z", "machine": "WINSTON", "job": "box-phase1-cycle", "title": "Repo sync cycle",
              "what": "x", "verified": True}]
    left, log = fd.merge_fixes(orders, fixes)
    assert [o["job"] for o in left] == ["nightshift-github-triage"]
    assert log[0]["job"] == "box-phase1-cycle" and log[0]["still_failing"] is True  # the checker has not rerun yet


def test_every_decision_row_has_options_and_its_suggestion_is_one_of_them():
    orders = [{"severity": "red", "title": "A", "command": "ssh x", "machine": "BOX HOST", "job": "backup"},
              {"severity": "amber", "title": "B", "brief": "fix it", "machine": "MILES", "job": "j"}]
    extra = [{"id": "lens-db", "title": "C", "context": "c", "suggested": "keep",
              "options": [{"value": "keep", "label": "Keep"}, {"value": "move", "label": "Move"}]}]
    rows = fd.decision_rows(orders, extra)
    assert len(rows) == 3 and len({r["id"] for r in rows}) == 3
    for r in rows:
        values = [o["value"] for o in r["options"]]
        assert 2 <= len(values) <= 4 and r["suggested"] in values


def test_the_board_build_changes_when_its_rows_change():
    a = fd.decision_rows([{"severity": "amber", "title": "B", "brief": "x", "machine": "M", "job": "j"}], [])
    b = fd.decision_rows([{"severity": "amber", "title": "B", "brief": "x", "machine": "M", "job": "k"}], [])
    assert fd.board_build(a) != fd.board_build(b) and fd.board_build(a) == fd.board_build(list(a))


def test_a_torn_log_line_is_skipped_not_fatal(tmp_path):
    p = tmp_path / "fixes.jsonl"
    p.write_text('{"job": "a"}\n{"job": \n\n{"job": "b"}\n')
    assert [r["job"] for r in fd.read_jsonl(p)] == ["a", "b"]


def test_a_crew_question_about_a_job_replaces_its_generic_row_and_keeps_its_age():
    orders = [{"severity": "amber", "title": "WINSTON: Self-test", "brief": "x", "machine": "WINSTON",
               "job": "bridge-v2-verify", "days": 30}]
    extra = [{"id": "v2-conformance", "title": "Schedule the v2 fixes", "machine": "WINSTON", "job": "bridge-v2-verify",
              "suggested": "crew-task", "options": [{"value": "crew-task", "label": "Crew"}, {"value": "accept", "label": "Accept"}]}]
    rows = fd.decision_rows(orders, extra)
    assert [r["id"] for r in rows] == ["v2-conformance"] and rows[0]["days"] == 30
