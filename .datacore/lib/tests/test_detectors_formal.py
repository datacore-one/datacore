"""Detectors cluster: counterexamples found by DatacoreSpec/Detectors.lean, replayed
against the real Python (2026-09-23).

These are alarms. The failure that matters is a false "ok", so every test here is
a case the pre-fix code reported as healthy (or let silently heal) while the
condition it exists to catch was present.
"""
import contextlib
import datetime as dt
import importlib.util
import io
import json
import pathlib
import subprocess
import sys

LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB)); sys.path.insert(0, str(LIB / "detectors"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AP = _load("ap_formal", LIB / "detectors" / "actor_presence.py")
SG = _load("sg_formal", LIB / "detectors" / "seq_gap.py")
IC = _load("ic_formal", LIB / "detectors" / "id_churn.py")
RS = _load("rs_formal", LIB / "reliability_scoreboard.py")
TR = _load("tr_formal", LIB / "today_registry.py")


def _ev(seq):
    return json.dumps({"seq": seq, "hlc": f"{1_700_000_000_000 + seq}.0.x"}) + "\n"


# ---------------------------------------------------------------- actor_presence

def _ap_root(tmp_path):
    root = tmp_path / "root"
    (root / ".datacore" / "registry").mkdir(parents=True)
    (root / ".datacore" / "registry" / "infrastructure.yaml").write_text(
        "servers:\n  mac:\n    ledger_actors: [alice]\n")
    for sp in ("0-a", "1-b"):
        (root / sp / ".datacore" / "events").mkdir(parents=True)
    return root


def _ap_run(root, state, monkeypatch):
    monkeypatch.setattr(AP, "STATE", state)
    monkeypatch.setattr(sys, "argv", ["ap", "--root", str(root), "--json"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = AP.main()
    return rc, json.loads(buf.getvalue())["rows"][0]["status"]


def test_ap_a_log_with_no_readable_events_is_missing_not_ok(tmp_path, monkeypatch):
    """Docstring: MISSING = 'a rostered actor has no log, or a log with no readable events'."""
    root, st = _ap_root(tmp_path), tmp_path / "state.json"
    log = root / "0-a/.datacore/events/alice.jsonl"
    log.write_text("".join(_ev(i) for i in range(5)))
    assert _ap_run(root, st, monkeypatch) == (0, "ok")
    log.write_text("garbage\n{torn\n")
    assert _ap_run(root, st, monkeypatch) == (1, "missing")


def test_ap_deletion_in_one_space_is_missing_while_another_space_keeps_the_log(tmp_path, monkeypatch):
    """The 2026-07-21 sweep shape: files wiped from one space, surviving elsewhere."""
    root, st = _ap_root(tmp_path), tmp_path / "state.json"
    for sp in ("0-a", "1-b"):
        (root / sp / ".datacore/events/alice.jsonl").write_text("".join(_ev(i) for i in range(5)))
    assert _ap_run(root, st, monkeypatch) == (0, "ok")
    (root / "1-b/.datacore/events/alice.jsonl").unlink()
    assert _ap_run(root, st, monkeypatch) == (1, "missing")
    assert _ap_run(root, st, monkeypatch) == (1, "missing"), "must not heal on re-run"


def test_ap_stalled_does_not_self_heal_on_the_next_run(tmp_path, monkeypatch):
    root, st = _ap_root(tmp_path), tmp_path / "state.json"
    log = root / "0-a/.datacore/events/alice.jsonl"
    log.write_text("".join(_ev(i) for i in range(10)))
    _ap_run(root, st, monkeypatch)
    log.write_text("".join(_ev(i) for i in range(4)))          # truncated 9 -> 3
    assert _ap_run(root, st, monkeypatch) == (1, "stalled")
    assert _ap_run(root, st, monkeypatch) == (1, "stalled"), "the baseline must survive a STALLED run"
    log.write_text("".join(_ev(i) for i in range(12)))         # recovered past the baseline
    assert _ap_run(root, st, monkeypatch) == (0, "ok")


def test_ap_classify_is_pure_and_matches_the_model():
    c = AP.classify
    assert c({"0-a": 5}, None) == ("ok", [])
    assert c({}, None) == ("no-log-yet", [])
    assert c({"0-a": None}, None) == ("no-log-yet", [])
    assert c({"0-a": None}, {"0-a": 4}) == ("missing", ["0-a"])
    assert c({"0-a": 5}, {"0-a": 4, "1-b": 4}) == ("missing", ["1-b"])
    assert c({"0-a": 3}, {"0-a": 4}) == ("stalled", [])
    assert c({"0-a": 4, "1-b": 9}, {"0-a": 4}) == ("ok", [])


# ---------------------------------------------------------------- seq_gap

def test_sg_head_seq_is_the_highest_seq_not_the_last_line():
    assert SG.head_seq("".join(_ev(i) for i in (0, 1, 2, 9, 3))) == 9
    assert SG.head_seq("".join(_ev(i) for i in range(4)) + '{"seq": 7') == 3   # torn tail still skipped
    assert SG.head_seq("") is None and SG.head_seq("junk\n") is None


def _git(cwd, *a):
    p = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def _sg_space(tmp_path):
    bare = tmp_path / "origin.git"; _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    sp = tmp_path / "0-a"; _git(tmp_path, "clone", "-q", str(bare), str(sp))
    _git(sp, "config", "user.email", "t@t"); _git(sp, "config", "user.name", "t")
    (sp / ".datacore/events").mkdir(parents=True)
    log = sp / ".datacore/events/alice.jsonl"
    log.write_text("".join(_ev(i) for i in range(5)))
    _git(sp, "add", "-A"); _git(sp, "commit", "-qm", "x")
    _git(sp, "push", "--no-verify", "-q", "-u", "origin", "main")      # tmp bare repo only
    return sp, log


def test_sg_unreadable_local_log_is_an_error_not_published(tmp_path):
    sp, log = _sg_space(tmp_path)
    log.write_text("garbage\n")
    row = SG.scan_space(sp, sleep_log="")[0]
    assert row["error"], row
    assert not (row["gap"] == 0 and not row["error"])


def test_sg_a_lower_last_line_does_not_hide_unpublished_events(tmp_path):
    sp, log = _sg_space(tmp_path)
    log.write_text("".join(_ev(i) for i in (0, 1, 2, 3, 4, 5, 6, 3)))   # max 6, remote 4
    row = SG.scan_space(sp, sleep_log="", now_ms=1e18)[0]
    assert row["local_seq"] == 6 and row["gap"] == 2, row


def test_sg_empty_local_log_has_nothing_unpublished(tmp_path):
    """Not an error: a log with zero events has written nothing. Truncation is
    actor_presence's alarm (MISSING/STALLED), deliberately not this one's."""
    sp, log = _sg_space(tmp_path)
    log.write_text("")
    row = SG.scan_space(sp, sleep_log="")[0]
    assert row["gap"] == 0 and not row["error"], row


# ---------------------------------------------------------------- id_churn

def test_ic_set_baseline_catches_churn_that_keeps_the_count(capsys):
    acked = [f"id-{i}" for i in range(10)]
    now = acked[2:] + ["new-1", "new-2"]                  # 2 repaired, 2 newly churned
    f = [{"space": "0-personal", "duplicates": 0, "examples": [],
          "orphaned_ledger_ids": len(now), "orphaned_ids": now}]
    out = IC.apply_baseline(f, {"0-personal": acked, "_acknowledged": "2026-09-23"})
    assert len(out) == 1 and out[0]["orphaned_ledger_ids"] == 2, out
    assert out[0]["orphaned_ids"] == ["new-1", "new-2"]


def test_ic_set_baseline_exactly_acknowledged_is_not_a_finding(capsys):
    acked = ["a", "b", "c"]
    f = [{"space": "s", "duplicates": 0, "examples": [], "orphaned_ledger_ids": 2, "orphaned_ids": ["a", "c"]}]
    assert IC.apply_baseline(f, {"s": acked}) == []


def test_ic_legacy_count_baseline_still_reads(capsys):
    f = [{"space": "s", "duplicates": 0, "examples": [], "orphaned_ledger_ids": 12, "orphaned_ids": []}]
    assert IC.apply_baseline(f, {"s": 10})[0]["orphaned_ledger_ids"] == 2


# ---------------------------------------------------------------- reliability_scoreboard R5 / streak

def _r5(monkeypatch, hits, hours):
    day = "2026-09-05"
    # one unrelated line keeps the journal "readable" when there are 0 hits
    journal = "\n".join(["x daemon started"] + ["x 100.101.159.42 GET /health"] * hits)
    monkeypatch.setattr(RS, "_journal", lambda args: journal)
    return RS.r5_reachable(pathlib.Path("/nonexistent"), day,
                           now=dt.datetime.fromisoformat(day).timestamp() + hours * 3600)


def test_r5_three_of_four_is_not_99_5_percent(monkeypatch):
    assert not _r5(monkeypatch, 3, 1.0)[0]


def test_r5_95_of_96_is_not_99_5_percent(monkeypatch):
    assert not _r5(monkeypatch, 95, 24.0)[0]
    assert _r5(monkeypatch, 96, 24.0)[0]


def test_r5_is_not_vacuous_below_four_probes(monkeypatch):
    assert not _r5(monkeypatch, 0, 0.9)[0], "3 probes due, 0 seen"
    assert _r5(monkeypatch, 3, 0.9)[0]
    assert not _r5(monkeypatch, 0, 0.1)[0], "nothing due yet is not a pass"


def _all_pass(monkeypatch):
    for name in ("r1_delivered", "r2_loud", "r3_unattended", "r4_data_safe", "r5_reachable", "r6_rebuildable"):
        monkeypatch.setattr(RS, name, lambda *a, **k: (True, ""))


def test_streak_reads_yesterday_by_date_not_file_order(tmp_path, monkeypatch):
    _all_pass(monkeypatch)
    (tmp_path / "reliability-scoreboard.log").write_text(
        "2026-09-04 PASS streak=29 level=4 R1=ok\n2026-09-02 FAIL streak=0 level=3 R1=FAIL\n")
    assert RS.compute("2026-09-05", tmp_path, tmp_path)["streak"] == 30


def test_backfilling_a_fail_rechains_later_days(tmp_path):
    lines = ["2026-09-03 FAIL streak=0 level=3 R1=FAIL",
             "2026-09-04 PASS streak=29 level=4 R1=ok",
             "2026-09-05 PASS streak=30 level=5 R1=ok"]
    out = RS.write_day_lines(lines[1:] + ["2026-09-01 PASS streak=1 level=3 R1=ok"], lines[0])
    days = [(l.split()[0], l.split()[2]) for l in out]
    assert days == [("2026-09-01", "streak=1"), ("2026-09-03", "streak=0"),
                    ("2026-09-04", "streak=1"), ("2026-09-05", "streak=2")], out
    assert "level=3" in out[-1]


def test_a_normal_daily_write_changes_no_history(tmp_path):
    hist = ["2026-09-03 PASS streak=5 level=3 R1=ok", "2026-09-04 PASS streak=6 level=3 R1=ok"]
    out = RS.write_day_lines(hist, "2026-09-05 PASS streak=7 level=4 R1=ok")
    assert out == hist + ["2026-09-05 PASS streak=7 level=4 R1=ok"]


# ---------------------------------------------------------------- today_registry

def test_tr_dependency_on_a_later_stage_is_a_problem():
    R = TR.Registration
    regs = [R("m1", "a", None, agent="x", stage="gather", depends_on=["b"]),
            R("m2", "b", None, agent="x", stage="compose")]
    probs = TR.validate(regs, ["a", "b"])
    assert any("later stage" in p for p in probs), probs


def test_tr_dependency_on_an_earlier_stage_is_fine():
    R = TR.Registration
    regs = [R("m1", "a", None, agent="x", stage="narrate", depends_on=["b"]),
            R("m2", "b", None, agent="x", stage="gather")]
    assert TR.validate(regs, ["a", "b"]) == []


def test_tr_unknown_stage_is_reported_not_coerced_silently():
    r = TR._parse("m3", {"section": "c", "agent": "x", "stage": "narate"}, pathlib.Path("/nonexistent"))
    assert r.stage == "gather"                      # the plan still schedules it
    probs = TR.validate([r], ["c"])
    assert any("narate" in p for p in probs), probs
