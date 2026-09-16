"""Age measured in the time a machine was actually awake.

A contract says "this artifact must be no older than N hours". On an always-on
host that is a liveness signal: the job had N hours to run and did not. On a
LAPTOP it is mostly a record of the lid being shut. The mac sleeps roughly a
quarter of every week -- measured 2026-09-16, 117 asleep hours out of the 472
since boot -- so an hourly job's artifact goes "stale" during any ordinary
night, every night, and the alert says nothing except that the machine was off.

Running the job by hand clears it until the next sleep, which is why this has
been "fixed" repeatedly without being fixed.

So for a machine the roster declares `always_on: false`, staleness is measured
in AWAKE seconds: wall age minus the time the machine spent asleep since the
artifact was written. A laptop that was open and running, and still did not
produce the artifact, is late exactly as before -- which is the signal the
contract was always meant to carry.

Darwin only, because that is where the laptop is; everywhere else this reports
no sleep and the behaviour is byte-for-byte what it was.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_ROSTER = Path(__file__).resolve().parents[1].parent / "registry" / "infrastructure.yaml"

# "2026-09-16 08:46:13 +0200 Sleep    Entering Sleep state due to ..."
_LOG_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4})\s+(Sleep|Wake|DarkWake)\b")


#: Roster kinds that are not promised to be up. The roster already says of the
#: workstation: "A WORKSTATION IS NOT A DEGRADED SERVER. It sleeps... Checks read
#: this to set expectations" -- this is the check finally reading it.
_SLEEPS = frozenset({"workstation", "laptop"})


def always_on(machine: str, roster: Path | None = None) -> bool:
    """Does the roster promise this machine is up? Unknown machines say yes.

    Defaulting to True keeps every existing contract behaving exactly as it did:
    a host only gets awake-time accounting by being declared to need it.
    """
    try:
        import yaml
        data = yaml.safe_load((roster or _ROSTER).read_text(encoding="utf-8")) or {}
        entry = (data.get("servers") or {}).get(machine) or {}
    except Exception:  # noqa: BLE001 -- an unreadable roster must not change behaviour
        return True
    declared = entry.get("always_on")
    if declared is not None:
        return bool(declared)
    return str(entry.get("kind") or "").lower() not in _SLEEPS


def _sleep_log() -> str:
    try:
        out = subprocess.run(["pmset", "-g", "log"], capture_output=True, text=True,
                             timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def asleep_seconds_since(since: float, *, now: float | None = None,
                         log: str | None = None) -> float:
    """Seconds this machine spent asleep between `since` and `now`.

    Pairs each `Sleep` with the next `Wake`/`DarkWake`, clipping every interval
    to the window. A trailing `Sleep` with no wake cannot be observed from a
    process that is running, so it is ignored rather than guessed at.
    """
    if sys.platform != "darwin":
        return 0.0
    now = time.time() if now is None else now
    if now <= since:
        return 0.0
    text = _sleep_log() if log is None else log
    if not text:
        return 0.0

    events: list[tuple[float, str]] = []
    for line in text.splitlines():
        m = _LOG_LINE.match(line.strip())
        if not m:
            continue
        try:
            stamp = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S %z").timestamp()
        except ValueError:
            continue
        events.append((stamp, m.group(2)))
    events.sort(key=lambda e: e[0])

    total = 0.0
    asleep_at: float | None = None
    for stamp, kind in events:
        if kind == "Sleep":
            if asleep_at is None:
                asleep_at = stamp
        elif asleep_at is not None:
            lo, hi = max(asleep_at, since), min(stamp, now)
            if hi > lo:
                total += hi - lo
            asleep_at = None
    return min(total, now - since)


def awake_age(mtime: float, machine: str, *, now: float | None = None,
              roster: Path | None = None, log: str | None = None) -> float:
    """Artifact age in seconds, counting only time the machine could have run.

    `log` is passed through for the same reason `asleep_seconds_since` takes it:
    without it this function reads the REAL `pmset` log, so a test pinning a
    historical `now` silently measures whatever this machine happened to do in
    that window. That is how `test_time_awake_still_counts` came to fail by
    seven seconds -- the mac really had slept seven seconds inside the window
    the fixture chose.
    """
    now = time.time() if now is None else now
    age = now - mtime
    if age <= 0 or always_on(machine, roster):
        return age
    return max(0.0, age - asleep_seconds_since(mtime, now=now, log=log))
