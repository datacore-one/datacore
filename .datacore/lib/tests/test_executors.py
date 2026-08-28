"""Tests for the executors package -- adapter conformance + live shadow accounting.

The conformance suite (top of file) is parametrized over every registered
executor and exercises `Executor.run()` semantics WITHOUT touching a real
network or binary: each test gets a fresh instance via `get_executor(name)`
and monkeypatches that instance's `_invoke` with a fake transport (a plain
function, not a subclass -- the injection point the base class's design
calls for). This proves the never-raise wrapping, schema-contract parsing,
spend emission, and timeout mapping are base-class behavior shared by every
adapter, not something each adapter has to reimplement.

Below that are adapter-specific unit tests that DO exercise each adapter's
real `_invoke` (subprocess.run / shutil.which mocked, or the anthropic
import guarded) -- these catch bugs the fake-transport conformance suite
structurally cannot see (JSON envelope parsing, binary-not-found handling,
guarded SDK import).

Live smoke tests (bottom of file) are gated behind `RUN_LIVE=1` and only
cover claude-code -- hermes/api credentials are never required to run this
suite.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys

import pytest

from executors import ExecResult, get_executor, registered_executors
from ledger.log import EventLog, read_events

REGISTERED_NAMES = sorted(registered_executors())


@pytest.fixture
def hermetic_env(tmp_path, monkeypatch):
    """Point DATACORE_ROOT at tmp_path, fix the actor, and force ledger
    signing off regardless of the ambient environment -- mirrors the `_env`
    helper in tests/test_ledger_cli.py so spend events land in an isolated
    space and never touch real key material."""
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path))
    monkeypatch.setenv("DATACORE_ACTOR", "test-actor")
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
    monkeypatch.delenv("DATACORE_NO_SPEND", raising=False)
    monkeypatch.delenv("DATACORE_EXECUTOR", raising=False)
    return tmp_path


# --- registry -------------------------------------------------------------


def test_registered_executors_includes_every_adapter():
    """openclaw joined on 2026-08-11. Its absence is why a claude-shaped
    dispatcher reported Data as having "no agent runtime" when plur-claw runs a
    perfectly good one — the registry, not the machine, was missing it.

    openrouter joined on 2026-08-27. It was the one provider in live use
    (comms module draft_evaluator) that the registry could not account for,
    making its spend invisible to shadow accounting."""
    assert set(registered_executors()) == {"claude-code", "hermes", "api", "openclaw", "openrouter"}


def test_get_executor_unknown_name_raises_value_error_listing_known():
    with pytest.raises(ValueError) as exc_info:
        get_executor("does-not-exist")
    message = str(exc_info.value)
    for name in REGISTERED_NAMES:
        assert name in message


def test_get_executor_defaults_to_claude_code(monkeypatch):
    monkeypatch.delenv("DATACORE_EXECUTOR", raising=False)
    assert get_executor().name == "claude-code"


def test_get_executor_uses_env_var_when_no_explicit_name(monkeypatch):
    monkeypatch.setenv("DATACORE_EXECUTOR", "hermes")
    assert get_executor().name == "hermes"


def test_get_executor_explicit_name_overrides_env(monkeypatch):
    monkeypatch.setenv("DATACORE_EXECUTOR", "hermes")
    assert get_executor("api").name == "api"


def test_exec_result_parse_ok_defaults_to_none():
    result = ExecResult(text="hi", parsed=None, cost_cents=5, error=None)
    assert result.parse_ok is None


# --- conformance suite (parametrized over every registered adapter) ------


@pytest.mark.parametrize("name", REGISTERED_NAMES)
class TestExecutorConformance:
    def test_never_raises_when_invoke_raises(self, name, hermetic_env, monkeypatch):
        executor = get_executor(name)
        monkeypatch.setattr(
            executor, "_invoke", lambda prompt, timeout_s: (_ for _ in ()).throw(RuntimeError("transport exploded"))
        )

        result = executor.run("hello")

        assert isinstance(result, ExecResult)
        assert result.text == ""
        assert result.cost_cents == 0
        assert result.parsed is None
        assert result.parse_ok is None
        assert result.error is not None
        assert "transport exploded" in result.error

    def test_timeout_expired_maps_to_error_not_raise(self, name, hermetic_env, monkeypatch):
        executor = get_executor(name)

        def timeout(prompt, timeout_s):
            raise subprocess.TimeoutExpired(cmd="fake-cmd", timeout=timeout_s)

        monkeypatch.setattr(executor, "_invoke", timeout)

        result = executor.run("hello", timeout_s=5)

        assert result.error is not None
        assert "5" in result.error
        assert result.text == ""
        assert result.cost_cents == 0

    def test_successful_run_emits_spend_event_in_tmp_space(self, name, hermetic_env, monkeypatch):
        executor = get_executor(name)
        monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("ok", 42))

        result = executor.run("hello")

        assert result.error is None
        assert result.text == "ok"
        assert result.cost_cents == 42

        events = read_events(hermetic_env)
        spend_events = [e for e in events if e.type == "spend.record"]
        assert len(spend_events) == 1
        # Required keys, not an exact dict: the payload gained `executor`,
        # and gains `item`/`model` when the caller supplies them.
        p = spend_events[0].payload
        assert p["cents"] == 42 and p["ref"] == f"executor:{name}"
        assert p["executor"] == name
        assert spend_events[0].actor == "test-actor"
        assert spend_events[0].sig == ""  # unsigned by default

    def test_error_run_emits_no_spend_event(self, name, hermetic_env, monkeypatch):
        executor = get_executor(name)
        monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: (_ for _ in ()).throw(RuntimeError("no")))

        executor.run("hello")

        assert read_events(hermetic_env) == []

    def test_no_spend_env_gate_suppresses_emission(self, name, hermetic_env, monkeypatch):
        monkeypatch.setenv("DATACORE_NO_SPEND", "1")
        executor = get_executor(name)
        monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("ok", 42))

        result = executor.run("hello")

        assert result.error is None
        assert read_events(hermetic_env) == []

    def test_schema_contract_appended_to_prompt_and_parses_valid_json(self, name, hermetic_env, monkeypatch):
        executor = get_executor(name)
        seen_prompts: list[str] = []

        def fake_invoke(prompt, timeout_s):
            seen_prompts.append(prompt)
            return ('{"ok": true}', 10)

        monkeypatch.setattr(executor, "_invoke", fake_invoke)
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

        result = executor.run("do the thing", schema=schema)

        assert len(seen_prompts) == 1
        assert seen_prompts[0].startswith("do the thing")
        assert json.dumps(schema, sort_keys=True) in seen_prompts[0]
        assert result.parsed == {"ok": True}
        assert result.parse_ok is True
        assert result.error is None

        # Schema-success-still-spends: a schema-carrying run is a normal
        # successful run and must land exactly one spend.record, same as
        # any other successful run.
        events = read_events(hermetic_env)
        spend_events = [e for e in events if e.type == "spend.record"]
        assert len(spend_events) == 1
        assert spend_events[0].payload["cents"] == 10

    def test_schema_parse_failure_sets_parse_ok_false_and_leaves_error_none(self, name, hermetic_env, monkeypatch):
        executor = get_executor(name)
        monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("not json at all", 10))

        result = executor.run("do the thing", schema={"type": "object"})

        assert result.parsed is None
        assert result.parse_ok is False
        assert result.error is None  # parse failure must never overload error
        assert result.text == "not json at all"

        # Schema-failure-still-spends: a schema PARSE failure is a content
        # outcome, not an execution failure -- _invoke still succeeded and
        # consumed real cost, so exactly one spend.record must still land.
        events = read_events(hermetic_env)
        spend_events = [e for e in events if e.type == "spend.record"]
        assert len(spend_events) == 1
        assert spend_events[0].payload["cents"] == 10

    def test_no_schema_requested_means_parse_ok_is_none(self, name, hermetic_env, monkeypatch):
        executor = get_executor(name)
        monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("plain text", 10))

        result = executor.run("do the thing")

        assert result.parsed is None
        assert result.parse_ok is None

    def test_estimated_cost_marks_ref_with_est_suffix(self, name, hermetic_env, monkeypatch):
        executor = get_executor(name)

        def fake_invoke(prompt, timeout_s):
            executor._cost_estimated = True
            return ("ok", 7)

        monkeypatch.setattr(executor, "_invoke", fake_invoke)

        executor.run("hello")

        events = read_events(hermetic_env)
        assert events[0].payload["ref"] == f"executor:{name}:est"

    def test_negative_cost_from_invoke_is_floored_to_zero_with_clamped_marker(self, name, hermetic_env, monkeypatch):
        executor = get_executor(name)
        monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("ok", -50))

        result = executor.run("hello")

        assert result.cost_cents == 0
        assert result.error is None  # a clamp is not itself a run error

        events = read_events(hermetic_env)
        assert len(events) == 1
        assert events[0].payload["cents"] == 0
        assert events[0].payload["ref"] == f"executor:{name}:clamped"

    def test_non_int_ish_cost_from_invoke_is_floored_to_zero_with_clamped_marker(
        self, name, hermetic_env, monkeypatch
    ):
        executor = get_executor(name)
        monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("ok", "not-a-number"))

        result = executor.run("hello")

        assert result.cost_cents == 0
        assert result.error is None

        events = read_events(hermetic_env)
        assert events[0].payload["cents"] == 0
        assert events[0].payload["ref"] == f"executor:{name}:clamped"

    def test_overflow_ish_cost_from_invoke_is_floored_to_zero_not_raised(self, name, hermetic_env, monkeypatch):
        # int(float('inf')) raises OverflowError (not TypeError/ValueError) --
        # a regression guard found during self-review: the coercion must
        # never let ANY exception type escape run(), not just the two most
        # obvious ones.
        executor = get_executor(name)
        monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("ok", float("inf")))

        result = executor.run("hello")

        assert result.error is None
        assert result.cost_cents == 0

        events = read_events(hermetic_env)
        assert events[0].payload["ref"] == f"executor:{name}:clamped"

    def test_normal_positive_cost_is_unaffected_by_the_conservation_floor(self, name, hermetic_env, monkeypatch):
        executor = get_executor(name)
        monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("ok", 55))

        result = executor.run("hello")

        assert result.cost_cents == 55

        events = read_events(hermetic_env)
        assert events[0].payload["cents"] == 55
        assert events[0].payload["ref"] == f"executor:{name}"  # no :clamped marker

    def test_spend_emission_failure_with_nonempty_text_appends_marker_not_replacing_result(
        self, name, hermetic_env, monkeypatch
    ):
        executor = get_executor(name)
        monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("real output", 10))
        monkeypatch.setattr(
            EventLog, "append", lambda self, type, payload: (_ for _ in ()).throw(OSError("disk full"))
        )

        result = executor.run("hi")

        assert result.text == "real output"
        assert result.cost_cents == 10
        assert result.error == "[spend-emit-failed]"

    def test_spend_emission_failure_with_empty_text_sets_error(self, name, hermetic_env, monkeypatch):
        executor = get_executor(name)
        monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("", 10))
        monkeypatch.setattr(
            EventLog, "append", lambda self, type, payload: (_ for _ in ()).throw(OSError("disk full"))
        )

        result = executor.run("hi")

        assert result.text == ""
        assert result.error is not None
        assert "spend emission failed" in result.error
        assert "disk full" in result.error


# --- actor / env resolution ------------------------------------------------


def test_actor_defaults_to_hostname_when_datacore_actor_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path))
    monkeypatch.delenv("DATACORE_ACTOR", raising=False)
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
    monkeypatch.delenv("DATACORE_NO_SPEND", raising=False)

    executor = get_executor("claude-code")
    monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("ok", 5))

    executor.run("hi")

    events = read_events(tmp_path)
    assert events[0].actor == socket.gethostname()


def test_space_dir_defaults_to_home_data_when_datacore_root_unset(monkeypatch):
    monkeypatch.delenv("DATACORE_ROOT", raising=False)
    monkeypatch.setenv("DATACORE_NO_SPEND", "1")  # avoid touching the real ~/Data
    executor = get_executor("claude-code")
    monkeypatch.setattr(executor, "_invoke", lambda prompt, timeout_s: ("ok", 5))

    # Should not raise even though it resolves to ~/Data -- NO_SPEND keeps it inert.
    result = executor.run("hi")
    assert result.error is None


# --- claude_code adapter ---------------------------------------------------


class TestClaudeCodeAdapter:
    def test_missing_binary_becomes_exec_result_error(self, hermetic_env, monkeypatch):
        import executors.claude_code as claude_code_mod

        monkeypatch.setattr(claude_code_mod.shutil, "which", lambda name: None)
        executor = claude_code_mod.ClaudeCodeExecutor()

        result = executor.run("hello")

        assert result.text == ""
        assert result.error is not None
        assert "claude" in result.error.lower()

    def test_parses_real_cost_from_json_envelope(self, hermetic_env, monkeypatch):
        import executors.claude_code as claude_code_mod

        monkeypatch.setattr(claude_code_mod.shutil, "which", lambda name: "/usr/bin/claude")
        fake_stdout = json.dumps({"result": "hi there", "total_cost_usd": 0.015})

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=fake_stdout, stderr="")

        monkeypatch.setattr(claude_code_mod.subprocess, "run", fake_run)
        executor = claude_code_mod.ClaudeCodeExecutor()

        result = executor.run("hello")

        assert result.text == "hi there"
        assert result.cost_cents == 2  # round(0.015 * 100)
        assert result.error is None

        events = read_events(hermetic_env)
        assert events[0].payload["ref"] == "executor:claude-code"  # real cost, not estimated

    def test_estimates_cost_when_envelope_has_no_cost_fields(self, hermetic_env, monkeypatch):
        import executors.claude_code as claude_code_mod

        monkeypatch.setattr(claude_code_mod.shutil, "which", lambda name: "/usr/bin/claude")
        fake_stdout = json.dumps({"result": "hi there"})

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=fake_stdout, stderr="")

        monkeypatch.setattr(claude_code_mod.subprocess, "run", fake_run)
        executor = claude_code_mod.ClaudeCodeExecutor()

        result = executor.run("hello")

        assert result.text == "hi there"
        assert result.cost_cents >= 0

        events = read_events(hermetic_env)
        assert events[0].payload["ref"] == "executor:claude-code:est"

    def test_nonjson_stdout_with_nonzero_exit_becomes_error(self, hermetic_env, monkeypatch):
        import executors.claude_code as claude_code_mod

        monkeypatch.setattr(claude_code_mod.shutil, "which", lambda name: "/usr/bin/claude")

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom: auth failed")

        monkeypatch.setattr(claude_code_mod.subprocess, "run", fake_run)
        executor = claude_code_mod.ClaudeCodeExecutor()

        result = executor.run("hello")

        assert result.error is not None
        assert "auth failed" in result.error

    def test_is_error_true_sets_error_and_still_emits_spend_with_err_ref(self, hermetic_env, monkeypatch):
        import executors.claude_code as claude_code_mod

        monkeypatch.setattr(claude_code_mod.shutil, "which", lambda name: "/usr/bin/claude")
        fake_stdout = json.dumps(
            {
                "result": "",
                "is_error": True,
                "subtype": "error_during_execution",
                "total_cost_usd": 0.01,
            }
        )

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=fake_stdout, stderr="")

        monkeypatch.setattr(claude_code_mod.subprocess, "run", fake_run)
        executor = claude_code_mod.ClaudeCodeExecutor()

        result = executor.run("hello")

        # Message now names the fault; subtype is the fallback when the
        # envelope carries nothing more specific.
        assert result.error is not None
        assert "error_during_execution" in result.error
        assert "error: success" not in result.error
        assert result.cost_cents == 1  # round(0.01 * 100) -- tokens WERE consumed

        events = read_events(hermetic_env)
        assert len(events) == 1  # in-band error still emits spend
        assert events[0].payload["cents"] == 1
        assert events[0].payload["ref"] == "executor:claude-code:err"

    def test_subtype_not_success_is_an_error_even_when_is_error_absent(self, hermetic_env, monkeypatch):
        import executors.claude_code as claude_code_mod

        monkeypatch.setattr(claude_code_mod.shutil, "which", lambda name: "/usr/bin/claude")
        fake_stdout = json.dumps(
            {
                "result": "partial output",
                "subtype": "error_max_turns",
                "total_cost_usd": 0.02,
            }
        )

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=fake_stdout, stderr="")

        monkeypatch.setattr(claude_code_mod.subprocess, "run", fake_run)
        executor = claude_code_mod.ClaudeCodeExecutor()

        result = executor.run("hello")

        assert result.error is not None
        assert "error_max_turns" in result.error

        events = read_events(hermetic_env)
        assert events[0].payload["ref"] == "executor:claude-code:err"

    def test_subtype_missing_entirely_is_treated_as_success_backward_tolerant(self, hermetic_env, monkeypatch):
        import executors.claude_code as claude_code_mod

        monkeypatch.setattr(claude_code_mod.shutil, "which", lambda name: "/usr/bin/claude")
        fake_stdout = json.dumps({"result": "hi there", "total_cost_usd": 0.01})

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=fake_stdout, stderr="")

        monkeypatch.setattr(claude_code_mod.subprocess, "run", fake_run)
        executor = claude_code_mod.ClaudeCodeExecutor()

        result = executor.run("hello")

        assert result.error is None
        events = read_events(hermetic_env)
        assert events[0].payload["ref"] == "executor:claude-code"  # no :err marker

    def test_explicit_success_subtype_with_is_error_false_is_not_an_error(self, hermetic_env, monkeypatch):
        import executors.claude_code as claude_code_mod

        monkeypatch.setattr(claude_code_mod.shutil, "which", lambda name: "/usr/bin/claude")
        fake_stdout = json.dumps(
            {
                "result": "hi there",
                "is_error": False,
                "subtype": "success",
                "total_cost_usd": 0.01,
            }
        )

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=fake_stdout, stderr="")

        monkeypatch.setattr(claude_code_mod.subprocess, "run", fake_run)
        executor = claude_code_mod.ClaudeCodeExecutor()

        result = executor.run("hello")

        assert result.error is None


# --- hermes adapter ---------------------------------------------------------


class TestHermesAdapter:
    def test_missing_binary_becomes_exec_result_error(self, hermetic_env, monkeypatch):
        import executors.hermes as hermes_mod

        monkeypatch.setattr(hermes_mod.shutil, "which", lambda name: None)
        executor = hermes_mod.HermesExecutor()

        result = executor.run("hello")

        assert result.text == ""
        assert result.error is not None
        assert "hermes" in result.error.lower()

    def test_always_estimates_cost_and_marks_ref(self, hermetic_env, monkeypatch):
        import executors.hermes as hermes_mod

        monkeypatch.setattr(hermes_mod.shutil, "which", lambda name: "/usr/bin/hermes")

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout="hermes reply\n", stderr="")

        monkeypatch.setattr(hermes_mod.subprocess, "run", fake_run)
        executor = hermes_mod.HermesExecutor()

        result = executor.run("hello")

        assert result.text == "hermes reply"
        assert result.error is None

        events = read_events(hermetic_env)
        assert events[0].payload["ref"] == "executor:hermes:est"

    def test_nonzero_exit_becomes_error(self, hermetic_env, monkeypatch):
        import executors.hermes as hermes_mod

        monkeypatch.setattr(hermes_mod.shutil, "which", lambda name: "/usr/bin/hermes")

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="connection refused")

        monkeypatch.setattr(hermes_mod.subprocess, "run", fake_run)
        executor = hermes_mod.HermesExecutor()

        result = executor.run("hello")

        assert result.error is not None
        assert "connection refused" in result.error


# --- api adapter -------------------------------------------------------------


class TestApiAdapter:
    def test_missing_sdk_becomes_exec_result_error_not_import_time(self, hermetic_env, monkeypatch):
        # The module itself must already be imported (via the `executors`
        # package import at module load time) without anthropic installed --
        # proving the guarded import is deferred to _invoke, not module load.
        import executors.api as api_mod

        monkeypatch.setitem(sys.modules, "anthropic", None)
        executor = api_mod.ApiExecutor()

        result = executor.run("hello")

        assert result.text == ""
        assert result.error is not None
        assert "anthropic" in result.error.lower()

    def test_uses_real_usage_tokens_when_present_not_marked_estimated(self, hermetic_env, monkeypatch):
        import executors.api as api_mod

        class FakeTextBlock:
            type = "text"
            text = "hi from the api"

        class FakeUsage:
            input_tokens = 100
            output_tokens = 50

        class FakeResponse:
            content = [FakeTextBlock()]
            usage = FakeUsage()

        class FakeMessages:
            def create(self, **kwargs):
                return FakeResponse()

        class FakeClientWithOptions:
            messages = FakeMessages()

        class FakeClient:
            def with_options(self, **kwargs):
                return FakeClientWithOptions()

        class FakeAnthropicModule:
            def Anthropic(self, *args, **kwargs):
                return FakeClient()

        monkeypatch.setitem(sys.modules, "anthropic", FakeAnthropicModule())
        executor = api_mod.ApiExecutor()

        result = executor.run("hello")

        assert result.text == "hi from the api"
        assert result.error is None

        events = read_events(hermetic_env)
        assert events[0].payload["ref"] == "executor:api"  # real usage tokens, not estimated

    def test_estimates_cost_when_response_has_no_usage(self, hermetic_env, monkeypatch):
        import executors.api as api_mod

        class FakeTextBlock:
            type = "text"
            text = "hi from the api"

        class FakeResponse:
            content = [FakeTextBlock()]
            usage = None

        class FakeMessages:
            def create(self, **kwargs):
                return FakeResponse()

        class FakeClientWithOptions:
            messages = FakeMessages()

        class FakeClient:
            def with_options(self, **kwargs):
                return FakeClientWithOptions()

        class FakeAnthropicModule:
            def Anthropic(self, *args, **kwargs):
                return FakeClient()

        monkeypatch.setitem(sys.modules, "anthropic", FakeAnthropicModule())
        executor = api_mod.ApiExecutor()

        result = executor.run("hello")

        assert result.text == "hi from the api"
        events = read_events(hermetic_env)
        assert events[0].payload["ref"] == "executor:api:est"


# --- live smokes (RUN_LIVE=1 only, claude-code only) ------------------------

RUN_LIVE = os.environ.get("RUN_LIVE") == "1"


@pytest.mark.skipif(not RUN_LIVE, reason="live smoke test requires RUN_LIVE=1 and a working `claude` CLI")
def test_claude_code_live_smoke(hermetic_env):
    executor = get_executor("claude-code")

    result = executor.run("Reply with the single word: pong", timeout_s=60)

    assert result.error is None, result.error
    assert "pong" in result.text.lower()
    assert result.cost_cents >= 0


class TestClaudeCodeSpawnContract:
    """The three spawn arguments that were dropped once and cost days.

    `--permission-mode`, `cwd` and the guard-bypass env were all present, all
    removed by a registry refactor, and their absence was invisible: the agent
    declines every write, reports fluently, and exits 0, so the caller records
    success while no file is ever written. Nothing failed. These assertions are
    the only thing that would notice.
    """

    def _capture(self, monkeypatch, cwd=None):
        import executors.claude_code as m
        monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/claude")
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            seen.update(kw)
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"result": "ok"}), stderr="")

        monkeypatch.setattr(m.subprocess, "run", fake_run)
        m.ClaudeCodeExecutor().run("hello", cwd=cwd)
        return seen

    def test_permission_mode_is_passed(self, hermetic_env, monkeypatch):
        """Without it the agent is read-only and declines every write."""
        seen = self._capture(monkeypatch)
        assert "--permission-mode" in seen["cmd"]
        i = seen["cmd"].index("--permission-mode")
        assert seen["cmd"][i + 1] == "acceptEdits"

    def test_not_the_blanket_bypass(self, hermetic_env, monkeypatch):
        """--dangerously-skip-permissions is refused under root (winston) and
        is a blanket grant where acceptEdits is the narrow one."""
        seen = self._capture(monkeypatch)
        assert "--dangerously-skip-permissions" not in seen["cmd"]

    def test_cwd_reaches_the_subprocess(self, hermetic_env, monkeypatch):
        """acceptEdits is scoped to the working directory, so a lost cwd turns
        every write into a denied out-of-scope write."""
        seen = self._capture(monkeypatch, cwd="/tmp/some-space")
        assert seen["cwd"] == "/tmp/some-space"

    def test_guard_bypass_is_explicit_in_env(self, hermetic_env, monkeypatch):
        """Passed explicitly, not inherited from a mutated os.environ -- the
        PLUR PreToolUse guard is unsatisfiable where the MCP server is absent
        and then refuses every tool call while exiting 0."""
        seen = self._capture(monkeypatch)
        assert seen["env"]["DATACORE_HEADLESS"] == "1"


class TestInBandErrorMessage:
    """An error message must name the fault, not the flag that spotted it.

    `is_error: true` with `subtype: "success"` rendered as "claude reported
    error: success" -- self-contradictory, and it discarded the envelope's
    actual diagnosis. A 15-item suite failed with that string and it produced a
    wrong root cause before anyone read the raw envelope.
    """

    def _err(self, envelope):
        import executors.claude_code as m
        ex = m.ClaudeCodeExecutor()
        ex._in_band_error = None
        ex._check_in_band_error(envelope)
        return ex._in_band_error

    def test_success_subtype_is_never_the_message(self):
        msg = self._err({"is_error": True, "subtype": "success",
                         "api_error_status": 401, "result": "OAuth token revoked"})
        assert msg is not None
        assert "error: success" not in msg
        assert "401" in msg and "OAuth token revoked" in msg

    def test_subtype_is_kept_alongside_richer_detail(self):
        """It is a real signal; only the literal "success" is suppressed."""
        msg = self._err({"is_error": True, "subtype": "error_max_turns",
                         "result": "partial output"})
        assert "subtype=error_max_turns" in msg and "partial output" in msg

    def test_clean_envelope_is_not_an_error(self):
        assert self._err({"is_error": False, "subtype": "success",
                          "result": "fine"}) is None

    def test_permission_denials_are_surfaced(self):
        msg = self._err({"is_error": True, "subtype": "success",
                         "permission_denials": [{"tool": "Write"}, {"tool": "Bash"}]})
        assert "permission_denials=2" in msg


class TestSpendIsAttributable:
    """Spend must be findable, correctly attributed, and linked to its cause.

    All three failed silently. Spend went to DATACORE_ROOT -- not a space, so no
    fold ever read it -- under a hostname actor rather than the declared one,
    with a payload naming only the adapter. A forensic pass over a 15-item run
    concluded `spend.record` did not exist in the schema. It did; it was being
    filed where nobody looks.
    """

    def test_spend_lands_in_the_space_the_caller_named(self, tmp_path, monkeypatch):
        import executors.base as base

        (tmp_path / ".datacore" / "events").mkdir(parents=True)
        monkeypatch.setenv("DATACORE_ACTOR", "miles")
        monkeypatch.delenv("DATACORE_NO_SPEND", raising=False)

        class Fake(base.Executor):
            name = "fake-spend"

            def _invoke(self, prompt, timeout_s):
                return "ok", 7

        Fake().run("hi", space=tmp_path, item="item-abc")

        from ledger.log import read_events
        spend = [e for e in read_events(tmp_path) if e.type == "spend.record"]
        assert len(spend) == 1, "spend must land in the caller's space"
        assert spend[0].actor == "miles", "declared actor, not the hostname"
        assert spend[0].payload["item"] == "item-abc", "linked to what incurred it"
        assert spend[0].payload["executor"] == "fake-spend"
