"""The nightly promise scoreboard (promise_nightly.py): what it tells The Firm.

Owner decision 2026-09-30: run the promise scoreboard every night on the
overnight host and alert The Firm when a promise that was green turns red.

What these tests hold, each with a fake history of boards (no eval runs, no
Telegram):
  * turned red   -- green last night, red tonight: one message, plain promise
                    text, id in brackets, the first failure line
  * recovered    -- red last night, green tonight: a short "recovered" line
  * unchanged    -- nothing moved: no message at all; a red that stays red is
                    not repeated, only listed in the Monday summary
  * could not run here -- a red whose failing tests all lack a need this host
                    does not have (test-needs.yaml), or that could not reach a
                    host, or has no eval file here, is "could not run here",
                    never a regression
  * the baseline -- .datacore/registry/promise-baseline.json is never written
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import promise_nightly as pn  # noqa: E402

TEXTS = {
    "CAP-4": "A capture made twice lands once",
    "DAY-2": "The morning briefing is ready before I wake",
    "MSG-8": "A broken morning job is repaired before I wake",
    "SYN-4": "Every machine is on main",
    "NEW-1": "A promise that was never green",
}
WED = "2026-09-30"   # a Wednesday: no weekly summary
MON = "2026-10-05"   # a Monday: the weekly summary


def raw(states: dict[str, str], failures: dict | None = None) -> dict:
    """promise_evals.py --json output."""
    return {"counts": {}, "promises": states, "failures": failures or {}}


def fail(file: str, test: str, line: str) -> dict:
    return {"file": file, "test": test, "line": line}


def board(date: str, states: dict[str, str], failures=None, unmet=None) -> dict:
    return pn.build_board(raw(states, failures), unmet or {}, TEXTS, date=date, host="testhost")


def night(tmp_path, monkeypatch, history: list[dict], tonight: dict, *, date=WED,
          unmet=None, baseline=("CAP4", "DAY2", "MSG8", "SYN4")):
    """Run main() once with `history` already on disk; return (sent texts, rc, log)."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    for b in history:
        (state / f"board-{b['date']}.json").write_text(json.dumps(b))
    base = tmp_path / "promise-baseline.json"
    base.write_text(json.dumps({"green": list(baseline)}) + "\n")
    sent: list[str] = []
    monkeypatch.setattr(pn, "STATE_DIR", state)
    monkeypatch.setattr(pn, "BASELINE", base)
    monkeypatch.setattr(pn, "run_board", lambda: tonight)
    monkeypatch.setattr(pn, "unmet_needs", lambda: unmet or {})
    monkeypatch.setattr(pn, "promise_texts", lambda: TEXTS)
    monkeypatch.setattr(pn, "send_to_firm", lambda text: (sent.append(text) or (True, "sent")))
    monkeypatch.setattr(pn, "today", lambda: date)
    rc = pn.main([])
    return sent, rc, state


# ---- turned red --------------------------------------------------------------

def test_a_green_promise_that_turns_red_is_alerted_once_with_its_first_failure(tmp_path, monkeypatch, capsys):
    hist = [board("2026-09-29", {"CAP-4": "green", "DAY-2": "green"})]
    tonight = raw({"CAP-4": "red", "DAY-2": "green"}, {"CAP-4": [
        fail(".datacore/lib/tests/test_promise_CAP_4_capture_twice_one_entry.py", "test_twice",
             "AssertionError: two entries in the inbox")]})
    sent, rc, state = night(tmp_path, monkeypatch, hist, tonight)

    assert rc == 0
    assert len(sent) == 1
    msg = sent[0]
    assert "A capture made twice lands once (CAP-4)" in msg
    assert "AssertionError: two entries in the inbox" in msg
    assert "DAY-2" not in msg                                   # green stayed green: not mentioned
    assert (state / "board-2026-09-30.json").exists()
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("promise-scoreboard: ") and "1 turned red" in last and last.endswith("alert sent")


