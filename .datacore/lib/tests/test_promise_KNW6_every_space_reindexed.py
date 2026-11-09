"""KNW-6: search finds any note, journal entry or task in every space,
including anything written yesterday.

The nightly reindex ran `zettel_processor.py --space personal` only, so
datafund, datacore, meridian, firm and the rest were last indexed on
2026-09-19 (found 2026-09-26). The job must index every discovered space,
and one space failing must not stop the others.

Seeded failure: index a fixed list, or stop at the first failing space.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import reindex_all_spaces as R  # noqa: E402


def test_every_discovered_space_is_indexed_and_one_failure_does_not_stop_the_rest(monkeypatch):
    spaces = ["personal", "datafund", "datacore", "meridian"]
    monkeypatch.setattr(R, "discover", lambda: list(spaces))
    ran = []

    def fake_index(space):
        ran.append(space)
        return 1 if space == "datafund" else 0

    monkeypatch.setattr(R, "index_space", fake_index)
    rc = R.main([])
    assert ran == spaces
    assert rc == 1, "a failed space is reported through the exit code"


def test_all_green_exits_zero(monkeypatch):
    monkeypatch.setattr(R, "discover", lambda: ["personal", "firm"])
    monkeypatch.setattr(R, "index_space", lambda space: 0)
    assert R.main([]) == 0
