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
    monkeypatch.setattr(pn, "run_board", lambda want=None: tonight)
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


def test_the_first_night_on_a_host_starts_its_history_and_sends_nothing(tmp_path, monkeypatch, capsys):
    """The baseline is recorded on the workstation. On 2026-09-30 the overnight
    host's first run found 27 baseline-green promises red there -- unreachable
    hosts, files that checkout lacks, its own agent identity -- none a broken
    promise. "Was green" means green on THIS host, so the first night only records."""
    tonight = raw({"CAP-4": "red", "NEW-1": "red"},
                  {"CAP-4": [fail("t.py", "t", "boom")], "NEW-1": [fail("u.py", "u", "never green")]})
    sent, rc, state = night(tmp_path, monkeypatch, [], tonight)
    assert rc == 0 and sent == []
    assert (state / "board-2026-09-30.json").exists()
    out = capsys.readouterr().out
    assert "first night on this host" in out
    assert out.strip().splitlines()[-1].endswith("alert none")


def test_the_second_night_compares_with_the_first(tmp_path, monkeypatch):
    hist = [board("2026-09-29", {"CAP-4": "red", "DAY-2": "green"}, {"CAP-4": [fail("t.py", "t", "x")]})]
    sent, _, _ = night(tmp_path, monkeypatch, hist, raw({"CAP-4": "red", "DAY-2": "red"},
                                                         {"CAP-4": [fail("t.py", "t", "x")],
                                                          "DAY-2": [fail("d.py", "d", "boom")]}))
    assert len(sent) == 1 and "(DAY-2)" in sent[0] and "(CAP-4)" not in sent[0]


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
    monkeypatch.setattr(pn, "run_board", lambda want=None: raw({"CAP-4": "red"}, {"CAP-4": [fail("t.py", "t", "x")]}))
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

    def broken(want=None):
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


@pytest.mark.parametrize("line", [
    "subprocess.TimeoutExpired: Command '['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', 'box', 'crontab -l']' timed out",
    "AssertionError: machines not on current main: box: could not check (UNREACHABLE timeout)",
    "AssertionError: could not tell (unreachable): ['box', 'hermes']",
    "Failed: box: could not read the crontab (timeout) -- could not tell is not a pass",
])
def test_an_ssh_timeout_or_could_not_tell_is_could_not_run(line):
    b = board(WED, {"SYN-4": "red"}, {"SYN-4": [fail("s.py", "t", line)]})
    assert b["promises"]["SYN-4"]["state"] == "could-not-run"


def test_a_promise_whose_every_eval_file_lacks_a_need_could_not_run_without_a_rerun():
    r = raw({"MSG-8": "red"})
    r["eval_files"] = {"MSG-8": ["m.py"]}
    b = pn.build_board(r, {"m.py": ["fleet"]}, TEXTS, date=WED, host="h")
    assert b["promises"]["MSG-8"]["state"] == "could-not-run"


def test_only_reds_that_could_be_news_are_rerun_for_their_failure_line(tmp_path, monkeypatch):
    hist = [board("2026-09-29", {"CAP-4": "red", "DAY-2": "green"}, {"CAP-4": [fail("t.py", "t", "x")]})]
    asked = {}

    def fake(want=None):
        asked["want"] = want
        return raw({"CAP-4": "red", "DAY-2": "green"}, {"CAP-4": [fail("t.py", "t", "x")]})
    state = tmp_path / "state"
    state.mkdir()
    (state / "board-2026-09-29.json").write_text(json.dumps(hist[0]))
    monkeypatch.setattr(pn, "STATE_DIR", state)
    monkeypatch.setattr(pn, "run_board", fake)
    monkeypatch.setattr(pn, "unmet_needs", lambda: {})
    monkeypatch.setattr(pn, "promise_texts", lambda: TEXTS)
    monkeypatch.setattr(pn, "send_to_firm", lambda text: (True, "sent"))
    monkeypatch.setattr(pn, "today", lambda: WED)
    pn.main([])
    assert asked["want"]("DAY-2") is True        # green last night: a red tonight is news
    assert asked["want"]("CAP-4") is False       # red last night: already told