def test_the_same_red_the_next_night_is_not_alerted_again(tmp_path, monkeypatch):
    f = {"CAP-4": [fail("t.py", "test_twice", "AssertionError: two entries")]}
    hist = [board("2026-09-28", {"CAP-4": "green"}), board("2026-09-29", {"CAP-4": "red"}, f)]
    sent, rc, _ = night(tmp_path, monkeypatch, hist, raw({"CAP-4": "red"}, f))
    assert rc == 0 and sent == []


def test_the_first_night_compares_with_the_owners_baseline(tmp_path, monkeypatch):
    tonight = raw({"CAP-4": "red", "NEW-1": "red"},
                  {"CAP-4": [fail("t.py", "t", "boom")], "NEW-1": [fail("u.py", "u", "never green")]})
    sent, _, _ = night(tmp_path, monkeypatch, [], tonight)
    assert len(sent) == 1
    assert "(CAP-4)" in sent[0]                 # green in the baseline, red tonight
    assert "NEW-1" not in sent[0]               # never green: not a regression


def test_a_red_behind_a_night_that_could_not_run_still_counts_as_turned_red(tmp_path, monkeypatch):
    hist = [board("2026-09-27", {"CAP-4": "green"}),
            board("2026-09-28", {"CAP-4": "red"}, {"CAP-4": [fail("t.py", "t", "x")]},
                  unmet={"t.py": ["fleet"]})]          # could not run that night
    sent, _, _ = night(tmp_path, monkeypatch, hist, raw({"CAP-4": "red"}, {"CAP-4": [fail("t.py", "t", "real")]}))
    assert len(sent) == 1 and "(CAP-4)" in sent[0] and "real" in sent[0]


# ---- recovered ---------------------------------------------------------------

def test_a_red_promise_that_turns_green_gets_a_short_recovered_line(tmp_path, monkeypatch):
    hist = [board("2026-09-29", {"CAP-4": "red", "DAY-2": "red"},
                  {"CAP-4": [fail("t.py", "t", "x")], "DAY-2": [fail("d.py", "d", "y")]})]
    sent, rc, _ = night(tmp_path, monkeypatch, hist, raw({"CAP-4": "green", "DAY-2": "red"},
                                                          {"DAY-2": [fail("d.py", "d", "y")]}))
    assert rc == 0 and len(sent) == 1
    msg = sent[0].lower()
    assert "recovered" in msg and "(cap-4)" in msg
    assert "day-2" not in msg                   # still red, already mentioned: not repeated
    changes = pn.changes(pn.load_history(tmp_path / "state", WED), board(WED, {"CAP-4": "green", "DAY-2": "red"}),
                         set())
    assert changes["turned_red"] == []


# ---- unchanged ---------------------------------------------------------------

def test_nothing_changed_sends_nothing(tmp_path, monkeypatch, capsys):
    f = {"NEW-1": [fail("u.py", "u", "x")]}
    hist = [board("2026-09-29", {"CAP-4": "green", "NEW-1": "red"}, f)]
    sent, rc, state = night(tmp_path, monkeypatch, hist, raw({"CAP-4": "green", "NEW-1": "red"}, f))
    assert rc == 0 and sent == []
    assert (state / "board-2026-09-30.json").exists()          # the board is still written
    assert capsys.readouterr().out.strip().splitlines()[-1].endswith("alert none")


# ---- could not run here --------------------------------------------------------

def test_a_red_whose_failures_all_lack_a_need_here_could_not_run_and_is_not_a_regression(tmp_path, monkeypatch):
    hist = [board("2026-09-29", {"MSG-8": "green"})]
    f = {"MSG-8": [fail(".datacore/lib/tests/test_promise_MSG8_x.py", "test_repair", "principals.yaml missing")]}
    unmet = {".datacore/lib/tests/test_promise_MSG8_x.py": ["file:.datacore/registry/principals.yaml"]}
    sent, rc, state = night(tmp_path, monkeypatch, hist, raw({"MSG-8": "red"}, f), unmet=unmet)

    b = json.loads((state / "board-2026-09-30.json").read_text())
    assert b["promises"]["MSG-8"]["state"] == "could-not-run"
    assert "file:.datacore/registry/principals.yaml" in b["promises"]["MSG-8"]["why"]
    assert len(sent) == 1
    assert "could not run here" in sent[0].lower() and "(MSG-8)" in sent[0]
    assert "turned red" not in sent[0].lower()


