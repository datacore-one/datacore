"""A test's refused message is not a lost alert (2026-10-12).

The nightly suite on the box runs the wrong-weekday promise eval, whose refused
message ("Your plan for Monday 2026-09-26: call the bank") was appended to the
box's real undelivered-alerts.jsonl. The morning sweep then told the owner, on
10-10, 10-11 and 10-12, that an alert had reached nobody. Tests write that list
in their own temp folder.
"""
from pathlib import Path

import tg_format


def test_the_undelivered_list_is_not_the_real_one():
    real = Path.home() / ".datacore" / "state" / "undelivered-alerts.jsonl"
    assert tg_format.undelivered_log() != real