class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _env_files(tmp_path, body: str) -> list:
    f = tmp_path / ".env"
    f.write_text(body)
    return [f]


def test_the_alert_goes_to_the_firm_group_with_this_hosts_bot(tmp_path, monkeypatch):
    """On the overnight host winston_send cannot load (a root-owned ~/.config/cos.env,
    2026-09-30); its own alerts post directly: TELEGRAM_BOT_TOKEN to ALERT_CHAT_ID."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ALERT_CHAT_ID", raising=False)
    monkeypatch.setattr(pn, "ENV_FILES", _env_files(tmp_path, "TELEGRAM_BOT_TOKEN=tok\nALERT_CHAT_ID=-100\nTELEGRAM_CHAT_ID=1to1\n"))
    seen = {}

    def fake_urlopen(url, data=None, timeout=None):
        seen["url"], seen["data"] = url, data.decode()
        return _Resp(200)
    monkeypatch.setattr(pn.urllib.request, "urlopen", fake_urlopen)
    ok, why = pn.send_to_firm("Promise scoreboard: A capture made twice lands once (CAP-4) turned red")
    assert ok and why == "sent"
    assert seen["url"].endswith("/bottok/sendMessage")
    assert "chat_id=-100" in seen["data"] and "1to1" not in seen["data"]


def test_no_group_means_no_send_and_a_recorded_undelivered(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", str(tmp_path / "undelivered.jsonl"))
    monkeypatch.setattr(pn, "ENV_FILES", _env_files(tmp_path, "TELEGRAM_BOT_TOKEN=tok\nTELEGRAM_CHAT_ID=1to1\n"))
    monkeypatch.delenv("ALERT_CHAT_ID", raising=False)
    called = []
    monkeypatch.setattr(pn.urllib.request, "urlopen", lambda *a, **k: called.append(a))
    ok, why = pn.send_to_firm("Promise scoreboard: test")
    assert ok is False and "ALERT_CHAT_ID" in why and called == []
    rec = json.loads((tmp_path / "undelivered.jsonl").read_text().splitlines()[-1])
    assert rec["sender"] == "promise_nightly"


def test_a_refused_send_is_recorded_as_undelivered(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", str(tmp_path / "undelivered.jsonl"))
    monkeypatch.setattr(pn, "ENV_FILES", _env_files(tmp_path, "TELEGRAM_BOT_TOKEN=tok\nALERT_CHAT_ID=-100\n"))
    monkeypatch.setattr(pn.urllib.request, "urlopen", lambda *a, **k: _Resp(401))
    ok, why = pn.send_to_firm("Promise scoreboard: test")
    assert ok is False and "401" in why
    assert (tmp_path / "undelivered.jsonl").exists()


# ---- every machine runs its own board (owner decision 2026-09-30) ----------------
#
# nightshift's first board: 89 red, most of them evals that ssh to the other hosts
# (which nightshift cannot reach) or need a live agent session (never on in the
# nightly). Neither is a broken promise; both are "could not run here".

ROSTER = """\
roles:
  console: mac
servers:
  mac:
    kind: workstation
    ssh_alias: '-'
    access: {actor: mac, hostname: Mac}
  nightshift:
    kind: server
    ssh_alias: nightshift
    ledger_actors: [nightshift, miles]
    access: {actor: miles, hostname: nightshift}
