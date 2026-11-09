"""One fleet promise board (plan 2026-10-31, phase H; processes page, "The
promise scoreboard", what changes [0]).

Every machine's latest board merges into one fleet board on the box, and the
fleet console reads that board (it read only Miles's). Written before the code.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import promise_fleet_board as F

TODAY = "2026-11-01"


def _board(host, date, states, why=None):
    return {"date": date, "host": host, "counts": {},
            "promises": {pid: {"state": st, "text": f"text {pid}", **({"why": why[pid]} if why and pid in why else {})}
                         for pid, st in states.items()}}


def test_a_red_anywhere_is_red_on_the_fleet_board_and_names_the_machine():
    fleet = F.merge({"box": _board("winston", TODAY, {"OPS-1": "green", "MEM-2": "red"}, {"MEM-2": "t.py: boom"}),
                     "nightshift": _board("dayshift", TODAY, {"OPS-1": "green", "MEM-2": "green"})}, today=TODAY)
    assert fleet["promises"]["MEM-2"]["state"] == "red"
    assert fleet["promises"]["MEM-2"]["machines"]["box"] == {"state": "red", "why": "t.py: boom"}
    assert fleet["promises"]["OPS-1"]["state"] == "green"
    assert fleet["counts"] == {"green": 1, "red": 1}


def test_could_not_run_everywhere_is_could_not_run_and_green_somewhere_beats_it():
    fleet = F.merge({"box": _board("w", TODAY, {"A-1": "could-not-run", "A-2": "could-not-run"}),
                     "mac": _board("m", TODAY, {"A-1": "green", "A-2": "could-not-run"})}, today=TODAY)
    assert fleet["promises"]["A-1"]["state"] == "green"
    assert fleet["promises"]["A-2"]["state"] == "could-not-run"


def test_a_stale_or_missing_board_is_listed_and_never_counted():
    fleet = F.merge({"box": _board("w", TODAY, {"A-1": "green"}),
                     "hermes": _board("h", "2026-10-20", {"A-1": "red"}),
                     "plur-claw": "unreachable: no route"}, today=TODAY)
    assert fleet["promises"]["A-1"]["state"] == "green", "a board from 12 days ago says nothing about today"
    assert fleet["machines"]["hermes"]["status"] == "stale" and fleet["machines"]["hermes"]["date"] == "2026-10-20"
    assert fleet["machines"]["plur-claw"]["status"] == "missing"
    assert "no route" in fleet["machines"]["plur-claw"]["why"]
    assert fleet["machines"]["box"]["status"] == "merged"


def test_no_current_board_at_all_is_not_a_fleet_board():
    fleet = F.merge({"box": "missing"}, today=TODAY)
    assert fleet["promises"] == {} and fleet["merged"] == 0


def test_the_cli_writes_the_fleet_board_atomically_and_reports_last(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_board("w", TODAY, {"A-1": "green"})))
    b.write_text(json.dumps(_board("d", TODAY, {"A-1": "red"})))
    out = tmp_path / "out"
    rc = F.main(["--board", f"box={a}", "--board", f"nightshift={b}", "--out-dir", str(out), "--today", TODAY])
    lines = capsys.readouterr().out.strip().splitlines()
    assert rc == 0, lines
    assert lines[-1].startswith("promise-fleet: 2 of 2 machine(s) merged"), lines
    latest = json.loads((out / "fleet-latest.json").read_text())
    assert latest["promises"]["A-1"]["state"] == "red"
    assert (out / f"fleet-{TODAY}.json").exists()
    assert not list(out.glob("*.tmp*")), "written atomically, no temp files left"


def test_the_cli_with_no_current_board_fails(tmp_path, capsys):
    rc = F.main(["--board", f"box={tmp_path / 'nope.json'}", "--out-dir", str(tmp_path / "o"), "--today", TODAY])
    assert rc != 0
    assert not (tmp_path / "o" / "fleet-latest.json").exists()


def test_the_default_machines_are_those_with_a_scoreboard_job():
    ms = F.board_machines()
    assert {"box", "nightshift", "hermes"} <= set(ms), ms


def test_the_console_reads_the_fleet_board_when_the_box_has_one():
    import fleet_dashboard as fd
    fleet = json.dumps(F.merge({"box": _board("w", TODAY, {"A-1": "red", "A-2": "green"})}, today=TODAY))
    miles = json.dumps(_board("d", TODAY, {"A-1": "green", "A-2": "green"}))
    raw = {"box": {"_promises": fleet, "_promises_prev": ""}, "nightshift": {"_promises": miles}}
    cur, prev, source = fd.promise_counts(raw)
    assert source == "fleet" and cur["red"] == 1 and cur["green"] == 1
    cur, prev, source = fd.promise_counts({"box": {}, "nightshift": {"_promises": miles}})
    assert source == "nightshift" and cur["green"] == 2, "until the fleet board is installed, Miles's board"


def test_the_box_board_is_read_in_its_pod_not_on_the_stale_host(monkeypatch):
    """The box's jobs moved into the pod around 2026-10-25; the host's board stopped there."""
    import fleet_reach
    seen = {}

    def place(machine, *a, host=False, **k):
        seen[machine] = host
        raise fleet_reach.Unreachable("stop here")
    monkeypatch.setattr(fleet_reach, "place", place)
    F.fetch("box")
    assert seen == {"box": False}
