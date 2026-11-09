"""Reports arrive in a space's 0-inbox/reports/; /today includes them, then files them.

Owner decision 2026-10-18 (space-structure board): the trading space's reports
"should first go into 0-inbox and should be processed, included in daily
report, and then moved." `list` is what the briefing reads; `file` moves only
what was listed, so a report that arrived while the briefing was being written
waits for the next one.
"""
import json
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import inbox_reports as R  # noqa: E402


def _space(tmp_path, files):
    space = tmp_path / "6-meridian"
    for rel, text in files.items():
        (space / rel).parent.mkdir(parents=True, exist_ok=True)
        (space / rel).write_text(text)
    return space


def test_list_shows_reports_waiting_in_the_inbox(tmp_path):
    space = _space(tmp_path, {
        "0-inbox/reports/market-phase/market-phase-2026-10-18.md": "x",
        "0-inbox/reports/pipeline-report-2026-W42.md": "y",
        "0-inbox/reports/.DS_Store": "",
        "0-inbox/other-capture.md": "not a report",
    })
    assert R.pending(space) == ["market-phase/market-phase-2026-10-18.md", "pipeline-report-2026-W42.md"]


def test_file_moves_listed_reports_under_their_path_and_nothing_else(tmp_path):
    space = _space(tmp_path, {
        "0-inbox/reports/market-phase/market-phase-2026-10-18.md": "x",
        "0-inbox/reports/late-arrival.md": "arrived after the briefing was written",
    })
    moved = R.file_reports(space, "1-tracks/trading/reports", ["market-phase/market-phase-2026-10-18.md"])
    assert moved == ["1-tracks/trading/reports/market-phase/market-phase-2026-10-18.md"]
    assert (space / moved[0]).read_text() == "x"
    assert R.pending(space) == ["late-arrival.md"]


def test_file_never_overwrites_a_filed_report(tmp_path):
    space = _space(tmp_path, {
        "0-inbox/reports/a.md": "new",
        "1-tracks/trading/reports/a.md": "already filed",
    })
    try:
        R.file_reports(space, "1-tracks/trading/reports", ["a.md"])
    except FileExistsError:
        pass
    else:
        raise AssertionError("an existing filed report must not be replaced")
    assert (space / "0-inbox/reports/a.md").read_text() == "new"


def test_cli_list_then_file(tmp_path):
    space = _space(tmp_path, {"0-inbox/reports/a.md": "x"})
    run = lambda *a: subprocess.run([sys.executable, str(LIB / "inbox_reports.py"), *a],
                                    capture_output=True, text=True, check=True).stdout
    listed = json.loads(run("list", "--space", str(space), "--json"))
    assert listed == ["a.md"]
    run("file", "--space", str(space), "--to", "1-tracks/trading/reports", *listed)
    assert (space / "1-tracks/trading/reports/a.md").exists()
