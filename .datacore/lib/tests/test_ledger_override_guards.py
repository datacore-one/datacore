"""The guards refuse going past the ledger (owner decision 2026-09-28).

Two walls, one rule. The unattended tool policy (tool_effects.yaml, effect
`ledger.forge`, a never-effect of every agent principal) and the
config-protection hook (every Claude Code session, attended too) refuse a call
that sets the removed override flag, or deletes, moves or rewrites anything
under `.datacore/state/seq-hwm/`. Reading a mark stays open: it is how the
situation is diagnosed.

The 2026-09-27 box commands are the fixtures: the inbox job's `echo 2631 >`
onto the witness, its `rm`, and the override on the adapter call.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(LIB / "hooks"))

import tool_policy  # noqa: E402
from config_protection import switch_off  # noqa: E402

HOOK = LIB / "hooks" / "config_protection.py"
W = "/srv/data/0-personal/.datacore/state/seq-hwm/winston.seq"

BYPASSES = [
    ("Bash", {"command": f"echo 2631 > {W}"}),
    ("Bash", {"command": f"rm {W}"}),
    ("Bash", {"command": f"rm -f {W} {W[:-4]}.hash"}),
    ("Bash", {"command": "cd ~/Data/0-personal/.datacore/state/seq-hwm && rm winston.seq"}),
    ("Bash", {"command": "cd ~/Data/0-personal/.datacore/state && mkdir -p seq-hwm-retired && "
                         "mv seq-hwm/winston.seq seq-hwm-retired/winston.seq.x"}),
    ("Bash", {"command": f"printf 0 | tee {W}"}),
    ("Bash", {"command": f"sed -i s/2644/2631/ {W}"}),
    ("Bash", {"command": f"truncate -s0 {W}"}),
    ("Bash", {"command": "find ~/Data -path '*seq-hwm*' -delete"}),
    ("Bash", {"command": f"python3 -c \"import os; os.remove('{W}')\""}),
    ("Bash", {"command": "DATACORE_HWM_OVERRIDE=1 python3 .datacore/lib/org_workspace_adapter.py complete --id x"}),
    ("Bash", {"command": "export DATACORE_HWM_OVERRIDE=1 && python3 x.py"}),
    ("Bash", {"command": "env DATACORE_HWM_OVERRIDE=1 python3 x.py"}),
    ("terminal", {"command": f"rm {W}"}),
    ("terminal", {"command": "DATACORE_HWM_OVERRIDE=1 python3 org_workspace_adapter.py move"}),
    ("execute_code", {"code": "import os\nos.environ['DATACORE_HWM_OVERRIDE'] = '1'"}),
    ("execute_code", {"code": f"from pathlib import Path\nPath('{W}').unlink()"}),
    ("Write", {"file_path": W, "content": "2631"}),
    ("Edit", {"file_path": W, "old_string": "2644", "new_string": "2631"}),
    ("write_file", {"path": W, "content": "2631"}),
]

READS = [
    ("Bash", {"command": f"cat {W}"}),
    ("Bash", {"command": f"cat {W} 2>/dev/null"}),
    ("Bash", {"command": "ls -la ~/Data/0-personal/.datacore/state/seq-hwm/"}),
    ("Bash", {"command": "python3 .datacore/lib/ledger_cli.py stopped --space 0-personal"}),
    ("Bash", {"command": "grep -rn HWM_OVERRIDE .datacore/lib"}),
    ("Read", {"file_path": W}),
    ("Write", {"file_path": "/srv/data/.datacore/docs/recovery.md",
               "content": "the override flag was removed; never rm the seq-hwm witness"}),
]


def _ids(cases):
    return [f"{t}:{json.dumps(i)[:60]}" for t, i in cases]


@pytest.mark.parametrize("tool,tool_input", BYPASSES, ids=_ids(BYPASSES))
def test_tool_policy_classifies_a_bypass_as_ledger_forge(tool, tool_input):
    assert "ledger.forge" in tool_policy.classify(tool, tool_input)


@pytest.mark.parametrize("tool,tool_input", READS, ids=_ids(READS))
def test_tool_policy_leaves_reading_open(tool, tool_input):
    assert "ledger.forge" not in tool_policy.classify(tool, tool_input)


def test_every_agent_principal_is_refused_the_bypass():
    policy = LIB.parent / "config" / "approvals_policy.yaml"
    d = tool_policy.decide("assistant", "Bash", {"command": f"rm {W}"}, policy_path=policy)
    assert d.blocked and d.kind == "never"
    d = tool_policy.decide("assistant", "terminal",
                           {"command": "DATACORE_HWM_OVERRIDE=1 python3 a.py"}, policy_path=policy)
    assert d.blocked and d.kind == "never"


@pytest.mark.parametrize("tool,tool_input", [c for c in BYPASSES if c[0] in ("Bash", "Write", "Edit")],
                         ids=_ids([c for c in BYPASSES if c[0] in ("Bash", "Write", "Edit")]))
def test_config_protection_refuses_the_bypass_in_every_session(tool, tool_input):
    assert switch_off(tool, tool_input)
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(
        {"tool_name": tool, "tool_input": tool_input}), capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "ledger" in r.stderr.lower()


@pytest.mark.parametrize("tool,tool_input", READS, ids=_ids(READS))
def test_config_protection_leaves_reading_open(tool, tool_input):
    assert switch_off(tool, tool_input) is None
