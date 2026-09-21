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


# "2026-09-16 08:46:13 +0200 Sleep    Entering Sleep state due to ..."
#
# NOT "Wake Requests". pmset writes one a second or two after nearly every
# Sleep -- dasd scheduling its NEXT maintenance wake -- and a bare `Wake\b`
# matches it, because the space after "Wake" is a word boundary. Every sleep was
# therefore "ended" within seconds: measured 2026-09-17 over one night, this
# parser counted 0.02h asleep where the log records 5.82h. WakeDetails and
# WakeTime were never at risk (no boundary before a letter); Requests was the
# one false wake, 227 of them against 228 Sleeps.
_LOG_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4})\s+(Sleep|Wake|DarkWake)"
    r"(?![ \t]*Requests)\b")


#: Roster kinds that are not promised to be up. The roster already says of the
#: workstation: "A WORKSTATION IS NOT A DEGRADED SERVER. It sleeps... Checks read
#: this to set expectations" -- this is the check finally reading it.
_SLEEPS = frozenset({"workstation", "laptop"})


def always_on(machine: str, roster: Path | None = None) -> bool:
    """Does the roster promise this machine is up? Unknown machines say yes.

    Defaulting to True keeps every existing contract behaving exactly as it did:
    a host only gets awake-time accounting by being declared to need it.
    """
    from .manifest import roster_path  # the data tree's roster, not this checkout's
    path = roster or roster_path()
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entry = (data.get("servers") or {}).get(machine) or {}
    except Exception:  # noqa: BLE001 -- an unreadable roster must not change behaviour
        _say_once(path, machine)
        return True
    declared = entry.get("always_on")
    if declared is not None:
        return bool(declared)
    return str(entry.get("kind") or "").lower() not in _SLEEPS


def node_class(machine: str, roster: Path | None = None) -> str:
    """"resident" or "visitor" -- whether this node's PRESENCE is promised.

    The roster has said "A WORKSTATION IS NOT A DEGRADED SERVER" for weeks and
    nothing enforced it, so the laptop accumulated twenty job contracts, most of
    them network duties with clock deadlines, and spent a month alerting about
    its own lid. This is that sentence as a type.

    The axis is not human-versus-agent: agents write from the laptop all day.
    It is whether the node can be relied on to be there. A resident can carry a
    duty with a deadline. A visitor joins, contributes and leaves -- a closed
    lid is a LEAVE, not a failure (the distinction SWIM, Cassandra and Consul
    all draw between `left` and `failed`) -- so it may carry only what is about
    itself, and nothing on it may be judged by the wall clock.

    Derived from `always_on`, so an unknown machine is a resident: a host only
    becomes a visitor by being declared one, which keeps every existing
    contract behaving exactly as it did.
    """
    return "resident" if always_on(machine, roster) else "visitor"


_WARNED: set[str] = set()


def _say_once(path: Path, machine: str) -> None:
    """Keep the safe default, but never take it SILENTLY.

    Falling back to "always on" is right when the roster cannot be read. Doing
    it without a word is how the 2026-09-16 sleep fix sat inert in the runner
    for a day while every staleness alert blamed the job. job_verify_notify.sh
    folds stderr into the alert it sends, so this line arrives inside the very
    alert it explains. Darwin only: that is the one platform where treating a
    machine as always-on changes an answer.
    """
    if sys.platform != "darwin" or str(path) in _WARNED:
        return
    _WARNED.add(str(path))
    print(f"job_verify: no readable machine roster at {path}; treating {machine!r} "
          f"as always-on, so sleep is NOT subtracted from artifact age", file=sys.stderr)


_PMSET = ("pmset", "-g", "log")


def _sleep_log() -> str:
    """The power log as text. Never raises.

    NOT decoded as strict UTF-8: pmset's log is not UTF-8. Assertion detail
    lines carry raw bytes -- on 2026-09-17 a WindowServer tickle line held 0xd5
    at byte 7,007,848 -- and `text=True` raised UnicodeDecodeError on it. The
    Sleep/Wake/DarkWake lines this module reads are ASCII, so replacing an
    undecodable byte elsewhere changes nothing it depends on.
    """
    try:
        out = subprocess.run(list(_PMSET), capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.decode("utf-8", errors="replace")


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


def in_dark_wake(*, log: str | None = None, systemstate: str | None = None) -> bool:
    """Is this Mac in a dark wake -- running, but with no display and nobody at it?

    A lid-closed laptop wakes every few minutes for Power Nap and dasd
    maintenance, for 2 to 45 seconds, with the network half up. launchd runs
    coalesced StartCalendarInterval jobs in exactly those windows: on
    2026-09-17 config-drift fired at 08:57:23 into a 45-second maintenance wake
    and lost an ssh call.

    Read the CURRENT state first: `pmset -g systemstate` lists the capabilities
    the machine holds right now, and a full wake holds Graphics; a dark wake
    holds only CPU and Network. Falling back to the power log's last event is a
    guess -- the log can end on `Sleep` while a process holding an assertion
    keeps the machine running in dark wake for hours, network fine.

    Anything that cannot be determined answers False: a job must never be
    silently skipped because this could not read the machine's state.
    """
    import os
    if sys.platform != "darwin" and systemstate is None and log is None:
        return False
    forced = os.environ.get("DATACORE_WAKE_STATE")
    if forced in ("full", "dark"):
        return forced == "dark"
    state = systemstate
    if state is None:
        try:
            out = subprocess.run(["pmset", "-g", "systemstate"], capture_output=True, timeout=10)
            state = out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            state = ""
    for line in state.splitlines():
        if line.startswith("Current System Capabilities"):
            return "Graphics" not in line
    text = _sleep_log() if log is None else log
    last = None
    for line in text.splitlines():
        m = _LOG_LINE.match(line.strip())
        if m:
            last = m.group(2)
    return last in ("DarkWake", "Sleep")


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
    try:
        return max(0.0, age - asleep_seconds_since(mtime, now=now, log=log))
    except Exception as exc:  # noqa: BLE001 -- see below
        # Sleep accounting only ever EXCUSES age; it must never become a new
        # way for a contract to fail. When it first ran for real (2026-09-17)
        # an undecodable byte in the power log raised here, job_verify turned
        # the exception into a failure for every job with a freshness bound,
        # and twelve alerts went out at once. Measure wall age instead -- the
        # behaviour before this module existed -- and say so.
        print(f"job_verify: sleep accounting failed ({type(exc).__name__}: {exc}); "
              f"using wall-clock age for {machine!r}", file=sys.stderr)
        return age
