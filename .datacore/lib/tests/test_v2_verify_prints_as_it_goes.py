"""The version check prints each group of checks as it completes, once.

On the chief of staff's machine a run killed half way (out of memory, the
scheduler's limit, a hung ledger verify) left a log with only the shell's
header: every row was still in a buffer. Each group is now printed when it
finishes, so a killed run leaves what it found. Each row appears once, and the
summary the job's contract reads comes last.
"""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import v2_verify as V  # noqa: E402


def test_each_row_is_printed_once_and_the_summary_comes_last(capsys):
    V._printed.update(n=0, dip=None)
    rep = V.Report()
    rep.add("0034", "ledger verify", True, "3/3 verify")
    V._print_report(rep)
    first = capsys.readouterr().out
    assert "ledger verify" in first, "the first group was not printed when it finished"
    rep.add("0034", "jobs declared", False, "2 missing")
    rep.add("0040", "config resolves", True, "")
    V._print_report(rep)
    V._print_report(rep, final=True)
    rest = capsys.readouterr().out
    out = first + rest
    rows = [l for l in out.splitlines() if l.startswith("    ")]
    for name in ("ledger verify", "jobs declared", "config resolves"):
        assert sum(name in r for r in rows) == 1, (name, rows)
    assert out.count("DIP-0034") == 1 and out.count("DIP-0040") == 1, out
    last = [l for l in out.splitlines() if l.strip()][-1]
    assert "v2 REGRESSED" in last and "jobs declared" in last, last
    assert "3 check(s): 2 ok, 1 FAIL" in out, out


def test_json_output_is_one_json_document(capsys, monkeypatch):
    """`--json` is read by machines: no rows before the document (review, 2026-11-22)."""
    import json
    V._printed.update(n=0, dip=None)
    for name in [n for n in dir(V) if n.startswith("check_")]:
        monkeypatch.setattr(V, name, lambda rep, *a, _n=name: rep.add("0000", _n, True, "{stubbed}"))
    monkeypatch.setattr(sys, "argv", ["v2_verify.py", "--json"])
    V.main()
    doc = json.loads(capsys.readouterr().out)
    assert doc["checks"] and all(c["ok"] is True for c in doc["checks"])


def test_the_wrapper_writes_the_log_in_place():
    """Renaming a finished log into place left the PREVIOUS run's log, a pass and
    still fresh, whenever the wrapper itself was killed (review, 2026-11-22)."""
    sh = (LIB / "v2_verify_run.sh").read_text()
    assert '} > "$OUT" 2>&1' in sh and "mktemp" not in sh and "mv -f" not in sh
