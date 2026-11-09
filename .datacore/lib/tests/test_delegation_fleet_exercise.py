"""delegation_fleet_exercise: the ring is the install's own (INS-3)."""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import roster  # noqa: E402
import delegation_fleet_exercise as ex  # noqa: E402


def test_the_ring_and_forbidden_edge_come_from_the_registry(monkeypatch):
    monkeypatch.setattr(roster, "section", lambda key, path=None: {
        "ring": {"cos": ["ops"], "ops": ["cos"]}, "forbidden": ["ops", "boss"]}
        if key == "delegation_exercise" else {})
    assert ex._exercise() == ({"cos": ["ops"], "ops": ["cos"]}, ("ops", "boss"))


def test_a_fresh_install_seeds_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(roster, "section", lambda key, path=None: {})
    ring, edge = ex._exercise()
    assert ring == {} and edge is None
    monkeypatch.setattr(ex, "RING", ring)
    monkeypatch.setattr(ex, "FORBIDDEN", edge)
    assert ex.main(["seed", "--space", str(tmp_path), "--as", "anyone", "--day", "2026-09-27",
                    "--assert-refusals"]) == 0
    assert "delegates to nobody" in capsys.readouterr().out


def test_today_is_a_day_tag_a_schedule_can_pass(monkeypatch):
    # The weekly round runs from a schedule file, where a `$(date +%F)` needs
    # escaping that differs between cron and supercronic: "today" is the UTC date.
    import datetime as _dt
    import delegation_fleet_exercise as dfe
    seen = {}
    monkeypatch.setattr(dfe, "cmd_status", lambda a: seen.setdefault("day", a.day) and 0)
    dfe.main(["status", "--space", "/nonexistent", "--day", "today"])
    assert seen["day"] == _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _scratch(monkeypatch, tmp_path):
    import subprocess
    import ledger.policy as pol
    ring = {"cos": ["ops", "qa"], "ops": ["cos", "qa"], "qa": ["cos", "ops"]}
    monkeypatch.setattr(ex, "RING", ring)
    monkeypatch.setattr(ex, "FORBIDDEN", None)
    monkeypatch.setattr(pol, "guarded_append", lambda log, kind, payload: log.append(kind, payload))
    space = tmp_path / "space"
    (space / ".datacore" / "events").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(space)], check=True)
    return space


def test_the_coordinator_seeds_its_round_and_hands_every_other_agent_theirs(monkeypatch, tmp_path):
    # Owner 2026-10-28: Winston runs the weekly round. He cannot write another
    # agent's items (AGT-10), so each other agent gets a task, routed "do",
    # whose check is that agent's own round existing.
    space = _scratch(monkeypatch, tmp_path)
    day = "2026-11-02"
    assert ex.main(["start", "--space", str(space), "--as", "cos", "--day", day]) == 0
    state = ex._seeded(space, day)
    assert {ex._item_id("cos", t, day) for t in ("ops", "qa")} <= set(state)
    for to in ("ops", "qa"):
        it = state[f"{ex.MARK}-{day}-start-{to}"]
        assert it.payload.get("route") == "do" and it.payload.get("assignee") == to
        assert f"seeded --space . --day {day} --from {to}" in it.payload.get("check", "")
    assert ex.main(["seeded", "--space", str(space), "--day", day, "--from", "cos"]) == 0
    assert ex.main(["seeded", "--space", str(space), "--day", day, "--from", "ops"]) == 1, \
        "a round nobody seeded must not pass its start task's check"


def test_a_start_task_passes_once_that_agent_seeded_its_own_round(monkeypatch, tmp_path):
    space = _scratch(monkeypatch, tmp_path)
    day = "2026-11-02"
    assert ex.main(["seed", "--space", str(space), "--as", "ops", "--day", day]) == 0
    assert ex.main(["seeded", "--space", str(space), "--day", day, "--from", "ops"]) == 0
