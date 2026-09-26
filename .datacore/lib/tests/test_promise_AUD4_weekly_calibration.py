"""AUD-4: Once a week every model audits the same practice project with known planted bugs, and I can see how many each one catches.

Kind: deterministic + production. Not built yet (spec
.datacore/specs/cross-model-audit.md, "Weekly calibration"); interface in
tests/_audit_contract.py.

Deterministic: `cross_model_audit.calibrate(answer_key, findings_by_family)`
scores each family against the answer key -- recall and false positives --
from the key, never from the models' own claims.

Production (@production): the practice project and its answer key exist in the
dev module (evals/audit/), and a calibration covering all four families was
published within the last 8 days under 2-datacore/1-tracks/dev/audits/calibration/.

Seeded failure: a family that reports 4 of 5 planted bugs plus one invented
one; a family that reports nothing; a family that claims every bug with the
wrong locations. The promise holds when their scores are 0.8 recall / 1 false
positive, 0.0 / 0, and 0.0 / 5.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_contract import AGENTS, ROOT, audit_module  # noqa: E402

KEY = [{"id": f"B{i}", "evidence": f"ledgerlite/core.py:{10 * i}"} for i in range(1, 6)]


def test_each_family_is_scored_against_the_answer_key():
    m = audit_module()
    got = m.calibrate(KEY, {
        "claude": [{"evidence": f"ledgerlite/core.py:{10 * i}"} for i in (1, 2, 3, 4)]
                  + [{"evidence": "ledgerlite/core.py:999"}],
        "deepseek": [],
        "glm": [{"evidence": f"ledgerlite/other.py:{10 * i}"} for i in range(1, 6)],
    })
    assert got["claude"]["recall"] == pytest.approx(0.8) and got["claude"]["false_positives"] == 1
    assert got["deepseek"]["recall"] == 0 and got["deepseek"]["false_positives"] == 0
    assert got["glm"]["recall"] == 0 and got["glm"]["false_positives"] == 5


@pytest.mark.production
def test_a_weekly_calibration_is_published_for_every_family():
    dev = ROOT / ".datacore" / "modules" / "dev"
    keys = list((dev / "evals" / "audit").glob("**/answer*key*.y*ml")) if (dev / "evals" / "audit").is_dir() else []
    assert keys, "no practice project with an answer key under the dev module's evals/audit/"
    cal = ROOT / "2-datacore" / "1-tracks" / "dev" / "audits" / "calibration"
    recent = [p for p in cal.glob("*.yaml")
              if date.fromtimestamp(p.stat().st_mtime) >= date.today() - timedelta(days=8)] if cal.is_dir() else []
    assert recent, f"no calibration published in the last 8 days under {cal.relative_to(ROOT)}"
    latest = yaml.safe_load(max(recent, key=lambda p: p.stat().st_mtime).read_text()) or {}
    scores = latest.get("families") or {}
    missing = sorted(set(AGENTS.values()) - set(scores))
    assert not missing, f"the calibration does not score: {missing}"
    assert all("recall" in s and "false_positives" in s for s in scores.values())
