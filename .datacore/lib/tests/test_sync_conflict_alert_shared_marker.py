"""One alert per sync conflict across the box's two syncs (2026-10-01).

On 2026-10-01 one conflict in 0-personal/org/inbox.org (task
sync-conflict-2df1b09a1f01) alerted twice: the box's cos_sync at 05:12 and the
fleet sync at 06:10, which met the same conflict in its own checkout and failed
the run ("met a new conflict"). Each kept its own record of what it had
alerted. Both now share one marker per conflict task, under
~/.datacore/state/sync-conflict-alerted/<task>, so whichever alerts second
sees "already reported".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger_transport as lt  # noqa: E402
from test_sync_conflict_org_and_alerts import (  # noqa: E402,F401 -- fleet is a fixture
    _conflict_items, _cos_run, _note_conflict, fleet)


def _home(w, monkeypatch):
    home = w["tmp"] / "home"
    monkeypatch.setenv("HOME", str(home))
    return home / ".datacore" / "state" / "sync-conflict-alerted"


def test_fleet_sync_alerts_first_then_cos_sync_stays_quiet(fleet, monkeypatch, capsys):
    import git_fleet_sync
    w = fleet
    markers = _home(w, monkeypatch)
    _note_conflict(w)
    monkeypatch.setattr(sys, "argv", ["git_fleet_sync.py", str(w["root"]), "--execute", "--pull"])

    assert git_fleet_sync.main() == 1                       # the fleet sync alerts
    (item,) = _conflict_items(w["b"])
    assert (markers / item.id).exists()

    line, alerts = _cos_run(w)

    assert alerts == 0, line
    assert "note.md" in line and "waiting for a person" in line, line


def test_cos_sync_alerts_first_then_fleet_sync_meeting_it_again_does_not_fail(
        fleet, monkeypatch, capsys):
    import git_fleet_sync
    w = fleet
    markers = _home(w, monkeypatch)
    _note_conflict(w)

    line, alerts = _cos_run(w)
    assert alerts == 1, line
    (item,) = _conflict_items(w["b"])
    assert (markers / item.id).exists()

    # The fleet sync met the same conflict in its own checkout (fresh there).
    monkeypatch.setattr(lt, "split_waiting", lambda repo, fresh=(): ([("note.md", item.id)], []))
    monkeypatch.setattr(sys, "argv", ["git_fleet_sync.py", str(w["root"]), "--execute", "--pull"])
    code = git_fleet_sync.main()
    out = capsys.readouterr().out

    assert code == 0, out
    assert "note.md" in out and "already reported" in out, out
    assert "met a new conflict" not in out, out


def test_a_conflict_nobody_reported_still_fails_the_fleet_sync(fleet, monkeypatch, capsys):
    import git_fleet_sync
    w = fleet
    markers = _home(w, monkeypatch)
    _note_conflict(w)
    monkeypatch.setattr(sys, "argv", ["git_fleet_sync.py", str(w["root"])])   # dry run

    git_fleet_sync.main()

    # A dry run alerts nobody, so it marks nothing.
    assert not markers.exists() or not any(markers.iterdir())
