"""The Datacore tool gate for OpenClaw (Data's runtime on plur-claw, 2026-09-30).

OpenClaw runs its tools itself (its own `exec`, `apply_patch`, `read`, ... and,
on the Codex harness, Codex-native tools relayed as `before_tool_call`), so no
Claude hook or Hermes plugin ever saw a call Data made. The gate is two parts:

  openclaw_plugin/index.js  an OpenClaw plugin whose `before_tool_call` handler
                            pipes the event to the gate and blocks on anything
                            but a clear "allow"
  openclaw_plugin/gate.py   translates the OpenClaw tool call into the names
                            tool_effects.yaml knows and asks
                            tool_policy.evaluate_hook

Both must fail closed: a gate that cannot load the policy refuses the call.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
PLUGIN = LIB / "openclaw_plugin"
GATE = PLUGIN / "gate.py"

POLICY = """version: 1
approver: human
cosign_effects: [email.send, payment, prod.deploy, data.delete, bulk.change, push.shared,
                 public.post, message.send, page.publish]
principals:
  data:
    never_effects: [payment, prod.deploy, trading, credential.rotate, gov.submit, code.merge,
                    secret.search, guard.disable, ledger.forge, calendar.respond, data.egress,
                    history.rewrite, eval.edit, worktree.discard]
