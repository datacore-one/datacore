"""AUD-3: A problem found by two or more different models becomes a candidate test I review; one found by a single model is kept as unconfirmed.

Kind: deterministic. Not built yet (spec .datacore/specs/cross-model-audit.md,
"Aggregation (a script, no model)"); interface in tests/_audit_contract.py.

Drives `cross_model_audit.aggregate` over one night's folder of four findings
files written to tmp_path, pinned to this repo's HEAD.

Seeded failure (the spec's drill): a planted defect reported by two families
at the same promise and location (one says line 10, the other line 12 of the
same file) must come out as ONE confirmed candidate, marked for owner review
and never as a fix; the same defect reported twice by ONE family must stay
unconfirmed; a finding only one family made is kept as unconfirmed, not
dropped; and aggregation must not call any model.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_contract import audit_module, finding, findings_file, head_sha  # noqa: E402

PLANTED = ".datacore/lib/promise_evals.py"


def _night(tmp_path):
    sha = head_sha()
    d = tmp_path / "2026-09-27"
    findings_file(d / "miles.yaml", agent="miles", capability="tasks", commit=sha, findings=[
        finding("TSK-2", f"{PLANTED}:10", "duplicate id regenerated on load"),
        finding("TSK-2", f"{PLANTED}:10", "duplicate id regenerated on load (again)"),
        finding("TSK-4", f"{PLANTED}:40", "closed PR leaves task open")])
    findings_file(d / "winston.yaml", agent="winston", capability="tasks", commit=sha, findings=[
        finding("TSK-2", f"{PLANTED}:12", "ids are not stable across a reload")])
    findings_file(d / "tris.yaml", agent="tris", capability="ledger", commit=sha, findings=[
        finding("LED-1", f"{PLANTED}:80", "only one family saw this")])
    findings_file(d / "data.yaml", agent="data", capability="sync", commit=sha, findings=[])
    return d


def _promises(rows):
    return sorted(r["promise"] for r in rows)


def test_two_families_agreeing_is_one_confirmed_candidate(tmp_path, monkeypatch):
    m = audit_module()
    import subprocess
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: (
        (_ for _ in ()).throw(AssertionError(f"aggregation called a model: {cmd}"))
        if any(x in " ".join(map(str, cmd)) for x in ("claude", "openrouter", "openai")) else real(cmd, *a, **k)))
    out = m.aggregate(_night(tmp_path))
    confirmed = out["confirmed"]
    assert _promises(confirmed) == ["TSK-2"], confirmed
    row = confirmed[0]
    assert sorted(row["families"]) == ["claude", "deepseek"]
    assert row.get("status") in ("candidate", "red-candidate"), "a confirmed finding is a candidate for review"
    assert "fix" not in str(row.get("status", "")).lower()


def test_one_family_alone_is_kept_unconfirmed(tmp_path):
    m = audit_module()
    out = m.aggregate(_night(tmp_path))
    assert _promises(out["unconfirmed"]) == ["LED-1", "TSK-4"], out["unconfirmed"]


def test_one_family_saying_it_twice_does_not_confirm(tmp_path):
    m = audit_module()
    sha = head_sha()
    d = tmp_path / "2026-09-28"
    findings_file(d / "miles.yaml", agent="miles", capability="tasks", commit=sha,
                  findings=[finding("TSK-2", f"{PLANTED}:10"), finding("TSK-2", f"{PLANTED}:11")])
    findings_file(d / "winston.yaml", agent="winston", capability="ledger", commit=sha, findings=[],
                  model="claude")   # a second agent on the SAME family
    findings_file(d / "tris.yaml", agent="tris", capability="sync", commit=sha,
                  findings=[finding("TSK-2", f"{PLANTED}:10")], model="claude")
    out = m.aggregate(d)
    assert out["confirmed"] == [], "one model family agreeing with itself confirmed a finding"
    assert _promises(out["unconfirmed"]) == ["TSK-2"]