def test_a_need_on_one_test_does_not_hide_another_failing_test_in_the_same_file(tmp_path, monkeypatch):
    hist = [board("2026-09-29", {"MSG-8": "green"})]
    path = ".datacore/lib/tests/test_promise_MSG8_x.py"
    f = {"MSG-8": [fail(path, "test_needs_registry", "no registry"), fail(path, "test_plain", "real failure")]}
    sent, _, _ = night(tmp_path, monkeypatch, hist, raw({"MSG-8": "red"}, f),
                       unmet={f"{path}::test_needs_registry": ["fleet"]})
    assert len(sent) == 1 and "turned red" in sent[0].lower() and "real failure" in sent[0]


def test_an_unreachable_host_is_could_not_run_not_a_regression(tmp_path, monkeypatch):
    hist = [board("2026-09-29", {"SYN-4": "green"})]
    f = {"SYN-4": [fail("s.py", "test_mac", "ssh: connect to host mac port 22: Connection timed out")]}
    sent, _, state = night(tmp_path, monkeypatch, hist, raw({"SYN-4": "red"}, f))
    b = json.loads((state / "board-2026-09-30.json").read_text())
    assert b["promises"]["SYN-4"]["state"] == "could-not-run"
    assert len(sent) == 1 and "could not run here" in sent[0].lower()


def test_no_eval_file_on_this_host_is_could_not_run(tmp_path, monkeypatch):
    hist = [board("2026-09-29", {"DAY-2": "green"})]
    sent, _, state = night(tmp_path, monkeypatch, hist, raw({"DAY-2": "no-eval"}))
    b = json.loads((state / "board-2026-09-30.json").read_text())
    assert b["promises"]["DAY-2"]["state"] == "could-not-run"
    assert len(sent) == 1 and "(DAY-2)" in sent[0] and "turned red" not in sent[0].lower()


def test_could_not_run_is_mentioned_once_not_every_night(tmp_path, monkeypatch):
    unmet = {"m.py": ["fleet"]}
    f = {"MSG-8": [fail("m.py", "t", "x")]}
    hist = [board("2026-09-28", {"MSG-8": "green"}), board("2026-09-29", {"MSG-8": "red"}, f, unmet)]
    sent, _, _ = night(tmp_path, monkeypatch, hist, raw({"MSG-8": "red"}, f), unmet=unmet)
    assert sent == []


# ---- weekly summary ----------------------------------------------------------

def test_a_red_that_stays_red_is_listed_in_the_monday_summary(tmp_path, monkeypatch):
    f = {"CAP-4": [fail("t.py", "t", "x")]}
    hist = [board("2026-10-02", {"CAP-4": "green"}), board("2026-10-03", {"CAP-4": "red"}, f),
            board("2026-10-04", {"CAP-4": "red"}, f)]
    sent, _, _ = night(tmp_path, monkeypatch, hist, raw({"CAP-4": "red"}, f), date=MON)
    assert len(sent) == 1
    assert "still red" in sent[0].lower() and "(CAP-4)" in sent[0]


def test_no_monday_summary_when_nothing_is_still_red(tmp_path, monkeypatch):
    hist = [board("2026-10-04", {"CAP-4": "green"})]
    sent, _, _ = night(tmp_path, monkeypatch, hist, raw({"CAP-4": "green"}), date=MON)
    assert sent == []


# ---- the baseline and the history --------------------------------------------

