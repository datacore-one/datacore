"""MSG-12: Every message sent to The Firm is kept in one log with its full text, sender
and time; one that could not be delivered is sent again until it is; and running a
check twice never tells me the same news twice.

Kind: deterministic. Three senders of this repo, each through its real code:
- promise_nightly.main (the nightly promise scoreboard) with a fake history of boards
  on disk, the eval run and the send replaced by stand-ins (no evals run, no Telegram);
- job_verify_notify.sh (the relay every job checker's failure goes through) in a
  sandbox home, with the real job_verify behind it and `ssh` to the relay host a stub
  that records what it was given;
- promise_nightly.send_to_firm through this host's alert command (a stub that records).

Evidence guarded (findings-winston-state "C8"; log review 2026-10-28):
- promise_nightly.py saves tonight's board (:567-570) BEFORE the send (:591-594) and
  tries once (:460); the next night compares with that saved board, so the promise is
  already "red" and an alert lost to a timeout (10-24) is never re-announced.
- promise_nightly.py:331 compares with boards from BEFORE today only, so a second run
  the same day compares with yesterday again and repeats its news.
- job_verify_notify.sh:99-101,114-116 sends without any record of what it sent; the
  only sent log (cos_alert.sh's .sent-log) keeps 60 characters of cos_alert's own
  messages. No log holds what reached The Firm.

Graded:
1. A scoreboard alert that could not be delivered is sent the next night, while the
   news is still true.
2. Running the scoreboard twice on the same day sends its news once.
3. After the relay and the scoreboard each send a message, one file holds both
   messages' full text, each sender's name and a time.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import promise_nightly as pn  # noqa: E402

TEXTS = {"CAP-4": "A capture made twice lands once", "DAY-2": "The morning briefing is ready before I wake"}
FAIL = {"CAP-4": [{"file": "t.py", "test": "test_twice", "line": "AssertionError: two entries in the inbox"}]}


def _raw(states, failures=None):
    return {"counts": {}, "promises": states, "failures": failures or {}}


def _board(date, states, failures=None):
    return pn.build_board(_raw(states, failures), {}, TEXTS, date=date, host="testhost")


def _setup(tmp_path, monkeypatch, history):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    for b in history:
        (state / f"board-{b['date']}.json").write_text(json.dumps(b))
    base = tmp_path / "promise-baseline.json"
    base.write_text(json.dumps({"green": ["CAP4", "DAY2"]}) + "\n")
    monkeypatch.setattr(pn, "STATE_DIR", state)
    monkeypatch.setattr(pn, "BASELINE", base)
    monkeypatch.setattr(pn, "unmet_needs", lambda: {})
    monkeypatch.setattr(pn, "promise_texts", lambda: TEXTS)
    monkeypatch.setattr(pn, "has_pytest", lambda: True)
    return state


def _night(monkeypatch, date, tonight, deliver: bool, sent: list):
    monkeypatch.setattr(pn, "run_board", lambda want=None: tonight)
    monkeypatch.setattr(pn, "today", lambda: date)
    monkeypatch.setattr(pn, "send_to_firm",
                        lambda text: (sent.append(text), (True, "sent") if deliver else (False, "TimeoutError"))[1])
    return pn.main([])


def test_an_alert_that_was_not_delivered_is_sent_again_the_next_night(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [_board("2026-10-22", {"CAP-4": "green", "DAY-2": "green"})])
    red = _raw({"CAP-4": "red", "DAY-2": "green"}, FAIL)
    tried: list[str] = []
    _night(monkeypatch, "2026-10-23", red, deliver=False, sent=tried)   # a Friday: lost to a timeout
    assert tried and "CAP-4" in tried[0], "the first night did not even try to send the news"
    again: list[str] = []
    _night(monkeypatch, "2026-10-24", red, deliver=True, sent=again)    # still red, delivery works
    assert any("CAP-4" in t for t in again), (
        "the scoreboard alert that turned CAP-4 red was never delivered, and the next night "
        "sent nothing: the undelivered news is never re-announced")


def test_running_the_scoreboard_twice_the_same_day_sends_its_news_once(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [_board("2026-10-22", {"CAP-4": "green", "DAY-2": "green"})])
    red = _raw({"CAP-4": "red", "DAY-2": "green"}, FAIL)
    sent: list[str] = []
    _night(monkeypatch, "2026-10-23", red, deliver=True, sent=sent)
    _night(monkeypatch, "2026-10-23", red, deliver=True, sent=sent)     # a second run, same day
    assert len(sent) == 1, (
        f"the scoreboard ran twice on 2026-10-23 and told The Firm the same news {len(sent)} times "
        f"('CAP-4 turned red')")


# ---- one log of what was sent ---------------------------------------------------

_SSH_STUB = "#!/bin/sh\ncat >> \"$SENT_SINK\"\nprintf '\\n<<END>>\\n' >> \"$SENT_SINK\"\nexit 0\n"


def _norm(s: str) -> str:
    return " ".join(s.split())


def test_every_message_sent_is_kept_in_one_log_with_full_text_sender_and_time(tmp_path):
    home = tmp_path / "home"
    (home / ".datacore" / "state").mkdir(parents=True)
    root = tmp_path / "Data"
    root.mkdir()
    runner = tmp_path / "runner" / ".datacore"
    runner.mkdir(parents=True)
    (runner / "lib").symlink_to(LIB)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(_SSH_STUB)
    (bin_dir / "ssh").chmod(0o755)
    sinks = tmp_path / "sinks"
    sinks.mkdir()

    bad = tmp_path / "artifacts" / "backup.log"
    bad.parent.mkdir()
    bad.write_text("backup FAILED: destination not mounted\n")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "jobs": [
        {"name": "sim-backup", "machine": "simbox", "schedule": "30 2 * * *", "cmd": "true",
         "on_fail": "log", "delegate": False,
         "artifacts": [{"path": str(bad), "check": "regex", "arg": "^backup OK"}]}]}, sort_keys=False))
    space = tmp_path / "space"
    space.mkdir()

    env = {k: v for k, v in os.environ.items() if k not in ("JOB_VERIFY_ENV_FILE", "DATACORE_ALERT_COMMAND")}
    env.update({"HOME": str(home), "PATH": f"{bin_dir}:{env.get('PATH', '/usr/bin:/bin')}",
                "DATACORE_ROOT": str(root), "DATACORE_ACTOR": "simbox",
                "JOB_VERIFY_RUNNER": str(tmp_path / "runner"), "JOB_VERIFY_PYTHON": sys.executable,
                "JOB_VERIFY_RELAY_HOST": "relay.sim.invalid",
                "JOB_VERIFY_LOG": str(home / ".datacore" / "state" / "job_verify.log"),
                "SENT_SINK": str(sinks / "relay.txt")})
    r = subprocess.run(["bash", str(LIB / "job_verify_notify.sh"), "--machine", "simbox",
                        "--manifest", str(manifest), "--space", str(space)],
                       env=env, capture_output=True, text=True, timeout=120)
    relayed = (sinks / "relay.txt").read_text().split("<<END>>")[0].strip() if (sinks / "relay.txt").exists() else ""
    assert relayed, f"the relay sent nothing to set the test up (rc={r.returncode}): {r.stderr[-300:]}"

    board_text = ("Promise scoreboard, 2026-10-23 on simbox\n\n🔴 1 promise(s) turned red\n"
                  "• A capture made twice lands once (CAP-4): test_twice: AssertionError: two entries in the "
                  "inbox after the second capture of the same voice note at 07:14 from the phone\n\n"
                  "Board: ~/.datacore/state/promise-scoreboard/board-2026-10-23.json on simbox")
    env2 = {**env, "DATACORE_ALERT_COMMAND": f"cat >> {sinks / 'board.txt'}"}
    r2 = subprocess.run([sys.executable, "-c",
                         "import sys; sys.path.insert(0, sys.argv[1]); import promise_nightly as p; "
                         "print(p.send_to_firm(sys.argv[2]))", str(LIB), board_text],
                        env=env2, capture_output=True, text=True, timeout=120)
    assert "True" in r2.stdout, f"the scoreboard send did not go through to set the test up: {r2.stderr[-300:]}"

    roots = {home, Path(os.environ["DATACORE_STATE"]), Path(os.environ["DATACORE_UNDELIVERED_LOG"]).parent}
    stamp = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}|\b1[6-9]\d{8}\b")
    holders = []
    for base in roots:
        for f in base.rglob("*"):
            if not f.is_file() or f.is_symlink() or sinks in f.parents:
                continue
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            flat = _norm(text)
            if (_norm(relayed) in flat and _norm(board_text) in flat
                    and "job_verify" in text and "promise_nightly" in text and stamp.search(text)):
                holders.append(f)
    assert holders, (
        "two messages reached The Firm (the job checker's relay and the promise scoreboard) and no "
        "single log holds their full text with sender and time: the relay keeps no record of what it "
        "sent, and the only sent log (cos_alert's) keeps 60 characters of its own messages")
