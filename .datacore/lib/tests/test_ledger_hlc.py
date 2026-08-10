"""Tests for ledger.hlc - Hybrid Logical Clock."""

from ledger.hlc import parse, tick


def test_tick_monotonic_same_ms():
    """Within the same millisecond, counter increments to ensure monotonicity."""
    a = tick("mac", None, _now_ms=1000)
    b = tick("mac", a, _now_ms=1000)
    assert b > a and parse(b)[1] == parse(a)[1] + 1


def test_tick_advances_with_clock():
    """When clock advances, counter resets to 0."""
    a = tick("mac", None, _now_ms=1000)
    b = tick("mac", a, _now_ms=2000)
    assert parse(b) == (2000, 0, "mac")


def test_tick_never_regresses_when_clock_goes_back():
    """When clock goes backward, timestamp holds steady but counter increments."""
    a = tick("mac", None, _now_ms=2000)
    b = tick("mac", a, _now_ms=1000)
    assert b > a and parse(b)[0] == 2000


def test_actor_tiebreak_sorts_lexicographically():
    """Actors with same timestamp sort lexicographically."""
    assert tick("box", None, _now_ms=5) < tick("mac", None, _now_ms=5)


def test_tick_counter_at_max_still_succeeds():
    """Counter 9999 is still representable in the 4-digit width -- must not
    raise. (Boundary check: the guard trips ABOVE 9999, not AT it.)"""
    last = f"{1000:013d}.9998.mac"
    stamp = tick("mac", last, _now_ms=1000)
    assert parse(stamp) == (1000, 9999, "mac")


def test_tick_counter_overflow_raises_value_error():
    """A 5th-digit counter (>9999) would sort lexicographically BEFORE
    '9999' as a string ('1' < '9'), silently breaking the monotonic
    ordering the whole HLC scheme depends on -- tick() must refuse to
    emit such a stamp rather than produce a corrupt one."""
    last = f"{1000:013d}.9999.mac"
    raised = False
    try:
        tick("mac", last, _now_ms=1000)
    except ValueError:
        raised = True
    assert raised, "expected ValueError for counter overflow (>9999)"
