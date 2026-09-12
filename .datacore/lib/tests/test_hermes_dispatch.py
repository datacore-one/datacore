"""Provider result and installation contracts at the real Datacore adapter."""
import io
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def wrapper_runtime(monkeypatch):
    from executors import hermes_oneshot as wrapper
    result = {"completed": True, "final_response": "Valid Unicode reply: \u20ac"}
    calls = []
    class Agent:
        def __init__(self, **kwargs): pass
        def run_conversation(self, prompt):
            calls.append(prompt)
            return result
    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=Agent))
    monkeypatch.setitem(sys.modules, "hermes_cli", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "hermes_cli.config", SimpleNamespace(load_config=lambda: {"model": "fixture"}))
    monkeypatch.setitem(sys.modules, "hermes_cli.runtime_provider", SimpleNamespace(resolve_runtime_provider=lambda **kwargs: {}))
    monkeypatch.setattr(wrapper, "install_policy_guard", lambda: None)
    monkeypatch.setattr(sys, "argv", ["hermes_oneshot.py", "--stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("Synthetic request"))
    def exit(code): raise SystemExit(code)
    monkeypatch.setattr(wrapper.os, "_exit", exit)
    return wrapper, result, calls


def test_wrapper_accepts_completed_nonempty_result(wrapper_runtime, capsys):
    wrapper, result, calls = wrapper_runtime
    with pytest.raises(SystemExit) as stopped:
        wrapper.main()
    assert stopped.value.code == 0 and calls == ["Synthetic request"]
    assert capsys.readouterr().out == result["final_response"] + "\n"


@pytest.mark.parametrize("failure", [
    {"completed": False}, {"completed": "false"}, {"completed": 1},
    {"failed": True}, {"partial": True}, {"error": "Provider failed"},
])
def test_wrapper_does_not_report_partial_text_as_success(wrapper_runtime, capsys, failure):
    wrapper, result, _ = wrapper_runtime
    result.update(failure)
    with pytest.raises(SystemExit) as stopped:
        wrapper.main()
    assert stopped.value.code != 0
    captured = capsys.readouterr()
    assert result["final_response"] in captured.out  # retain useful partial output
    assert "incomplete" in captured.err


@pytest.mark.parametrize("response", ["", " \n", [], {"text": "invalid"}, 1])
def test_wrapper_refuses_empty_or_invalid_response(wrapper_runtime, response):
    wrapper, result, _ = wrapper_runtime
    result["final_response"] = response
    with pytest.raises(SystemExit) as stopped:
        wrapper.main()
    assert stopped.value.code != 0


@pytest.fixture
def adapter_runtime(tmp_path, monkeypatch):
    from executors import hermes as adapter
    import actor_identity
    registry = tmp_path / "principals.yaml"
    registry.write_text("principals:\n  fixture: {writes_as: [worker]}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry)
    monkeypatch.setenv("DATACORE_NO_SPEND", "1")
    monkeypatch.setenv("DATACORE_ACTOR", "worker")
    monkeypatch.setattr(adapter.os.path, "isfile", lambda path: True)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "Synthetic success", "")
    monkeypatch.setattr(adapter, "run_process", run)
    return adapter, calls


def test_adapter_uses_explicit_installed_interpreter(adapter_runtime, monkeypatch, tmp_path):
    adapter, calls = adapter_runtime
    python = str(tmp_path / "qualified-runtime/bin/python")
    monkeypatch.setenv("DATACORE_HERMES_PYTHON", python)
    result = adapter.HermesExecutor().run("Private request", cwd=tmp_path)
    assert not result.error
    command, options = calls[0]
    assert command[0] == python and "Private request" not in command
    assert options["input"] == "Private request"


@pytest.mark.parametrize("python", ["python", "", "relative/bin/python", "/tmp/../other/python", "~/runtime/python"])
def test_adapter_refuses_invalid_explicit_interpreter_without_fallback(adapter_runtime, monkeypatch, python):
    adapter, calls = adapter_runtime
    monkeypatch.setenv("DATACORE_HERMES_PYTHON", python)
    result = adapter.HermesExecutor().run("Private request")
    assert result.error and calls == []


def test_adapter_refuses_missing_explicit_interpreter_without_legacy_fallback(adapter_runtime, monkeypatch):
    adapter, calls = adapter_runtime
    python = "/nonexistent-qualified-runtime/python"
    monkeypatch.setenv("DATACORE_HERMES_PYTHON", python)
    monkeypatch.setattr(adapter.os.path, "isfile", lambda path: path != python)
    result = adapter.HermesExecutor().run("Private request")
    assert result.error and calls == []


def test_adapter_preserves_partial_output_as_failure(adapter_runtime, monkeypatch):
    adapter, _ = adapter_runtime
    monkeypatch.setattr(adapter, "run_process", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 1, "Preserved partial reply", "incomplete"))
    result = adapter.HermesExecutor().run("Synthetic request")
    assert result.error and result.text == "Preserved partial reply"
