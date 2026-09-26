"""AUD-6: Audits stay within a nightly cost limit per agent, and a skipped or failed audit shows in The Firm, never silently.

Kind: deterministic + production. Not built yet (spec
.datacore/specs/cross-model-audit.md: "never spend beyond a per-agent nightly
cap"; DONE_WHEN "A missed or failed audit night shows in The Firm group the
next morning"); interface in tests/_audit_contract.py.

Deterministic: `cross_model_audit.NIGHTLY_CAP_USD` holds a positive cap for
each of the four agents and `may_start` refuses at or past it;
`night_alerts(night_dir)` -- what goes to The Firm group the next morning --
names every agent whose file is missing or invalid, and says nothing on a
clean night.

Production (@production): the missed-night check is scheduled on the box
(read-only crontab over ssh).

Seeded failure: an agent that has already spent its cap; a night where Data's
file is missing and Tris's is not valid YAML; a clean night of four valid files.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_contract import AGENTS, audit_module, finding, findings_file, head_sha  # noqa: E402


def test_each_agent_has_a_nightly_cap_and_it_holds():
    m = audit_module()
    assert set(m.NIGHTLY_CAP_USD) == set(AGENTS)
    for agent, cap in m.NIGHTLY_CAP_USD.items():
        assert cap > 0, f"{agent} has no cap"
        assert m.may_start(agent, 0.0)
        assert not m.may_start(agent, cap), f"{agent} may start at its cap"
        assert not m.may_start(agent, cap + 0.01)


def _night(tmp_path, *, break_some):
    d = tmp_path / "2026-09-27"
    sha = head_sha()
    for agent in AGENTS:
        if break_some and agent == "data":
            continue
        findings_file(d / f"{agent}.yaml", agent=agent, capability=f"cap-{agent}", commit=sha,
                      findings=[finding()])
    if break_some:
        (d / "tris.yaml").write_text("agent: tris\nfindings: [unclosed\n")
    return d


def test_a_missed_or_failed_audit_is_told_to_the_firm(tmp_path):
    m = audit_module()
    alerts = m.night_alerts(_night(tmp_path, break_some=True))
    text = "\n".join(alerts)
    assert "data" in text.lower(), f"a missing audit was silent: {alerts}"
    assert "tris" in text.lower(), f"an invalid findings file was silent: {alerts}"
    assert "miles" not in text.lower() and "winston" not in text.lower(), alerts


def test_a_clean_night_says_nothing(tmp_path):
    m = audit_module()
    assert m.night_alerts(_night(tmp_path, break_some=False)) == []


@pytest.mark.production
def test_the_missed_night_check_is_scheduled_on_the_box():
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "winston",
                        "crontab -l"], capture_output=True, text=True, timeout=45)
    assert r.returncode == 0, r.stderr[-300:]
    lines = [ln for ln in r.stdout.splitlines() if "cross_model_audit" in ln and not ln.startswith("#")]
    assert lines, "no cross_model_audit entry in the box crontab: a missed night would be silent"
