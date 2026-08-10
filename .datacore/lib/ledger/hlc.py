"""Hybrid logical clock: sortable stamps, monotonic per actor, no regressions."""
import time


def tick(actor: str, last: str | None = None, _now_ms: int | None = None) -> str:
    """Generate a sortable timestamp stamp that is monotonic per actor.

    Args:
        actor: Actor identifier (e.g., hostname)
        last: Previous stamp returned by tick (or None for first call)
        _now_ms: Current time in milliseconds (for testing; if None, uses system time)

    Returns:
        A sortable stamp formatted as "{pt_ms:013d}.{counter:04d}.{actor}"
    """
    now = int(time.time() * 1000) if _now_ms is None else _now_ms
    if last is None:
        pt, c = now, 0
    else:
        lpt, lc, _ = parse(last)
        if now > lpt:
            pt, c = now, 0
        else:
            pt, c = lpt, lc + 1
    if c > 9999:
        raise ValueError(
            f"HLC counter overflow: counter {c} exceeds the 4-digit width "
            "(max 9999) that keeps stamps lexicographically sortable within "
            "a single millisecond bucket -- refusing to emit a stamp that "
            "would break ordering"
        )
    return f"{pt:013d}.{c:04d}.{actor}"


def parse(stamp: str) -> tuple[int, int, str]:
    """Parse a stamp into its components.

    Args:
        stamp: A stamp string formatted as "{pt_ms:013d}.{counter:04d}.{actor}"

    Returns:
        A tuple of (physical_time_ms, counter, actor)
    """
    pt, c, actor = stamp.split(".", 2)
    return int(pt), int(c), actor