"""


def _load_gate():
    spec = importlib.util.spec_from_file_location("openclaw_gate_under_test", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def gate(tmp_path):
    mod = _load_gate()
    policy = tmp_path / "approvals_policy.yaml"
    policy.write_text(POLICY)
    env = {"DATACORE_POLICY_PRINCIPAL": "data", "DATACORE_POLICY_SPACE": str(tmp_path / "no-ledger")}

    def decide(event):
        return mod.decide_event(event, env=env, policy_path=policy, record=False)
    return decide


def exec_call(command):
    return {"toolName": "exec", "params": {"command": command}}


# ── the three calls the owner named, as principal data ──────────────────────

def test_git_stash_is_refused(gate):
    out = gate(exec_call("git stash"))
    assert out["block"] is True
    assert "worktree.discard" in out["blockReason"]


def test_git_status_is_allowed(gate):
    assert gate(exec_call("git status")) == {"block": False}


def test_an_email_send_is_refused(gate):
    out = gate(exec_call("python3 -c 'import smtplib; smtplib.SMTP(\"x\").sendmail(1,2,3)'"))
    assert out["block"] is True and "email.send" in out["blockReason"]
    out = gate({"toolName": "mcp__gmail__send_email", "params": {"to": "a@b.c", "body": "hi"}})
    assert out["block"] is True and "email.send" in out["blockReason"]


# ── the shapes OpenClaw actually hands over ─────────────────────────────────

def test_codex_native_exec_with_an_argv_cmd_is_read(gate):
    # Codex's exec_command reaches the hook as `exec`; the relay adds `command`
    # but a runtime that did not would leave only `cmd`, possibly as argv.
    out = gate({"toolName": "exec", "params": {"cmd": ["bash", "-lc", "git reset --hard"]}})
    assert out["block"] is True and "worktree.discard" in out["blockReason"]
    out = gate({"toolName": "exec_command", "params": {"cmd": "git stash list"}})
    assert out["block"] is True


def test_a_shell_call_whose_command_cannot_be_read_is_refused(gate):
    out = gate({"toolName": "exec", "params": {"weird": 1}})
    assert out["block"] is True


def test_a_patch_to_a_guard_is_refused_by_its_path(gate):
    patch = "*** Begin Patch\n*** Update File: .datacore/lib/hooks/tool_policy_guard.py\n@@\n-x\n+y\n*** End Patch\n"
    out = gate({"toolName": "apply_patch", "params": {"input": patch}})
    assert out["block"] is True and "guard.disable" in out["blockReason"]
    out = gate({"toolName": "apply_patch", "params": {"input": "*** Begin Patch\n*** End Patch\n"},
                "derivedPaths": ["/srv/app/.datacore/config/tool_effects.yaml"]})
    assert out["block"] is True


def test_an_ordinary_patch_and_write_are_allowed(gate):
    patch = "*** Begin Patch\n*** Add File: notes/today.md\n+hello\n*** End Patch\n"
    assert gate({"toolName": "apply_patch", "params": {"input": patch}}) == {"block": False}
    assert gate({"toolName": "write", "params": {"path": "notes/a.md", "content": "x"}}) == {"block": False}


def test_reading_an_env_file_is_refused(gate):
    out = gate({"toolName": "read", "params": {"path": "/srv/app/.hermes/.env"}})
    assert out["block"] is True and "secret.search" in out["blockReason"]


def test_an_mcp_tool_without_the_prefix_is_still_an_mcp_tool(gate):
    out = gate({"toolName": "gmail__send_email", "params": {"to": "a@b.c"}})
    assert out["block"] is True


# ── OpenClaw's own tools (owner decision 2026-09-30) ────────────────────────
# `message` sends into a chat channel, `conversations_send` delivers to an
# external conversation, `gateway update.run` updates the runtime itself.
# Until now none was in the vocabulary, so all passed unchecked.

@pytest.mark.parametrize("params", [
    {"action": "send", "channel": "telegram", "target": "@plur_ai", "message": "hi"},
    {"action": "reply", "message": "hi"},
    {"action": "thread-reply", "message": "hi"},
    {"action": "broadcast", "message": "hi"},
    {"action": "sendAttachment", "path": "/tmp/x.png"},
    {"action": "upload-file", "path": "/tmp/x.png"},
    {"action": "poll", "question": "?"},
    {"action": "delete", "messageId": "1"},
    {"message": "no action given means a send"},
])
def test_the_message_tool_sending_is_a_message_send(gate, params):
    out = gate({"toolName": "message", "params": params})
    assert out["block"] is True and "message.send" in out["blockReason"], params


@pytest.mark.parametrize("action", ["read", "search", "channel-list", "member-info", "thread-list",
                                    "reactions", "download-file"])
def test_the_message_tool_reading_stays_open(gate, action):
    assert gate({"toolName": "message", "params": {"action": action}}) == {"block": False}


def test_conversations_send_is_a_message_send(gate):
    out = gate({"toolName": "conversations_send", "params": {"conversationRef": "x", "text": "hi"}})
    assert out["block"] is True and "message.send" in out["blockReason"]


def test_the_gateway_tool_may_read_but_never_update_the_runtime(gate):
    out = gate({"toolName": "gateway", "params": {"action": "update.run"}})
    assert out["block"] is True and "prod.deploy" in out["blockReason"]
    assert gate({"toolName": "gateway", "params": {"action": "config.get"}}) == {"block": False}
    assert gate({"toolName": "gateway", "params": {"action": "config.schema.lookup",
                                                   "path": "agents"}}) == {"block": False}


def test_a_standing_grant_reaches_a_codex_argv_command(tmp_path):
    """Codex hands a shell call over as argv `cmd`; the gate reads it into
    `command`, and the argv copy must not make the same command look like a
    second, ungranted one (found live on plur-claw, 2026-09-30)."""
    mod = _load_gate()
    policy = tmp_path / "approvals_policy.yaml"
    policy.write_text(POLICY + r"""    standing_grants:
      - effect: public.post
        command: '(^|/)\.datacore/modules/comms/lib/x_poster\.py\b'
