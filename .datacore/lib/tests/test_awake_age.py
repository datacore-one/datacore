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
    assert awake.awake_age(written, "laptop-fixture", now=now, log=NIGHT,
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


def test_the_runner_layout_finds_the_roster_in_the_data_tree(tmp_path):
    """The bug was never in the arithmetic. It was in WHERE the roster is read.

    The roster is gitignored, so it exists only in a host's data tree. Scheduled
    jobs run from ~/.datacore/v2-runner, a checkout of tracked files only. The
    reader looked next to its own code, found nothing there, and fell back to
    "always on" -- so the sleep-aware fix that shipped 2026-09-16 passed every
    test run from ~/Data and was inert in the one place the verifier runs.

    This reproduces that layout for real: the jobs package copied into a
    code-only tree with no roster, the roster only in a separate data tree, run
    in a subprocess so nothing about THIS checkout can leak in.
    """
    import shutil
    import subprocess

    code = tmp_path / "v2-runner"
    (code / ".datacore" / "lib").mkdir(parents=True)
    shutil.copytree(LIB / "jobs", code / ".datacore" / "lib" / "jobs",
                    ignore=shutil.ignore_patterns("__pycache__"))
    assert not (code / ".datacore" / "registry" / "infrastructure.yaml").exists()

    data = tmp_path / "Data"
    (data / ".datacore" / "registry").mkdir(parents=True)
    (data / ".datacore" / "registry" / "infrastructure.yaml").write_text(
        "servers:\n  mac:\n    kind: workstation\n  winston:\n    kind: server\n")

    probe = ("from jobs.awake import always_on; from jobs.manifest import known_machines;"
             "print(always_on('mac'), always_on('winston'), sorted(known_machines() or []))")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"),
           "DATACORE_ROOT": str(data)}
    out = subprocess.run([sys.executable, "-c", probe], cwd=code / ".datacore" / "lib",
                         env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["False", "True", "['mac',", "'winston']"], out.stdout


def test_an_explicit_data_root_without_a_roster_does_not_fall_through(tmp_path, monkeypatch):
    """$DATACORE_ROOT is authoritative. A test or a scratch tree that sets it must
    never silently read the real machine's roster from ~/Data instead."""
    from jobs.manifest import roster_path
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path))
    assert roster_path() == tmp_path / ".datacore" / "registry" / "infrastructure.yaml"
    assert awake.always_on("mac") is True


# Real lines from this Mac, 2026-09-17, lid closed on battery. The shape that
# matters: every Sleep is followed seconds later by a `Wake Requests` line, which
# is dasd SCHEDULING a future wake, not a wake.
CLAMSHELL_NIGHT = """\
2026-09-17 02:52:28 +0200 Sleep               \tEntering Sleep state due to 'Clamshell Sleep':TCPKeepAlive=active
2026-09-17 02:52:29 +0200 Wake Requests       \t[*process=dasd request=SleepService deltaSecs=976 wakeAt=2026-09-17 03:08:46]
2026-09-17 03:08:46 +0200 DarkWake            \tDarkWake from Deep Idle [CDNP] : due to rtc/SleepService Using BATT (Charge:95%) 2 secs
2026-09-17 03:08:47 +0200 WakeDetails         \tDriverReason:rtc
2026-09-17 03:08:47 +0200 WakeTime            \tWakeTime: 1.2 sec
2026-09-17 03:08:48 +0200 Sleep               \tEntering Sleep state due to 'Sleep Service Back to Sleep':TCPKeepAlive=active
2026-09-17 03:08:51 +0200 Wake Requests       \t[*process=dasd request=SleepService deltaSecs=932 wakeAt=2026-09-17 03:24:23]
2026-09-17 09:03:08 +0200 Wake                \tWake from Deep Idle [CDNP] : due to UserActivity Using AC
"""