def test_the_baseline_is_never_written(tmp_path, monkeypatch):
    hist = [board("2026-09-29", {"CAP-4": "red"}, {"CAP-4": [fail("t.py", "t", "x")]})]
    base_before = None
    sent, _, _ = night(tmp_path, monkeypatch, hist, raw({"CAP-4": "green", "DAY-2": "green"}))
    base_before = (tmp_path / "promise-baseline.json").read_text()
    assert json.loads(base_before) == {"green": ["CAP4", "DAY2", "MSG8", "SYN4"]}
    cmd = pn.runner_command()
    assert "--write-baseline" not in cmd and "--agents" not in cmd and "--json" in cmd


def test_the_runner_never_turns_on_agent_evals(monkeypatch):
    monkeypatch.setenv("DATACORE_AGENT_EVALS", "1")
    assert "DATACORE_AGENT_EVALS" not in pn.runner_env()


def test_history_keeps_only_the_last_nights(tmp_path, monkeypatch):
    hist = [board(f"2026-09-{d:02d}", {"CAP-4": "green"}) for d in range(1, 30)]
    night(tmp_path, monkeypatch, hist, raw({"CAP-4": "green"}))
    kept = sorted(p.name for p in (tmp_path / "state").glob("board-*.json"))
    assert len(kept) == pn.KEEP
    assert kept[-1] == "board-2026-09-30.json"


def test_an_undelivered_alert_fails_the_run_and_says_so(tmp_path, monkeypatch, capsys):
    hist = [board("2026-09-29", {"CAP-4": "green"})]
    state = tmp_path / "state"
    state.mkdir()
    (state / "board-2026-09-29.json").write_text(json.dumps(hist[0]))
    base = tmp_path / "b.json"
    base.write_text('{"green": []}')
    monkeypatch.setattr(pn, "STATE_DIR", state)
    monkeypatch.setattr(pn, "BASELINE", base)
    monkeypatch.setattr(pn, "run_board", lambda: raw({"CAP-4": "red"}, {"CAP-4": [fail("t.py", "t", "x")]}))
    monkeypatch.setattr(pn, "unmet_needs", lambda: {})
    monkeypatch.setattr(pn, "promise_texts", lambda: TEXTS)
    monkeypatch.setattr(pn, "send_to_firm", lambda text: (False, "http 401"))
    monkeypatch.setattr(pn, "today", lambda: WED)
    assert pn.main([]) == 1
    assert "alert NOT delivered (http 401)" in capsys.readouterr().out.strip().splitlines()[-1]


def test_a_runner_that_fails_writes_no_board(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    monkeypatch.setattr(pn, "STATE_DIR", state)
    monkeypatch.setattr(pn, "today", lambda: WED)

    def broken():
        raise RuntimeError("promise_evals.py printed no JSON")
    monkeypatch.setattr(pn, "run_board", broken)
    assert pn.main([]) == 1
    assert not list(state.glob("board-*.json")) if state.exists() else True
    assert "FAILED" in capsys.readouterr().out.strip().splitlines()[-1]


# ---- the first failure line ----------------------------------------------------

def test_failure_details_name_the_failing_test_and_its_first_line(tmp_path):
    """promise_evals.py says red or green; the alert also needs WHY, so the red
    evals are run once more and each failing test's first line is kept."""
    f = tmp_path / "test_promise_ZZ1_fixture.py"
    f.write_text("def test_ok():\n    assert True\n\n"
                 "class TestGroup:\n    def test_bad(self):\n"
                 "        assert 1 == 2, 'boom: the capture landed twice'\n")
    details = pn.failure_details(tmp_path, [f], {})
    assert list(details) == [str(f)]
    [(test, line)] = details[str(f)]
    assert test == "TestGroup::test_bad"
    assert "boom: the capture landed twice" in line and "\n" not in line


def test_a_red_that_passes_on_the_second_run_says_so(tmp_path):
    f = tmp_path / "test_promise_ZZ2_fixture.py"
    f.write_text("def test_ok():\n    assert True\n")
    details = pn.failure_details(tmp_path, [f], {})
    assert details[str(f)] == [("", "passed when run again for its failure line (flaky?)")]
