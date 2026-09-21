"""seq-gap: an event younger than the publish interval is pending, not a gap."""
import importlib.util, json, pathlib, subprocess, time

ROOT = pathlib.Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("sg", ROOT / ".datacore" / "lib" / "detectors" / "seq_gap.py")
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)


def _event(seq, age_min, now_ms):
    return json.dumps({"seq": seq, "hlc": f"{int(now_ms - age_min * 60000)}.0000.mac", "type": "t"})


def _space(tmp_path, now_ms):
    origin = tmp_path / "origin.git"; _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    space = tmp_path / "1-space"; _git(tmp_path, "clone", "-q", str(origin), str(space))
    _git(space, "config", "user.email", "t@t"); _git(space, "config", "user.name", "t")
    ev = space / ".datacore" / "events"; ev.mkdir(parents=True)
    (ev / "mac.jsonl").write_text(_event(1, 600, now_ms) + "\n")
    _git(space, "add", "-A"); _git(space, "commit", "-q", "-m", "base"); _git(space, "push", "-q", "-u", "origin", "main")
    return space, ev / "mac.jsonl"


def test_a_fresh_unpublished_event_is_pending_not_a_gap(tmp_path):
    now_ms = time.time() * 1000
    space, log = _space(tmp_path, now_ms)
    log.write_text(log.read_text() + _event(2, 3, now_ms) + "\n")          # written 3 minutes ago
    rows = S.scan_space(space, grace_min=90, now_ms=now_ms, sleep_log="")
    assert rows[0]["gap"] == 0 and rows[0]["pending"] == 1


def test_an_event_older_than_the_grace_is_a_real_gap(tmp_path):
    now_ms = time.time() * 1000
    space, log = _space(tmp_path, now_ms)
    log.write_text(log.read_text() + _event(2, 120, now_ms) + "\n")        # written two hours ago
    rows = S.scan_space(space, grace_min=90, now_ms=now_ms, sleep_log="")
    assert rows[0]["gap"] == 1 and rows[0]["pending"] == 0


def test_a_mixed_backlog_counts_as_a_gap(tmp_path):
    now_ms = time.time() * 1000
    space, log = _space(tmp_path, now_ms)
    log.write_text(log.read_text() + _event(2, 120, now_ms) + "\n" + _event(3, 2, now_ms) + "\n")
    rows = S.scan_space(space, grace_min=90, now_ms=now_ms, sleep_log="")
    assert rows[0]["gap"] == 2, "one old event makes the whole backlog overdue"


def test_a_published_log_has_no_gap_and_nothing_pending(tmp_path):
    now_ms = time.time() * 1000
    space, log = _space(tmp_path, now_ms)
    rows = S.scan_space(space, grace_min=90, now_ms=now_ms, sleep_log="")
    assert rows[0]["gap"] == 0 and rows[0]["pending"] == 0


def _laptop(monkeypatch):
    """Declare this machine one that sleeps, and on a platform with a power log.

    `always_on` returns True for a machine the roster does not name, so in an
    isolated test tree every host looks like a server and no sleep accounting
    applies -- which is correct as a default (a host only gets this by being
    declared to need it) and useless as a test of the laptop case.
    """
    from jobs import awake
    monkeypatch.setattr(awake.sys, "platform", "darwin")
    monkeypatch.setattr(awake, "always_on", lambda machine, roster=None: False)


def test_a_night_asleep_does_not_turn_a_pending_event_into_a_gap(tmp_path, monkeypatch):
    """The month-long false alarm, stated as a test.

    The grace is a budget for the PUBLISHER, which runs hourly. A laptop asleep
    from 23:00 to 08:00 spends none of it: an event written just before the lid
    shut is ten hours old by the wall clock next morning, but the publisher was
    scheduled for about ten minutes of that. Reported as a gap it asserts "your
    work is not anywhere but this disk", which was not true and could not be
    acted on -- and opening the lid and publishing by hand cleared it until the
    next night, which is why it survived being fixed repeatedly.
    """
    _laptop(monkeypatch)
    now_ms = time.time() * 1000
    space, log = _space(tmp_path, now_ms)
    log.write_text(log.read_text() + _event(2, 600, now_ms) + "\n")   # ten hours ago

    # Asleep for all but twenty minutes of those ten hours, in pmset's format.
    import datetime as _dt
    def stamp(mins_ago):
        d = _dt.datetime.fromtimestamp(now_ms / 1000 - mins_ago * 60, _dt.timezone.utc)
        return d.strftime("%Y-%m-%d %H:%M:%S +0000")
    asleep = (f"{stamp(590)} Sleep               Entering Sleep state\n"
              f"{stamp(20)} Wake                Wake from Deep Idle\n")

    rows = S.scan_space(space, grace_min=90, now_ms=now_ms, sleep_log=asleep)
    assert rows[0]["gap"] == 0, "nine hours asleep is not nine hours of not publishing"
    assert rows[0]["pending"] == 1


def test_a_machine_that_stayed_awake_still_reports_the_gap(tmp_path, monkeypatch):
    """Sleep accounting only ever EXCUSES age. It must never become a new way
    to hide a real gap on a machine that was up the whole time."""
    _laptop(monkeypatch)
    now_ms = time.time() * 1000
    space, log = _space(tmp_path, now_ms)
    log.write_text(log.read_text() + _event(2, 600, now_ms) + "\n")
    rows = S.scan_space(space, grace_min=90, now_ms=now_ms, sleep_log="")
    assert rows[0]["gap"] == 1
