"""AGT-3: Each agent runs on the model access I configured for it: a
subscription, an API key, OpenRouter or a local model. Switching is a setting,
not code, and no agent uses access I retired.

Owner decision 2026-09-26: switching is a setting; no provider is a fault by
itself, only retired or unconfigured access. The retired ANTHROPIC_API_KEY must
not appear in any env.

Kind: deterministic + production.
  * the executor an agent runs through is named in cadence-control.yaml
    (`executors:`) and resolves to a registered runtime -- a setting;
  * every executor hands its agent a child environment WITHOUT the retired
    metered keys, even when the calling process carries them (a fake `claude`
    on PATH records what it received; nothing is called);
  * production: on every agent host no process runs with ANTHROPIC_API_KEY in
    its environment (read over ssh as NAMES only, never values), and each
    agent's runtime names a configured provider/model (model names only).

Seeded failure: executors/base.py _execution_env copies os.environ wholesale
(AC-17); with the fleet .env's ANTHROPIC_API_KEY in the parent, `claude -p`
silently bills the retired key instead of the subscription.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
RETIRED = ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN")


def test_each_agents_runtime_is_a_setting_that_resolves():
    from executors.base import registered_executors
    ctl = yaml.safe_load((ROOT / "8-firm" / ".datacore" / "cadence-control.yaml").read_text()) or {}
    ex = ctl.get("executors") or {}
    assert set(ex) >= {"miles", "tris", "data", "winston"}, f"an agent has no configured runtime: {ex}"
    unknown = {a: n for a, n in ex.items() if n not in registered_executors()}
    assert not unknown, f"configured runtimes that do not exist: {unknown}"


FAKE_CLAUDE = """#!/bin/sh
cat > /dev/null
for v in ANTHROPIC_API_KEY ANTHROPIC_TOKEN CLAUDE_CODE_OAUTH_TOKEN; do
  eval "x=\\${$v+set}"; echo "$v=${x:-unset}" >> "$ENV_SEEN"
done
echo '{"result": "ok", "is_error": false, "subtype": "success", "total_cost_usd": 0}'
"""


def test_the_claude_runtime_never_hands_the_agent_a_retired_key(tmp_path, monkeypatch):
    b = tmp_path / "bin"; b.mkdir()
    (b / "claude").write_text(FAKE_CLAUDE); (b / "claude").chmod(0o755)
    seen = tmp_path / "seen.txt"
    monkeypatch.setenv("PATH", f"{b}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ENV_SEEN", str(seen))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-eval-RETIRED")
    monkeypatch.setenv("ANTHROPIC_TOKEN", "eval-RETIRED")
    monkeypatch.setenv("DATACORE_NO_SPEND", "1")
    from executors.claude_code import ClaudeCodeExecutor
    res = ClaudeCodeExecutor().run("say ok", timeout_s=30, cwd=tmp_path, actor="miles")
    assert res.error is None, res.error
    got = dict(l.split("=", 1) for l in seen.read_text().split())
    leaked = [k for k in RETIRED if got.get(k) == "set"]
    assert not leaked, f"the agent's claude process received the retired {leaked}"


@pytest.mark.parametrize("name", ["hermes", "openclaw", "openclaw-gateway", "openrouter"])
def test_every_runtime_builds_its_child_env_without_retired_keys(name, monkeypatch):
    from executors.base import get_executor
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-eval-RETIRED")
    ex = get_executor(name)
    ex._actor, ex._item, ex._space = "tris", None, None
    env = ex._execution_env()
    assert not [k for k in RETIRED if k in env], f"{name}: child env carries a retired key"


@pytest.mark.production
def test_no_agent_host_runs_anything_with_the_retired_key():
    probe = ('for d in /proc/[0-9]*; do [ -O "$d" ] || continue; '
             'tr "\\0" "\\n" < "$d/environ" 2>/dev/null | grep -q "^ANTHROPIC_API_KEY=" && '
             'echo "$(cat $d/comm 2>/dev/null)"; done 2>/dev/null | sort | uniq -c; true')
    found = []
    for host in ("nightshift", "winston", "hermes", "plur-claw"):
        r = subprocess.run([*SSH, host, probe], capture_output=True, text=True, timeout=45)
        assert r.returncode == 0, f"{host}: could not read ({r.stderr.strip()[-160:]})"
        found += [f"{host}: {l.strip()}" for l in r.stdout.splitlines() if l.strip()]
    assert not found, "processes running with the retired ANTHROPIC_API_KEY in their env: " + "; ".join(found)


@pytest.mark.production
def test_each_agent_runtime_names_a_configured_model():
    """Model names only. Tris (hermes) and Winston's box gateway: ~/.hermes/config.yaml; Data: openclaw.json."""
    hermes = ('python3 -c "import yaml,os,json;d=yaml.safe_load(open(os.path.expanduser(\'~/.hermes/config.yaml\')));'
              'm=d.get(\'model\') or {};print(json.dumps({k:m.get(k) for k in (\'provider\',\'default\')}))"')
    claw = ('python3 -c "import json,os;d=json.load(open(os.path.expanduser(\'~/.openclaw/openclaw.json\')));'
            'print(json.dumps({\'model\':((d.get(\'agents\') or {}).get(\'defaults\') or {}).get(\'model\')}))"')
    bad = []
    for host, cmd, key in (("hermes", hermes, "default"), ("winston", hermes, "default"), ("plur-claw", claw, "model")):
        r = subprocess.run([*SSH, host, cmd], capture_output=True, text=True, timeout=45)
        try:
            doc = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            bad.append(f"{host}: unreadable runtime config"); continue
        if not doc.get(key):
            bad.append(f"{host}: no model configured")
        if str(doc.get("provider") or "").lower() == "anthropic":
            bad.append(f"{host}: runs on the metered Anthropic API")
    assert not bad, "; ".join(bad)