""")
    env = {"DATACORE_POLICY_PRINCIPAL": "data", "DATACORE_POLICY_SPACE": str(tmp_path / "no-ledger")}
    poster = "python3 /srv/agent/Data/.datacore/modules/comms/lib/x_poster.py --account plur hi"
    for params in ({"command": poster}, {"cmd": ["bash", "-lc", f"cd /srv/agent/Data && {poster}"]},
                   {"cmd": poster}):
        out = mod.decide_event({"toolName": "exec", "params": params}, env=env, policy_path=policy, record=False)
        assert out == {"block": False}, (params, out)
    # a different `cmd` beside `command` is still judged: it is not the same call
    out = mod.decide_event({"toolName": "exec", "params": {"command": poster, "cmd": "python3 engagement_post.py"}},
                           env=env, policy_path=policy, record=False)
    assert out["block"] is True


def test_openclaw_tool_names_do_not_leak_into_other_runtimes():
    """The action-qualified names exist only for OpenClaw's own tools: a Claude
    or Hermes tool called `message` is not a thing, and computer_use's
    `"action": "left_click"` is not a message."""
    sys.path.insert(0, str(LIB))
    import tool_policy as tp
    effects = tp.load_effects()
    assert "message.send" not in tp.classify("computer_use", {"action": "left_click"}, effects)
    assert "message.send" not in tp.classify("Bash", {"command": "echo '\"action\": \"send\"'"}, effects)


# ── fail closed ─────────────────────────────────────────────────────────────

def test_a_missing_policy_refuses(tmp_path):
    mod = _load_gate()
    out = mod.decide_event(exec_call("git status"), env={"DATACORE_POLICY_PRINCIPAL": "data"},
                           policy_path=tmp_path / "absent.yaml", record=False)
    assert out["block"] is True


def test_an_unknown_principal_refuses(tmp_path):
    mod = _load_gate()
    policy = tmp_path / "approvals_policy.yaml"
    policy.write_text(POLICY)
    out = mod.decide_event(exec_call("git status"), env={"DATACORE_POLICY_PRINCIPAL": "stranger"},
                           policy_path=policy, record=False)
    assert out["block"] is True


@pytest.mark.parametrize("stdin", ["", "not json", "[]", '{"params": {}}'])
def test_a_malformed_request_refuses(stdin):
    r = subprocess.run([sys.executable, str(GATE)], input=stdin, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert json.loads(r.stdout)["block"] is True


def test_a_gate_that_cannot_load_the_policy_library_refuses(tmp_path):
    lone = tmp_path / "openclaw_plugin" / "gate.py"
    lone.parent.mkdir()
    shutil.copy(GATE, lone)  # no tool_policy.py anywhere near it
    r = subprocess.run([sys.executable, "-S", str(lone)], input=json.dumps(exec_call("git status")),
                       capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ""})
    out = json.loads(r.stdout)
    assert out["block"] is True and "unavailable" in out["blockReason"]


# ── the plugin itself (node) ────────────────────────────────────────────────

NODE = shutil.which("node")
HARNESS = r"""
const [,, pluginPath, gate, eventJson] = process.argv;
const mod = await import(pluginPath);
const entry = mod.default;
let handler, opts;
const api = { pluginConfig: { gate, python: process.env.PYBIN, timeoutMs: 3000 },
              logger: { info(){}, warn(){}, error(){} },
              on(name, h, o) { if (name === "before_tool_call") { handler = h; opts = o; } } };
entry.register(api);
const out = await handler(JSON.parse(eventJson), { sessionKey: "agent:main:test" });
console.log(JSON.stringify({ out: out ?? null, id: entry.id, priority: opts?.priority ?? null }));
"""


def _run_plugin(tmp_path, gate_script: str, event=None):
    fake = tmp_path / "fake_gate.py"
    fake.write_text(gate_script)
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS)
    r = subprocess.run([NODE, str(harness), str(PLUGIN / "index.js"), str(fake),
                        json.dumps(event or exec_call("git status"))],
                       capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "PYBIN": sys.executable})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_plugin_allows_only_on_a_clear_allow(tmp_path):
    res = _run_plugin(tmp_path, 'import sys; sys.stdin.read(); print(\'{"block": false}\')')
    assert res["out"] is None and res["id"] == "datacore"


@needs_node
def test_plugin_passes_a_refusal_on(tmp_path):
    res = _run_plugin(tmp_path, 'import sys; sys.stdin.read(); print(\'{"block": true, "blockReason": "no"}\')')
    assert res["out"] == {"block": True, "blockReason": "no"}


@needs_node
@pytest.mark.parametrize("script", [
    "import sys; sys.exit(3)",                                  # the gate crashed
    "print('garbage')",                                         # unreadable answer
    "print('{}')",                                              # no decision
    "import time; time.sleep(30)",                              # hung
])
def test_plugin_fails_closed(tmp_path, script):
    res = _run_plugin(tmp_path, script)
    assert res["out"]["block"] is True


@needs_node
def test_plugin_fails_closed_when_the_gate_is_missing(tmp_path):
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS)
    r = subprocess.run([NODE, str(harness), str(PLUGIN / "index.js"), str(tmp_path / "absent.py"),
                        json.dumps(exec_call("git status"))],
                       capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "PYBIN": sys.executable})
    assert json.loads(r.stdout.strip().splitlines()[-1])["out"]["block"] is True


@needs_node
def test_plugin_runs_first(tmp_path):
    res = _run_plugin(tmp_path, 'import sys; sys.stdin.read(); print(\'{"block": false}\')')
    assert res["priority"] >= 1000