"""


def _roster(tmp_path, body=ROSTER):
    reg = tmp_path / ".datacore" / "registry"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "infrastructure.yaml").write_text(body)
    return tmp_path


def test_fleet_is_met_only_on_the_machine_the_roster_names_as_its_console(tmp_path):
    """Having the roster is not reaching the fleet: nightshift has it and cannot
    ssh to box, hermes or plur-claw. The roster names the one machine that reads
    the fleet (roles.console); everywhere else `fleet` is not met."""
    import needs_gate as ng
    root = _roster(tmp_path)
    assert ng.met("fleet", root, {"DATACORE_ACTOR": "mac"})
    assert not ng.met("fleet", root, {"DATACORE_ACTOR": "miles"})
    assert not ng.met("fleet", root, {"DATACORE_ACTOR": "nightshift"})
    # a roster that names no console: nobody has declared who reads the fleet
    root2 = _roster(tmp_path / "noconsole", ROSTER.replace("roles:\n  console: mac\n", ""))
    assert not ng.met("fleet", root2, {"DATACORE_ACTOR": "mac"})
    assert not ng.met("fleet", tmp_path / "bare", {"DATACORE_ACTOR": "mac"})


def test_a_fleet_eval_on_a_host_that_cannot_reach_the_fleet_could_not_run(tmp_path):
    import needs_gate as ng
    root = _roster(tmp_path)
    path = ".datacore/lib/tests/test_promise_SYN4_x.py"
    entries = [{"test": path, "only": ["test_every_host"], "needs": ["fleet"], "why": "ssh"}]
    unmet = ng.unmet_by_test(root=root, env={"DATACORE_ACTOR": "miles"}, entries=entries)
    b = pn.build_board(raw({"SYN-4": "red"}, {"SYN-4": [fail(path, "test_every_host", "AssertionError: box differs")]}),
                       unmet, TEXTS, date=WED, host="nightshift")
    assert b["promises"]["SYN-4"]["state"] == "could-not-run" and "fleet" in b["promises"]["SYN-4"]["why"]
    # the console runs it, and the same failure is a red there
    unmet_mac = ng.unmet_by_test(root=root, env={"DATACORE_ACTOR": "mac"}, entries=entries)
    b = pn.build_board(raw({"SYN-4": "red"}, {"SYN-4": [fail(path, "test_every_host", "AssertionError: box differs")]}),
                       unmet_mac, TEXTS, date=WED, host="mac")
    assert b["promises"]["SYN-4"]["state"] == "red"


def test_a_red_not_rerun_as_news_whose_test_lacks_a_need_here_could_not_run(tmp_path, monkeypatch):
    """A red already red last night is not rerun for its failure line, and only a
    FILE-wide unmet need used to excuse it; a need declared for single tests
    (file::test) was ignored, so on the first nights every per-test fleet or agent
    eval stayed red. A red whose eval file carries any unmet need is rerun, so each
    failing test is judged against its own needs."""
    path = ".datacore/lib/tests/test_promise_MSG8_x.py"
    hist = [board("2026-09-28", {"MSG-8": "green"}),
            board("2026-09-29", {"MSG-8": "red"}, {"MSG-8": [fail(path, "test_agent", "x")]})]

    def fake(want=None):
        r = raw({"MSG-8": "red"}, {"MSG-8": [fail(path, "test_agent", "agent eval not run (set DATACORE_AGENT_EVALS=1)")]}
                if want is None or want("MSG-8") else {})
        r["eval_files"] = {"MSG-8": [path]}
        return r
    state = tmp_path / "state"
    state.mkdir()
    for b in hist:
        (state / f"board-{b['date']}.json").write_text(json.dumps(b))
    monkeypatch.setattr(pn, "STATE_DIR", state)
    monkeypatch.setattr(pn, "run_board", fake)
    monkeypatch.setattr(pn, "unmet_needs", lambda: {f"{path}::test_agent": ["agent"]})
    monkeypatch.setattr(pn, "promise_eval_files", lambda: {"MSG8": [path]})
    monkeypatch.setattr(pn, "promise_texts", lambda: TEXTS)
    monkeypatch.setattr(pn, "send_to_firm", lambda text: (True, "sent"))
    monkeypatch.setattr(pn, "today", lambda: WED)
    pn.main([])
    b = json.loads((state / "board-2026-09-30.json").read_text())
    assert b["promises"]["MSG-8"]["state"] == "could-not-run"


def test_an_agent_eval_that_was_not_enabled_could_not_run():
    """agent_eval.require_enabled() fails with its own words when the nightly
    (which never turns agent evals on) runs it: the eval did not run."""
    line = "Failed: agent eval not run (set DATACORE_AGENT_EVALS=1)"
    b = board(WED, {"MEM-1": "red"}, {"MEM-1": [fail("m.py", "test_agent_does_not_reach", line)]})
    assert b["promises"]["MEM-1"]["state"] == "could-not-run"


def test_the_first_night_on_a_host_reruns_every_red_for_its_reason(tmp_path, monkeypatch):
    """The first night starts the host's history; a red recorded there without
    its failure line is never rerun later (already red), so its reason would be
    lost for good."""
    asked = {}

    def fake(want=None):
        asked["want"] = want
        return raw({"NEW-1": "red"}, {"NEW-1": [fail("u.py", "u", "x")]})
    state = tmp_path / "state"
    monkeypatch.setattr(pn, "STATE_DIR", state)
    monkeypatch.setattr(pn, "BASELINE", tmp_path / "none.json")
    monkeypatch.setattr(pn, "run_board", fake)
    monkeypatch.setattr(pn, "unmet_needs", lambda: {})
    monkeypatch.setattr(pn, "promise_eval_files", lambda: {})
    monkeypatch.setattr(pn, "promise_texts", lambda: TEXTS)
    monkeypatch.setattr(pn, "today", lambda: WED)
    pn.main(["--no-send"])
    assert asked["want"] is None or asked["want"]("NEW-1") is True


def test_a_host_without_pytest_says_it_could_not_run_instead_of_a_board_of_reds(tmp_path, monkeypatch, capsys):
    """box's first board (2026-09-30): 0 green, 188 red -- its python3 has no
    pytest, so every eval 'crashed'. That is this host unable to run the board,
    not 188 broken promises."""
    state = tmp_path / "state"
    monkeypatch.setattr(pn, "STATE_DIR", state)
    monkeypatch.setattr(pn, "today", lambda: WED)
    monkeypatch.setattr(pn, "has_pytest", lambda: False)
    monkeypatch.setattr(pn, "run_board", lambda want=None: raw({"CAP-4": "red"}))
    assert pn.main(["--no-send"]) == 1
    assert not state.exists() or not list(state.glob("board-*.json"))
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert "FAILED" in last and "pytest" in last


def test_a_host_whose_alerts_go_through_its_own_command_uses_it(tmp_path, monkeypatch):
    """box's alerts go through Winston's sender, the workstation's through the
    always-on host; DATACORE_ALERT_COMMAND (the variable job_verify already
    honours) names that route, reading the text on stdin."""
    out = tmp_path / "sent.txt"
    monkeypatch.setenv("DATACORE_ALERT_COMMAND", f"cat > {out}")
    monkeypatch.setattr(pn, "ENV_FILES", [])
    monkeypatch.setattr(pn.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no direct send"))
    ok, why = pn.send_to_firm("Promise scoreboard: A capture made twice lands once (CAP-4) turned red")
    assert ok and why == "sent"
    assert "(CAP-4)" in out.read_text()
    monkeypatch.setenv("DATACORE_ALERT_COMMAND", "exit 3")
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", str(tmp_path / "undelivered.jsonl"))
    ok, why = pn.send_to_firm("x")
    assert ok is False and "3" in why


def test_the_promise_list_can_be_named_where_the_code_has_no_system_space(tmp_path):
    """The satellites run the code from a bare runner checkout; their promise
    list is in the data root's copy of the system space."""
    import subprocess
    d = tmp_path / "promises"
    d.mkdir()
    (d / "p.yaml").write_text("capabilities:\n  - promises:\n      - {id: ZZ-1, promise: A test promise}\n")
    r = subprocess.run([sys.executable, "-c", "import promise_evals as p; print(p.promises())"], cwd=LIB,
                       env={**__import__("os").environ, "DATACORE_PROMISES_DIR": str(d)},
                       capture_output=True, text=True)
    assert "'ZZ-1': 'A test promise'" in r.stdout, r.stderr


