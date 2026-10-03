"""A model or agent run that failed says, in plain words, what failed and why.

Owner, 2026-10-02: "Errors should say what they are." Round 2.

Before: nightshift's "N Tasks Failed" alert carried `claude exited 1: <stderr>`
cut to 160 characters (often a command-line banner), and the chief-of-staff jobs
said "model exit 1" plus whatever line the run printed last. The reader had to
guess whether a person was needed (a login, a usage limit) or the task was the
problem (too big for its budget).

`check_diagnosis.agent_failure()` names the cause; `explain_agent()` adds the raw
tail at the end, cut at a word. The CLI serves the shell jobs on the box.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import check_diagnosis as cd  # noqa: E402

CASES = [
    # (raw error, rc, expected kind, words the sentence must contain)
    ("Timed out after 60 min: too big for its budget (60 min) - split it into steps that each fit",
     None, "too_big", ("too big", "60-minute", "split")),
    ("Execution outcome unknown (TimeoutExpired); reconcile before retry",
     None, "too_big", ("too big", "split")),
    ("Claude usage limit reached before execution; nothing ran", None, "usage_limit",
     ("usage limit", "resets")),
    ("claude exited 1: You've hit your limit · resets 5am (Europe/Ljubljana)", 1, "usage_limit",
     ("usage limit",)),
    ("claude exited 1: Not logged in · Please run /login", 1, "login", ("login", "log in again")),
    ("401 OAuth access token has been revoked", 1, "login", ("login",)),
    ("Error: HTTP 402: Insufficient credits", 1, "credit", ("credit",)),
    ("claude exited 137: ", 137, "out_of_memory", ("out of memory",)),
    ("<--- Last few GCs --->\nFATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory",
     134, "out_of_memory", ("out of memory",)),
    ("Command timed out", None, "timeout", ("timed out",)),
    ("Execution refused: RuntimeError", None, "refused", ("refused to start", "RuntimeError")),
    ("prod.deploy needs a co-signed grant before it runs and this task carries none; the call is paused",
     1, "refused", ("tool policy", "co-signed grant")),
    ("evaluation crashed (KeyError: 'score')", None, "refused", ("evaluator", "KeyError")),
    ("output rejected by evaluator critic-v2: no deliverable", None, "refused",
     ("evaluator", "critic-v2")),
    ("claude exited 1: fatal: Not possible to fast-forward, aborting.", 1, "git",
     ("git problem", "fast-forward")),
    ("agent/task-12: uncommitted changes left in /srv/Data/5-plur", None, "git",
     ("git problem", "uncommitted")),
    ("claude exited 1: API Error 529 Overloaded", 1, "provider", ("overloaded", "529")),
    ("MCP server plur failed to connect", 1, "mcp", ("plur", "not connected")),
    ("ssh: connect to host nightshift port 22: Connection refused", 255, "unreachable",
     ("could not reach", "nightshift")),
    ("StaleLogError: this machine already wrote seq 41", 1, "ledger_stop", ("ledger",)),
    ("Output write failed: /srv/Data/0-inbox/x.md", None, "write", ("could not be saved",)),
    ("task raised ValueError: bad heading", None, "internal", ("nightshift itself", "ValueError")),
]


@pytest.mark.parametrize("raw,rc,kind,words", CASES, ids=[c[2] + str(i) for i, c in enumerate(CASES)])
def test_each_cause_is_named_in_plain_words(raw, rc, kind, words):
    got_kind, sentence = cd.agent_failure(raw, rc=rc)
    assert got_kind == kind, (raw, got_kind, sentence)
    for w in words:
        assert w.lower() in sentence.lower(), (w, sentence)
    assert "exited" not in sentence or kind == "other", sentence


def test_unknown_failure_keeps_exit_status_and_last_line():
    kind, sentence = cd.agent_failure("starting\nsomething odd happened here", rc=3)
    assert kind == "other"
    assert "exited 3" in sentence and "something odd happened here" in sentence


def test_a_banner_is_not_mistaken_for_the_cause():
    raw = ("claude exited 1: Usage: claude [options] [command] [prompt]\n"
           "Claude Code - starts an interactive session by default\n" + "x " * 200
           + "\nNot logged in · Please run /login")
    kind, sentence = cd.agent_failure(raw, rc=1)
    assert kind == "login", sentence


def test_explain_keeps_the_raw_tail_at_the_end_cut_at_a_word():
    raw = "claude exited 1: " + " ".join(f"zq{i:03d}word" for i in range(100)) + " Not logged in"
    out = cd.explain_agent(raw, rc=1, tail=80)
    head, _, tail = out.partition(" (raw: ")
    assert "login" in head
    assert tail.endswith(")") and tail.startswith("…")
    words = set(raw.split())
    assert all(w in words for w in tail[1:-1].split()), tail
    assert len(tail) <= 90


def test_send_failure_says_why_telegram_refused():
    assert "token" in cd.send_failure("telegram send failed (http 401) on chunk 1/1").lower()
    assert "chat" in cd.send_failure("telegram send failed (http 400) on chunk 1/2").lower()
    assert "not set" in cd.send_failure("WINSTON_CHAT_ID not set").lower()
    assert "rate" in cd.send_failure("telegram send failed (http 429) on chunk 1/1").lower()
    assert "could not reach" in cd.send_failure(
        "telegram send failed (exception: URLError: <urlopen error [Errno -3] "
        "Temporary failure in name resolution>) on chunk 1/1")


def test_command_cause_names_a_full_disk_and_a_permission():
    assert "disk" in cd.cause(cmd="tar czf x", rc=2, cwd=".", repo=None, sha="",
                              stderr="tar: x: Wrote only 4096 of 10240 bytes\n"
                                     "tar: Error: No space left on device").lower()
    assert "permission" in cd.cause(cmd="tar czf x", rc=2, cwd=".", repo=None, sha="",
                                    stderr="tar: .plur/a: Cannot open: Permission denied").lower()


@pytest.mark.parametrize("mode,args,stdin,want", [
    ("agent", ["--rc", "1"], "Not logged in · Please run /login\n", "login"),
    ("send", [], "winston_send: NOT delivered (telegram send failed (http 401) on chunk 1/1)\n", "token"),
    ("command", ["--rc", "2", "--cmd", "tar czf /b/x.tgz"], "tar: Error: No space left on device\n", "disk"),
])
def test_cli_for_the_shell_jobs(mode, args, stdin, want):
    r = subprocess.run([sys.executable, str(LIB / "check_diagnosis.py"), mode, *args],
                       input=stdin, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip()
    assert want in line.lower() and "\n" not in line, line


def test_cli_with_no_output_still_says_something():
    r = subprocess.run([sys.executable, str(LIB / "check_diagnosis.py"), "agent", "--rc", "1"],
                       input="", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "exited 1" in r.stdout and "printed nothing" in r.stdout
