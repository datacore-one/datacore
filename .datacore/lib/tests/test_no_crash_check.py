"""`nonempty` is satisfied by a script that crashes noisily into its own log.

23 of this manifest's 58 artifact checks asserted only that a file existed and
had bytes in it. That is the weakest possible oracle: a job that dies still
writes its traceback, and the traceback makes the file non-empty and fresh, so
the contract passes and the board stays green. `last_line_regex` exists in this
module because the same lesson was learned once already, on winston's backup and
health digest -- it was never applied to the free-form logs.

`no_crash` asserts the other side of the question for logs whose success line
cannot be guessed: no evidence that a PROGRAM died. The markers are shaped like
interpreter and shell failures rather than the word "error", because box-news
summarises news articles and box-briefing is an LLM's prose -- a bare "failed"
there would false-FAIL a good run, and a contract that cries wolf stops being
read, which is the failure this whole exercise is trying to prevent.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobs.checks import CRASH_MARKERS, run_check  # noqa: E402
from jobs.manifest import Artifact  # noqa: E402


def _artifact(path, check="no_crash", arg=None):
    return Artifact(path=str(path), check=check, arg=arg, max_age_hours=None)


def test_a_traceback_fails_the_contract_that_nonempty_would_pass(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("starting the sweep\nTraceback (most recent call last):\n  File x\nValueError\n")

    assert run_check(_artifact(log, "nonempty")) == [], "nonempty is why this went unnoticed"
    errors = run_check(_artifact(log))
    assert errors and "Traceback" in errors[0], errors


def test_a_healthy_log_passes(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("22:45 6-meridian synced clean (HEAD a48ba1a7)\n22:45 8-firm synced clean\n")

    assert run_check(_artifact(log)) == []


def test_prose_about_failure_is_not_a_crash(tmp_path):
    """box-news summarises the news; box-briefing is an LLM writing English."""
    log = tmp_path / "news.log"
    log.write_text("Markets fell after the outage; the company said the error was "
                   "human and the deployment had failed.\n")

    assert run_check(_artifact(log)) == [], "a contract that cries wolf stops being read"


def test_an_old_failure_is_not_this_run_s_verdict(tmp_path):
    """These logs are append-only. A crash six weeks up the file is history."""
    log = tmp_path / "job.log"
    log.write_text("Traceback (most recent call last):\n" + "".join(
        f"line {n} synced clean\n" for n in range(200)))

    assert run_check(_artifact(log)) == []


def test_an_empty_log_still_fails(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("")

    errors = run_check(_artifact(log))
    assert errors and "empty" in errors[0].lower(), errors


def test_every_marker_is_detected(tmp_path):
    for marker in CRASH_MARKERS:
        log = tmp_path / "m.log"
        log.write_text(f"doing work\n{marker} something\ndone\n")
        assert run_check(_artifact(log)), f"{marker!r} went undetected"


def test_a_missing_file_still_reports_missing(tmp_path):
    errors = run_check(_artifact(tmp_path / "absent.log"))
    assert errors and "does not exist" in errors[0]