def test_an_eval_that_cannot_be_imported_here_does_not_hide_the_rest_of_its_batch(tmp_path):
    """hermes, 2026-09-30: two evals import a module that host does not have; the
    collection error stopped pytest for the whole batch of twenty, and every other
    file in it counted red with no reason (two of them pass when run)."""
    import promise_evals
    bad = tmp_path / "test_promise_ZZ3_needs_a_module.py"
    bad.write_text("import a_module_this_host_does_not_have\n\ndef test_x():\n    pass\n")
    good = tmp_path / "test_promise_ZZ4_fine.py"
    good.write_text("def test_ok():\n    assert True\n")
    red = tmp_path / "test_promise_ZZ5_red.py"
    red.write_text("def test_bad():\n    assert 1 == 2, 'a real failure'\n")
    result = promise_evals.run_suite(tmp_path, [bad, good, red], {})
    assert result == {bad.name: False, good.name: True, red.name: False}
    details = pn.failure_details(tmp_path, [bad, red], {})
    [(test, line)] = details[str(red)]
    assert test == "test_bad" and "a real failure" in line


# ---- every test skipped (owner, 2026-10-01) --------------------------------------
# AGT-11 on the owner's Mac: its only test skips with "this is the owner's
# machine, not an agent's". promise_evals counts a skip as not passing, so the
# board read red -- a test that did not run is "could not run here", with the
# skip's own reason, never a regression.

