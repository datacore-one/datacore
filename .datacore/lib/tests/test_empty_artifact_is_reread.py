"""An artifact that is empty right now may be mid-rewrite; wrong content is wrong."""
import sys
import threading
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from jobs import checks  # noqa: E402
from jobs.manifest import Artifact  # noqa: E402


def test_a_producer_mid_rewrite_does_not_page(tmp_path, monkeypatch):
    """2026-09-18 10:00Z: the hourly ingest truncated its log at :00 and the
    verifier read it at :00 -- "regex '0 space(s) failed' did not match -- file
    is empty" -- about an ingest that was working."""
    monkeypatch.setattr(checks, "EMPTY_RETRY_SECONDS", 0.4)
    log = tmp_path / "ingest.log"
    log.write_text("")                      # truncated, producer still running

    def finish():
        time.sleep(0.15)
        log.write_text("imported 0 task(s) across 9 space(s); 0 space(s) failed\n")
    writer = threading.Thread(target=finish)
    writer.start()
    try:
        errors = checks.run_check(Artifact(path=str(log), check="regex",
                                           arg=r"0 space\(s\) failed", max_age_hours=26))
    finally:
        writer.join()
    assert errors == [], errors


def test_an_artifact_that_stays_empty_still_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "EMPTY_RETRY_SECONDS", 0.2)
    log = tmp_path / "ingest.log"
    log.write_text("")
    errors = checks.run_check(Artifact(path=str(log), check="regex",
                                       arg=r"0 space\(s\) failed", max_age_hours=26))
    assert errors and "file is empty" in errors[0]


def test_wrong_content_is_not_waited_out(tmp_path, monkeypatch):
    """Nothing here waits for content to improve: a wrong file fails at once."""
    monkeypatch.setattr(checks, "EMPTY_RETRY_SECONDS", 5.0)
    log = tmp_path / "ingest.log"
    log.write_text("imported 0 task(s) across 9 space(s); 2 space(s) failed\n")
    started = time.monotonic()
    errors = checks.run_check(Artifact(path=str(log), check="regex",
                                       arg=r"0 space\(s\) failed", max_age_hours=26))
    assert errors and time.monotonic() - started < 1.0
