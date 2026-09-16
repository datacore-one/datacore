"""Artifact age on a laptop is measured in the time it could have run.

`max_age_hours` means "the job had this long to run and did not". On the mac
that read as "the lid was shut": 117 of the 472 hours since boot were asleep
(2026-09-16), so the hourly cycle's artifact went stale every night and the
alert carried no information. Running the job by hand cleared it until the next
sleep, which is why it was "fixed" repeatedly and stayed broken.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import pytest  # noqa: E402

from jobs import awake  # noqa: E402

# One night: asleep 23:00 -> 08:00, in pmset's own format.
NIGHT = (
    "2026-09-15 23:00:00 +0000 Sleep               Entering Sleep state\n"
    "2026-09-16 08:00:00 +0000 Wake                Wake from Deep Idle\n"
)
SLEPT_AT = 1789513200.0   # 2026-09-15 23:00:00 UTC
WOKE_AT = 1789545600.0    # 2026-09-16 08:00:00 UTC


@pytest.fixture(autouse=True)
def _darwin(monkeypatch):
    monkeypatch.setattr(awake.sys, "platform", "darwin")


def test_a_night_asleep_is_not_age():
    """An artifact written just before the lid shut is minutes old, not hours."""
    written = SLEPT_AT - 600                 # ten minutes before sleeping
    now = WOKE_AT + 600                      # ten minutes after waking
    slept = awake.asleep_seconds_since(written, now=now, log=NIGHT)
    assert slept == pytest.approx(WOKE_AT - SLEPT_AT)
    awake_seconds = (now - written) - slept
    assert awake_seconds == pytest.approx(1200)
    assert (now - written) / 3600 > 9, "precondition: wall age would have alerted"


def test_time_awake_still_counts():
    """A job late while the machine was RUNNING must still fail."""
    written = WOKE_AT                        # written at wake
    now = WOKE_AT + 5 * 3600                 # five awake hours later
    assert awake.asleep_seconds_since(written, now=now, log=NIGHT) == 0
    assert awake.awake_age(written, "laptop-fixture", now=now,
                           roster=_roster("laptop")) == pytest.approx(5 * 3600)


def test_sleep_before_the_window_is_not_subtracted():
    written = WOKE_AT + 3600
    now = written + 3600
    assert awake.asleep_seconds_since(written, now=now, log=NIGHT) == 0


def test_an_unfinished_sleep_is_not_guessed_at():
    """A Sleep with no Wake cannot be observed by a running process."""
    log = "2026-09-15 23:00:00 +0000 Sleep               Entering Sleep state\n"
    assert awake.asleep_seconds_since(SLEPT_AT - 600, now=SLEPT_AT + 600, log=log) == 0


def _roster(kind: str) -> Path:
    import tempfile
    p = Path(tempfile.mkdtemp()) / "infrastructure.yaml"
    p.write_text(f"servers:\n  laptop-fixture:\n    kind: {kind}\n"
                 f"  box-fixture:\n    kind: server\n", encoding="utf-8")
    return p


@pytest.mark.parametrize("kind,expected", [("workstation", False), ("laptop", False),
                                           ("server", True)])
def test_roster_kind_decides_who_gets_awake_time(kind, expected):
    assert awake.always_on("laptop-fixture", _roster(kind)) is expected


def test_an_unknown_machine_is_treated_as_always_on():
    """Defaulting the other way would silently soften every unlisted host."""
    assert awake.always_on("not-in-roster", _roster("workstation")) is True


def test_an_unreadable_roster_changes_nothing():
    assert awake.always_on("laptop-fixture", Path("/nonexistent/roster.yaml")) is True


def test_a_server_never_discounts_sleep(monkeypatch):
    written = SLEPT_AT - 600
    now = WOKE_AT + 600
    monkeypatch.setattr(awake, "_sleep_log", lambda: NIGHT)
    assert awake.awake_age(written, "box-fixture", now=now,
                           roster=_roster("server")) == pytest.approx(now - written)