AGT_SKIP = "skipped: this is the owner's machine, not an agent's"


def test_a_promise_whose_every_test_was_skipped_could_not_run_with_the_reason():
    b = board(WED, {"AGT-11": "red"}, {"AGT-11": [fail("a.py", "test_agent_side", AGT_SKIP),
                                                  fail("a.py", "test_other_side", AGT_SKIP)]})
    e = b["promises"]["AGT-11"]
    assert e["state"] == "could-not-run", e
    assert "this is the owner's machine, not an agent's" in e["why"]


def test_a_skip_beside_a_real_failure_stays_red():
    b = board(WED, {"AGT-11": "red"}, {"AGT-11": [fail("a.py", "test_skipped", AGT_SKIP),
                                                  fail("a.py", "test_bad", "AssertionError: wrong")]})
    assert b["promises"]["AGT-11"]["state"] == "red"


def test_skipped_tests_in_a_real_file_could_not_run_but_a_skip_beside_a_pass_does_not(tmp_path):
    only = tmp_path / "test_promise_ZZ3_fixture.py"
    only.write_text("import pytest\n\ndef test_a():\n    pytest.skip(\"this is the owner's machine, not an agent's\")\n")
    mixed = tmp_path / "test_promise_ZZ4_fixture.py"
    mixed.write_text("import pytest\n\ndef test_ok():\n    assert True\n\n"
                     "def test_b():\n    pytest.skip('not here')\n")
    details = pn.failure_details(tmp_path, [only, mixed], {})
    fails = {pid: [fail(str(f), t, line) for t, line in details[str(f)]]
             for pid, f in (("ZZ-3", only), ("ZZ-4", mixed))}
    b = board(WED, {"ZZ-3": "red", "ZZ-4": "red"}, fails)
    assert b["promises"]["ZZ-3"]["state"] == "could-not-run", b["promises"]["ZZ-3"]
    assert "owner's machine" in b["promises"]["ZZ-3"]["why"]
    assert b["promises"]["ZZ-4"]["state"] == "red", "a test that passed beside the skip: the promise is partly unrun"