def test_a_wake_request_is_not_a_wake():
    since = 1789606348.0            # 2026-09-17 02:52:28 +0200 -- the Sleep itself
    now = 1789628588.0              # 2026-09-17 09:03:08 +0200 -- the real Wake
    asleep = awake.asleep_seconds_since(since, now=now, log=CLAMSHELL_NIGHT)
    # Two real sleeps: 02:52:28 -> 03:08:46 and 03:08:48 -> 09:03:08. The
    # two-second DarkWake between them is the only awake time in the window.
    assert asleep == pytest.approx((now - since) - 2, abs=1)


def test_a_power_log_that_is_not_utf8_is_still_read(monkeypatch, tmp_path):
    """pmset's log carries raw bytes in assertion details. Reading it must not raise.

    Real shape, 2026-09-17: byte 0xd5 inside a WindowServer line, ~7MB in. A
    strict decode raised, and every freshness-checked job on the mac failed.
    """
    log = tmp_path / "pmset.log"
    log.write_bytes(
        b"2026-09-17 02:52:28 +0200 Sleep               \tEntering Sleep state\n"
        b"   pid 409(WindowServer): UserIsActive named: \xd5 tickle\n"
        b"2026-09-17 09:03:08 +0200 Wake                \tWake from Deep Idle\n")
    monkeypatch.setattr(awake, "_PMSET", ("/bin/cat", str(log)))
    monkeypatch.setenv("DATACORE_POWER_ASL_DIR", "")
    monkeypatch.setattr(awake, "_MEMO", None)
    text = awake._sleep_log()
    assert len(text.splitlines()) == 3
    asleep = awake.asleep_seconds_since(1789606348.0, now=1789628588.0)
    assert asleep == pytest.approx(1789628588.0 - 1789606348.0, abs=1)


def test_sleep_accounting_that_breaks_falls_back_to_wall_age(monkeypatch, tmp_path, capsys):
    roster = tmp_path / "infrastructure.yaml"
    roster.write_text("servers:\n  mac:\n    kind: workstation\n")

    def boom(*_a, **_k):
        raise UnicodeDecodeError("utf-8", b"\xd5", 0, 1, "invalid continuation byte")
    monkeypatch.setattr(awake, "asleep_seconds_since", boom)
    assert awake.awake_age(1000.0, "mac", now=4600.0, roster=roster) == 3600.0
    assert "using wall-clock age" in capsys.readouterr().err


def test_a_regex_failure_says_what_the_artifact_actually_said(tmp_path):
    """The cause has to reach the alert, not just the pattern that missed."""
    from jobs.checks import run_check
    from jobs.manifest import Artifact
    status = tmp_path / "phase1-cycle-status.txt"
    status.write_text("FAIL phase1-cycle 2026-09-16T03:25:00Z rc=127 "
                      "(runtime init failed: no usable Python with PyYAML and org-workspace)\n")
    errors = run_check(Artifact(path=str(status), check="regex", arg="^OK phase1-cycle"))
    assert len(errors) == 1
    assert "did not match" in errors[0] and "no usable Python" in errors[0]


def test_the_asl_day_files_are_read_in_pmsets_format(tmp_path, monkeypatch):
    """pmset -g log took 63 s on 2026-09-23 -- past its timeout -- and every
    caller silently lost the machine's sleep. The ASL day files carry the same
    records; rendered in pmset's format, the existing parsers read them."""
    d = tmp_path / "asl"; d.mkdir()
    (d / "2026.09.23.asl").write_bytes(b"x")
    monkeypatch.setenv("DATACORE_POWER_ASL_DIR", str(d))
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(awake, "_MEMO", None)
    monkeypatch.setattr(awake, "_asl_events",
                        lambda p: [[1790154844, "Sleep"], [1790154887, "DarkWake"], [1790154908, "Wake"]])
    assert awake.last_full_wake(log=awake._sleep_log()) == 1790154908
    # A day file that cannot be read is not an empty day: fall back, never guess.
    monkeypatch.setattr(awake, "_MEMO", None)
    monkeypatch.setattr(awake, "_asl_events", lambda p: None)
    monkeypatch.setattr(awake, "_pmset_log", lambda: "FALLBACK")
    (d / "2026.09.24.asl").write_bytes(b"y")
    assert awake._sleep_log() == "FALLBACK"
