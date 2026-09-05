"""The scoreboard is six files and six rules; a day passes or it does not (2026-09-05)."""
import datetime as dt, importlib.util, json, os, pathlib, time

LIB = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rs", LIB / "reliability_scoreboard.py")
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)


def _good_box(tmp_path, day):
    state, cos = tmp_path / "state", tmp_path / "cos"
    (cos / "alerts").mkdir(parents=True); state.mkdir()
    now = time.time()
    (cos / "backup.log").write_text("2026-09-05T02:30:20+00:00 backup ok: local=184M (x.tar.gz) kept=14/100 offsite=ok remote_kept=17/30\n")
    (cos / "restore-check.log").write_text("2026-09-05T15:56:59+00:00 restore-check ok: snapshot=x engrams=3477 live=3477\n")
    (state / "fleet-sync.log").write_text("1-datafund  [main]\n  clean (pulled)\nOK: every repo converged\n")
    (state / "cos-uptime.log").write_text("".join(f"{day}T{h:02d}:00:01Z UP mcp=connected\n" for h in range(24)))
    (cos / "verify-daily.log").write_text(f"{day} 07:10 OK  0 failed systemd units\n{day} 07:10 OK  heartbeat cron scheduled\n")
    (cos / "alerts" / ".sent-log").write_text(f"{int(now)} unit http=200 unit-failed:cos-alert-selftest.service\n")
    return state, cos


def test_a_clean_day_passes_and_starts_the_streak(tmp_path, monkeypatch):
    day = "2026-09-05"; state, cos = _good_box(tmp_path, day)
    monkeypatch.setattr(M, "_failed_units", lambda: set())
    r = M.compute(day, state, cos)
    assert r["pass"] and r["streak"] == 1 and r["level"] == 3, M.line(r)


def test_the_streak_counts_consecutive_passing_days_and_a_fail_resets_it(tmp_path, monkeypatch):
    day = "2026-09-05"; state, cos = _good_box(tmp_path, day)
    monkeypatch.setattr(M, "_failed_units", lambda: set())
    (state / "reliability-scoreboard.log").write_text("2026-09-04 PASS streak=6 level=3 R1=ok\n")
    r = M.compute(day, state, cos)
    assert (r["streak"], r["level"]) == (7, 4)
    (state / "reliability-scoreboard.log").write_text("2026-09-04 PASS streak=29 level=4 R1=ok\n")
    assert M.compute(day, state, cos)["level"] == 5
    (state / "reliability-scoreboard.log").write_text("2026-09-03 PASS streak=29 level=4 R1=ok\n")  # a day is missing
    assert M.compute(day, state, cos)["streak"] == 1, "a missing day breaks the streak"
    (cos / "interventions.log").write_text(f"{day} 14:02 gregor: edited cos.env by hand\n")
    r = M.compute(day, state, cos)
    assert not r["pass"] and r["streak"] == 0 and not r["checks"]["R3"]["ok"]


def test_each_failure_class_fails_its_own_condition(tmp_path, monkeypatch):
    day = "2026-09-05"; state, cos = _good_box(tmp_path, day)
    monkeypatch.setattr(M, "_failed_units", lambda: {"datacore-fleet-sync.service"})
    r = M.compute(day, state, cos)
    assert not r["checks"]["R2"]["ok"] and "without a sent alert" in r["checks"]["R2"]["note"]
    (state / "job-verify-recurrence.json").write_text(json.dumps({"mac-id-churn": {"consecutive": 48, "recurring": True}}))
    assert "mac-id-churn" in M.compute(day, state, cos)["checks"]["R1"]["note"]
    (state / "fleet-sync.log").write_text("FAIL: 1 repo(s) have pull conflicts\n")
    assert not M.compute(day, state, cos)["checks"]["R4"]["ok"]
    (cos / "verify-daily.log").write_text(f"{day} 07:10 FAIL 1 failed units\n")
    assert not M.compute(day, state, cos)["checks"]["R6"]["ok"]
    (state / "cos-uptime.log").write_text(f"{day}T01:00:01Z DOWN\n" + "".join(f"{day}T{h:02d}:00:01Z UP\n" for h in range(2, 24)))
    assert not M.compute(day, state, cos)["checks"]["R5"]["ok"]


def test_r5_is_measured_from_the_probers_hits_when_no_probe_log_is_here(tmp_path, monkeypatch):
    day = "2026-09-05"; state, cos = _good_box(tmp_path, day)
    (state / "cos-uptime.log").unlink()
    now = dt.datetime.fromisoformat(day).timestamp() + 8 * 3600          # 08:00 -> 32 probes expected
    hits = "\n".join(f"Sep 05 0{i//4}:{(i%4)*15:02d}:01 bridge python[1]: INFO:     100.101.159.42:5 - \"GET /health HTTP/1.1\" 200 OK" for i in range(32))
    monkeypatch.setattr(M, "_journal", lambda args: hits if "-u" in args else "")
    monkeypatch.setattr(M, "_failed_units", lambda: set())
    r = M.compute(day, state, cos, now=now)
    assert r["checks"]["R5"]["ok"] and "32/32" in r["checks"]["R5"]["note"], r["checks"]["R5"]
    few = "\n".join(hits.splitlines()[:20])
    monkeypatch.setattr(M, "_journal", lambda args: few if "-u" in args else "")
    r = M.compute(day, state, cos, now=now)
    assert not r["checks"]["R5"]["ok"] and "20/32" in r["checks"]["R5"]["note"]


def test_r2_sees_a_unit_that_failed_and_recovered_during_the_day(tmp_path, monkeypatch):
    day = "2026-09-05"; state, cos = _good_box(tmp_path, day)
    journal = "Sep 05 03:00:01 bridge systemd[1]: v2-verify.service: Failed with result 'exit-code'.\n"
    monkeypatch.setattr(M, "_journal", lambda args: journal if "-u" not in args else "")
    monkeypatch.setattr(M, "_failed_units", lambda: set())          # green again by scoreboard time
    r = M.compute(day, state, cos)
    assert not r["checks"]["R2"]["ok"] and "v2-verify.service" in r["checks"]["R2"]["note"]
    (cos / "alerts" / ".sent-log").write_text(f"{int(dt.datetime.fromisoformat(day).timestamp()) + 3600} unit http=200 unit-failed:v2-verify.service\n")
    r = M.compute(day, state, cos)
    assert r["checks"]["R2"]["ok"], r["checks"]["R2"]