def test_a_red_recorded_from_skips_alone_is_rerun_and_becomes_could_not_run(tmp_path, monkeypatch):
    """Boards written before this rule hold AGT-11 as red with a skip as its
    reason; an already-red promise is not rerun, so it would stay red for good."""
    path = ".datacore/lib/tests/test_promise_AGT11_x.py"
    hist = [board("2026-09-29", {"AGT-11": "red"}, {"AGT-11": [fail(path, "test_a", AGT_SKIP)]})]
    hist[0]["promises"]["AGT-11"]["state"] = "red"          # as the old rule recorded it

    def fake(want=None):
        r = raw({"AGT-11": "red"}, {"AGT-11": [fail(path, "test_a", AGT_SKIP)]}
                if want is None or want("AGT-11") else {})
        r["eval_files"] = {"AGT-11": [path]}
        return r
    state = tmp_path / "state"
    state.mkdir()
    for h in hist:
        (state / f"board-{h['date']}.json").write_text(json.dumps(h))
    monkeypatch.setattr(pn, "STATE_DIR", state)
    monkeypatch.setattr(pn, "BASELINE", tmp_path / "none.json")
    monkeypatch.setattr(pn, "run_board", fake)
    monkeypatch.setattr(pn, "unmet_needs", lambda: {})
    monkeypatch.setattr(pn, "promise_eval_files", lambda: {"AGT11": [path]})
    monkeypatch.setattr(pn, "promise_texts", lambda: TEXTS)
    monkeypatch.setattr(pn, "send_to_firm", lambda text: (True, "sent"))
    monkeypatch.setattr(pn, "today", lambda: WED)
    pn.main([])
    b = json.loads((state / "board-2026-09-30.json").read_text())
    assert b["promises"]["AGT-11"]["state"] == "could-not-run", b["promises"]["AGT-11"]


# ---- the weekly agent board (board D14, 2026-10-01) ------------------------------
# The live-AI evals cost model runs, so the nightly never runs them. Once a week
# `--agents` runs only the promises that have an agent eval, with the switch on,
# into its own history beside the nightly's, and alerts on what turned red.

def test_the_agent_board_runs_only_promises_with_an_agent_eval(monkeypatch):
    monkeypatch.setattr(pn, "agent_promises", lambda: ["AGT6", "MEM6"])
    cmd = pn.runner_command(agents=True)
    assert "--agents" in cmd and "--write-baseline" not in cmd
    assert cmd[cmd.index("--only") + 1] == "AGT6,MEM6"
    assert "--agents" not in pn.runner_command()


def test_the_agent_board_turns_the_switch_on_and_the_nightly_still_never_does(monkeypatch):
    monkeypatch.setenv("DATACORE_AGENT_EVALS", "1")
    assert pn.runner_env(agents=True).get("DATACORE_AGENT_EVALS") == "1"
    assert "DATACORE_AGENT_EVALS" not in pn.runner_env()


def test_agent_promises_are_the_ones_whose_eval_runs_an_agent():
    ids = pn.agent_promises()
    assert "AGT6" in ids, "AGT-6's eval runs a real agent (require_enabled)"
    assert "CAP4" not in ids


def test_the_agent_board_keeps_its_own_history_and_alerts_on_what_turned_red(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    (state / "agents").mkdir(parents=True)
    (state / "agents" / "board-2026-09-23.json").write_text(json.dumps(board("2026-09-23", {"CAP-4": "green"})))
    (state / "board-2026-09-29.json").write_text(json.dumps(board("2026-09-29", {"CAP-4": "red"})))
    base = tmp_path / "promise-baseline.json"
    base.write_text(json.dumps({"green": []}) + "\n")
    sent, wants = [], []
    monkeypatch.setattr(pn, "STATE_DIR", state)
    monkeypatch.setattr(pn, "BASELINE", base)
    monkeypatch.setattr(pn, "run_board", lambda want=None, agents=False: (
        wants.append((want, agents)) or raw({"CAP-4": "red"})))
    monkeypatch.setattr(pn, "unmet_needs", lambda agents=False: {})
    monkeypatch.setattr(pn, "promise_texts", lambda: TEXTS)
    monkeypatch.setattr(pn, "send_to_firm", lambda text: (sent.append(text) or (True, "sent")))
    monkeypatch.setattr(pn, "today", lambda: WED)
    rc = pn.main(["--agents"])
    out = capsys.readouterr().out
    assert rc == 0 and len(sent) == 1 and "CAP-4" in sent[0] and sent[0].startswith("Agent evals (weekly")
    assert (state / "agents" / f"board-{WED}.json").exists()
    assert not (state / f"board-{WED}.json").exists(), "the agent board wrote into the nightly's history"
    assert wants and wants[0][1] is True
    assert wants[0][0]("CAP-4") is False, "a red agent eval must not be run a second time for its line (cost)"
    assert out.strip().splitlines()[-1].startswith("agent-evals: ")
