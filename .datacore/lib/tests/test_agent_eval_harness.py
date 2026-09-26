"""The agent-behaviour eval harness (tests/agent_eval.py) itself.

* Switched off (DATACORE_AGENT_EVALS unset), an agent eval FAILS with
  "agent eval not run (set DATACORE_AGENT_EVALS=1)" -- it never skips and never
  passes. Deterministic.
* pass^k: one failing run fails the case; an advisory rubric never overrides.
  Deterministic (runner replaced by a fake).
* Model registry: an unimplemented family fails loudly, it does not fall back
  to Claude. Deterministic.
* End to end: a real headless agent, one run, creates hello.txt containing hi
  in a throwaway scaffold, with HOME/DATACORE_ROOT/DATACORE_STATE in tmp.
  Runs only with DATACORE_AGENT_EVALS=1 (and fails otherwise, by design).
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_eval as AE  # noqa: E402


def test_switched_off_fails_not_skips(tmp_path):
    """Run a real agent test in a child pytest with the switch unset: it must FAIL."""
    t = tmp_path / "test_child.py"
    t.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
        import agent_eval as AE
        def test_case():
            AE.require_enabled()
    """))
    env = {k: v for k, v in os.environ.items() if k != AE.ENV_SWITCH}
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rA", str(t)],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    out = p.stdout + p.stderr
    assert p.returncode == 1, out
    assert "1 failed" in out and "skipped" not in out, out
    assert AE.NOT_RUN in out, out


def _fake_runner(outcomes):
    calls = iter(outcomes)

    def runner(prompt, scaffold, home, state, **kw):
        # isolation is what the real runner relies on: fresh tmp scaffold, home, state
        assert not str(scaffold).startswith(str(AE.ROOT) + os.sep)
        assert home.is_dir() and state.is_dir()
        content = next(calls)
        if content is not None:
            (scaffold / "hello.txt").write_text(content)
        return 0, "done", {"result": "done"}, False
    return runner


def _hello_case(**kw):
    return AE.AgentCase(name="hello", prompt="x", build=lambda d: None,
                        grade=lambda r: (r.file("hello.txt").strip() == "hi", "hello.txt == hi"), **kw)


def test_pass_k_every_run_must_pass(monkeypatch, tmp_path):
    monkeypatch.setitem(AE.RUNNERS, "claude", _fake_runner(["hi", "hi", "hi"]))
    assert AE.run_case(_hello_case(runs=3), workdir=tmp_path / "a").passed
    monkeypatch.setitem(AE.RUNNERS, "claude", _fake_runner(["hi", None, "hi"]))
    v = AE.run_case(_hello_case(runs=3), workdir=tmp_path / "b")
    assert not v.passed and [r.graded for r in v.runs] == [True, False, True], v.report()


def test_rubric_is_advisory_only(monkeypatch, tmp_path):
    monkeypatch.setitem(AE.RUNNERS, "claude", _fake_runner([None]))
    v = AE.run_case(_hello_case(runs=1, rubric=lambda r: "LOOKS GREAT, PASS"), workdir=tmp_path)
    assert not v.passed and v.runs[0].advisory == "LOOKS GREAT, PASS"


def test_runs_are_isolated_copies(monkeypatch, tmp_path):
    monkeypatch.setitem(AE.RUNNERS, "claude", _fake_runner(["hi", "hi"]))
    v = AE.run_case(_hello_case(runs=2), workdir=tmp_path)
    assert v.runs[0].scaffold != v.runs[1].scaffold


def test_unimplemented_family_fails_loudly(tmp_path):
    (tmp_path / "s").mkdir()
    for fam in ("deepseek", "glm", "gpt"):
        with pytest.raises(NotImplementedError):
            AE.run_agent("x", tmp_path / "s", model=fam, runs=1, workdir=tmp_path / fam)
    with pytest.raises(ValueError):
        AE.run_agent("x", tmp_path / "s", model="nope", runs=1, workdir=tmp_path / "nope")


@pytest.mark.agent
def test_end_to_end_hello(tmp_path):
    AE.require_enabled()
    case = AE.AgentCase(
        name="hello", runs=1, timeout_s=150, allowed_tools=("Write",),
        prompt="Create a file named hello.txt in the current directory containing exactly: hi",
        build=lambda d: None,
        grade=lambda r: (r.file("hello.txt").strip() == "hi" and r.exit_code == 0,
                         f"hello.txt={r.file('hello.txt')!r} exit={r.exit_code}"))
    v = AE.run_case(case, workdir=tmp_path)
    assert v.passed, v.report()

