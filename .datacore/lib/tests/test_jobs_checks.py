"""Tests for jobs.checks - artifact contract verification.

`run_check(artifact, *, now=...)` never raises: every filesystem or
content surprise (missing file, stale mtime, unreadable file, binary
garbage, bad JSON) is converted into an error string naming the expanded
path and the reason. An empty list means the contract holds. `now` is
always injected in these tests so nothing depends on the real clock.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from jobs.checks import expand_path, run_check
from jobs.manifest import Artifact

NOW = 86400 * 19936  # arbitrary fixed instant, well clear of any real "today"

# Fixed instant used for the timezone-pinned {today} tests below: verified
# (by actually running the conversion, not just computed on paper) to be
# 2024-07-30T23:00:00Z -- late enough in the UTC day that a positive-offset
# zone rolls over to the next calendar date, which is exactly the property
# these tests need to exercise.
PINNED_EPOCH = 1722297600.0 + 23 * 3600


@contextlib.contextmanager
def _pinned_tz(tz_name: str):
    """Pin the process timezone to `tz_name` for the duration of the block.

    Skips (rather than fails) on platforms without `time.tzset()` (e.g.
    Windows), and always restores the previous TZ + calls tzset() again on
    the way out, even if the body raises.
    """
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset() not available on this platform")
    original = os.environ.get("TZ")
    os.environ["TZ"] = tz_name
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


# --- path expansion -----------------------------------------------------


def test_today_expansion_uses_local_date_of_injected_now(tmp_path):
    today = datetime.fromtimestamp(NOW).strftime("%Y-%m-%d")
    target = tmp_path / f"report-{today}.json"
    target.write_text("{}")

    resolved = expand_path(str(tmp_path / "report-{today}.json"), now=NOW)

    assert resolved == str(target)
    assert Path(resolved).exists()


def test_tilde_expansion_uses_monkeypatched_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "artifact.txt").write_text("x")

    resolved = expand_path("~/artifact.txt", now=NOW)

    assert resolved == str(tmp_path / "artifact.txt")


def test_today_is_substituted_before_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    today = datetime.fromtimestamp(NOW).strftime("%Y-%m-%d")
    (tmp_path / f"log-{today}.txt").write_text("x")

    resolved = expand_path("~/log-{today}.txt", now=NOW)

    assert resolved == str(tmp_path / f"log-{today}.txt")


def test_today_expansion_pinned_utc_hardcoded_date():
    # Hardcoded (not computed via the production formula) expected date for
    # a fixed epoch under a pinned TZ -- this is the regression guard the
    # earlier tests above lacked: they derived "expected" via the same
    # datetime.fromtimestamp() call production uses, so a local-vs-UTC
    # regression would pass them too.
    with _pinned_tz("UTC"):
        resolved = expand_path("report-{today}.txt", now=PINNED_EPOCH)

    assert resolved == "report-2024-07-30.txt"


def test_today_expansion_pinned_far_east_tz_rolls_to_next_day():
    # Same instant as the UTC test above, but interpreted in a UTC+14 zone.
    # If expand_path ever regressed to datetime.utcfromtimestamp() (or any
    # other UTC-based conversion) instead of the local `datetime.fromtimestamp`,
    # this would still resolve to "2024-07-30" and the assertion would catch it.
    with _pinned_tz("Pacific/Kiritimati"):
        resolved = expand_path("report-{today}.txt", now=PINNED_EPOCH)

    assert resolved == "report-2024-07-31.txt"


# --- exists ---------------------------------------------------------------


def test_exists_check_passes_when_file_present(tmp_path):
    p = tmp_path / "marker.txt"
    p.write_text("anything")

    errors = run_check(Artifact(path=str(p), check="exists"), now=NOW)

    assert errors == []


def test_missing_file_reports_error_naming_path(tmp_path):
    p = tmp_path / "does-not-exist.txt"

    errors = run_check(Artifact(path=str(p), check="exists"), now=NOW)

    assert len(errors) == 1
    assert str(p) in errors[0]


def test_missing_file_stops_pipeline_no_further_checks(tmp_path):
    p = tmp_path / "does-not-exist.json"

    errors = run_check(
        Artifact(path=str(p), check="json_has_keys", arg=["ok"], max_age_hours=1),
        now=NOW,
    )

    # only the missing-file error, not a stale error or missing-keys error too
    assert len(errors) == 1
    assert str(p) in errors[0]


# --- freshness (max_age_hours) --------------------------------------------


def test_freshness_passes_at_exact_boundary(tmp_path):
    p = tmp_path / "fresh.txt"
    p.write_text("x")
    max_age_hours = 2
    boundary_mtime = NOW - max_age_hours * 3600
    os.utime(p, (boundary_mtime, boundary_mtime))

    errors = run_check(
        Artifact(path=str(p), check="exists", max_age_hours=max_age_hours), now=NOW
    )

    assert errors == []


def test_freshness_fails_just_past_boundary(tmp_path):
    p = tmp_path / "stale.txt"
    p.write_text("x")
    max_age_hours = 2
    stale_mtime = NOW - max_age_hours * 3600 - 1
    os.utime(p, (stale_mtime, stale_mtime))

    errors = run_check(
        Artifact(path=str(p), check="exists", max_age_hours=max_age_hours), now=NOW
    )

    assert len(errors) == 1
    assert str(p) in errors[0]
    assert "stale" in errors[0].lower()
    assert str(max_age_hours) in errors[0]


def test_stale_and_type_check_failure_both_accumulate(tmp_path):
    p = tmp_path / "stale.json"
    p.write_text(json.dumps({"a": 1}))
    max_age_hours = 1
    stale_mtime = NOW - max_age_hours * 3600 - 10
    os.utime(p, (stale_mtime, stale_mtime))

    errors = run_check(
        Artifact(path=str(p), check="json_has_keys", arg=["missing_key"], max_age_hours=max_age_hours),
        now=NOW,
    )

    assert len(errors) == 2
    joined = "\n".join(errors)
    assert "stale" in joined.lower()
    assert "missing_key" in joined


# --- nonempty ---------------------------------------------------------------


def test_nonempty_passes_with_content(tmp_path):
    p = tmp_path / "data.txt"
    p.write_text("some bytes")

    errors = run_check(Artifact(path=str(p), check="nonempty"), now=NOW)

    assert errors == []


def test_nonempty_fails_on_zero_bytes(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("")

    errors = run_check(Artifact(path=str(p), check="nonempty"), now=NOW)

    assert len(errors) == 1
    assert str(p) in errors[0]


# --- json_has_keys -----------------------------------------------------


def test_json_has_keys_passes_when_all_keys_present(tmp_path):
    p = tmp_path / "status.json"
    p.write_text(json.dumps({"ok": True, "ts": 123, "extra": "ignored"}))

    errors = run_check(Artifact(path=str(p), check="json_has_keys", arg=["ok", "ts"]), now=NOW)

    assert errors == []


def test_json_has_keys_lists_missing_keys(tmp_path):
    p = tmp_path / "status.json"
    p.write_text(json.dumps({"ok": True}))

    errors = run_check(
        Artifact(path=str(p), check="json_has_keys", arg=["ok", "ts", "extra"]), now=NOW
    )

    assert len(errors) == 1
    assert "ts" in errors[0]
    assert "extra" in errors[0]
    assert "ok" not in errors[0].split(":", 1)[1]  # 'ok' isn't reported missing


def test_json_has_keys_only_checks_top_level(tmp_path):
    p = tmp_path / "status.json"
    p.write_text(json.dumps({"nested": {"ok": True}}))

    errors = run_check(Artifact(path=str(p), check="json_has_keys", arg=["ok"]), now=NOW)

    assert len(errors) == 1
    assert "ok" in errors[0]


def test_json_parse_failure_reports_error(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not valid json")

    errors = run_check(Artifact(path=str(p), check="json_has_keys", arg=["ok"]), now=NOW)

    assert len(errors) == 1
    assert str(p) in errors[0]


def test_json_non_object_root_reports_error(tmp_path):
    p = tmp_path / "list.json"
    p.write_text(json.dumps([1, 2, 3]))

    errors = run_check(Artifact(path=str(p), check="json_has_keys", arg=["ok"]), now=NOW)

    assert len(errors) == 1
    assert str(p) in errors[0]


def test_json_has_keys_with_non_list_arg_reports_error_not_crash(tmp_path):
    # Artifact built directly (bypassing jobs.manifest's loader validation,
    # which only json_has_keys-typed arg lists ever pass through in
    # practice) -- run_check must defend itself locally rather than assume
    # the loader always stood between it and the caller.
    p = tmp_path / "status.json"
    p.write_text(json.dumps({"ok": True}))

    errors = run_check(Artifact(path=str(p), check="json_has_keys", arg=None), now=NOW)

    assert len(errors) == 1
    assert str(p) in errors[0]


# --- regex ---------------------------------------------------------------


def test_regex_passes_on_match(tmp_path):
    p = tmp_path / "status.log"
    p.write_text("2026-07-30 OK backup complete\n")

    errors = run_check(Artifact(path=str(p), check="regex", arg=r"^\d{4}-\d{2}-\d{2} OK"), now=NOW)

    assert errors == []


def test_regex_fails_on_no_match(tmp_path):
    p = tmp_path / "status.log"
    p.write_text("FAILED: disk full\n")

    errors = run_check(Artifact(path=str(p), check="regex", arg=r"^OK\b"), now=NOW)

    assert len(errors) == 1
    assert str(p) in errors[0]


def test_regex_matches_line_anchored_pattern_after_frontmatter_preamble(tmp_path):
    """Mirrors the real journal shape (Task 6.3 live finding, box-briefing).

    A `^`-anchored manifest pattern like `^##\\s+(Daily Briefing|Good
    Morning)` is a LINE assertion ("some line starts with one of these
    headings"), not a whole-file assertion ("the file's first character is
    #"). A journal file always opens with a YAML frontmatter block before
    the heading the check cares about -- so this must still pass. Without
    `re.MULTILINE` in the implementation, `^` only anchors to position 0 of
    the whole text (here, the frontmatter's `-`), and this exact case
    reproduces the false FAILED that job_verify.py hit live against the
    real box on 2026-07-30: a well-formed journal, wrongly reported as
    contract-violating.
    """
    p = tmp_path / "2026-07-30.md"
    p.write_text(
        "---\n"
        "date: 2026-07-30\n"
        "day: Thu\n"
        "type: daily\n"
        "---\n"
        "\n"
        "## Daily Summary\n"
        "- some accomplishment\n"
        "\n"
        "## Daily Briefing\n"
        "\n"
        "## Good Morning\n"
        "Some briefing text.\n"
    )

    errors = run_check(
        Artifact(path=str(p), check="regex", arg=r"^##\s+(Daily Briefing|Good Morning)"),
        now=NOW,
    )

    assert errors == []


def test_regex_reads_binary_garbage_as_replacement_chars_not_crash(tmp_path):
    p = tmp_path / "binary.log"
    p.write_bytes(b"\xff\xfe\x00\x01OK\xff")

    # should not raise even though the file isn't valid utf-8
    errors = run_check(Artifact(path=str(p), check="regex", arg=r"OK"), now=NOW)

    assert errors == []


# --- unreadable file (permissions) ----------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_unreadable_file_reports_error_not_crash(tmp_path):
    p = tmp_path / "secret.json"
    p.write_text(json.dumps({"ok": True}))
    p.chmod(0o000)
    try:
        errors = run_check(Artifact(path=str(p), check="json_has_keys", arg=["ok"]), now=NOW)
    finally:
        p.chmod(0o644)

    assert len(errors) == 1
    assert str(p) in errors[0]


# --- defensive: never raise, whatever the input --------------------------


def test_embedded_null_byte_in_path_reports_error_not_crash():
    # os.stat()/open() raise ValueError (not OSError) on embedded NUL bytes --
    # must still come back as an error string, not blow up run_check.
    errors = run_check(Artifact(path="bad\x00path.txt", check="exists"), now=NOW)

    assert len(errors) == 1


def test_invalid_regex_pattern_reports_error_not_crash(tmp_path):
    p = tmp_path / "status.log"
    p.write_text("some text")

    errors = run_check(Artifact(path=str(p), check="regex", arg="(unbalanced"), now=NOW)

    assert len(errors) == 1
    assert str(p) in errors[0]


# --- default now (real clock) ---------------------------------------------


def test_run_check_defaults_now_to_real_clock(tmp_path):
    p = tmp_path / "marker.txt"
    p.write_text("x")

    errors = run_check(Artifact(path=str(p), check="exists"))

    assert errors == []
