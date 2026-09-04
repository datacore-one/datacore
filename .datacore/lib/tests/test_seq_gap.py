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
    rows = S.scan_space(space, grace_min=90, now_ms=now_ms)
    assert rows[0]["gap"] == 0 and rows[0]["pending"] == 1


def test_an_event_older_than_the_grace_is_a_real_gap(tmp_path):
    now_ms = time.time() * 1000
    space, log = _space(tmp_path, now_ms)
    log.write_text(log.read_text() + _event(2, 120, now_ms) + "\n")        # written two hours ago
    rows = S.scan_space(space, grace_min=90, now_ms=now_ms)
    assert rows[0]["gap"] == 1 and rows[0]["pending"] == 0


def test_a_mixed_backlog_counts_as_a_gap(tmp_path):
    now_ms = time.time() * 1000
    space, log = _space(tmp_path, now_ms)
    log.write_text(log.read_text() + _event(2, 120, now_ms) + "\n" + _event(3, 2, now_ms) + "\n")
    rows = S.scan_space(space, grace_min=90, now_ms=now_ms)
    assert rows[0]["gap"] == 2, "one old event makes the whole backlog overdue"


def test_a_published_log_has_no_gap_and_nothing_pending(tmp_path):
    now_ms = time.time() * 1000
    space, log = _space(tmp_path, now_ms)
    rows = S.scan_space(space, grace_min=90, now_ms=now_ms)
    assert rows[0]["gap"] == 0 and rows[0]["pending"] == 0
