"""AUD-1: Every night each of the four agents audits a different part of Datacore on its own model, so the whole system is covered within a week.

Kind: deterministic + production. Not built yet (spec
.datacore/specs/cross-model-audit.md); the interface is stated in
tests/_audit_contract.py.

Deterministic: `cross_model_audit.rotation` over the real, current promise
files (read fresh, never cached): for seven consecutive nights each agent gets
one capability, no two agents share one on a night, and the week covers every
capability. The four agents run four different model families.

Production (@production): the last seven nights' findings under
2-datacore/1-tracks/dev/audits/nightly/<date>/ -- four files a night, one per
agent, each on that agent's family, four different capabilities, and together
every capability.

Seeded failure: a rotation that hands two agents the same slice, or one that
never reaches some capability in a week; a night with a missing agent file.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_contract import AGENTS, NIGHTLY, ROOT, audit_module  # noqa: E402

PROMISES = ROOT / "2-datacore" / "1-tracks" / "dev" / "datacore-upgrade" / "promises"


def _keys():
    out = set()
    for f in PROMISES.glob("*.yaml"):
        for cap in (yaml.safe_load(f.read_text()) or {}).get("capabilities") or []:
            out.add(cap["key"])
    return out


def test_four_agents_on_four_model_families():
    m = audit_module()
    assert set(m.AGENTS) == set(AGENTS), m.AGENTS
    assert len(set(m.AGENTS.values())) == 4, f"two agents share a model family: {m.AGENTS}"


def test_the_rotation_set_is_the_current_promise_list():
    m = audit_module()
    assert set(m.capabilities()) == _keys()


def test_each_night_different_slices_and_the_week_covers_everything():
    m = audit_module()
    caps = sorted(_keys())
    start = date(2026, 9, 28)
    seen = set()
    for d in range(7):
        night = m.rotation(caps, start + timedelta(days=d))
        assert set(night) == set(AGENTS), f"night {d}: agents {sorted(night)}"
        assert len(set(night.values())) == 4, f"night {d}: two agents on one slice {night}"
        seen |= set(night.values())
    assert seen == set(caps), f"not covered within a week: {sorted(set(caps) - seen)}"


@pytest.mark.production
def test_the_last_week_of_nights_ran_and_covered_everything():
    today = date.today()
    seen, problems = set(), []
    for d in range(1, 8):
        night = today - timedelta(days=d)
        folder = NIGHTLY / night.isoformat()
        files = {p.stem: yaml.safe_load(p.read_text()) or {} for p in folder.glob("*.yaml")} \
            if folder.is_dir() else {}
        if set(files) < set(AGENTS):
            problems.append(f"{night}: findings from {sorted(files) or 'nobody'}")
            continue
        caps = [files[a].get("capability") for a in AGENTS]
        if len(set(caps)) != 4:
            problems.append(f"{night}: slices {caps}")
        wrong = [a for a in AGENTS if files[a].get("model") != AGENTS[a]]
        if wrong:
            problems.append(f"{night}: not on their own model: {wrong}")
        seen |= set(caps)
    missed = sorted(_keys() - seen)
    assert not problems and not missed, f"{problems}; never audited this week: {missed}"
