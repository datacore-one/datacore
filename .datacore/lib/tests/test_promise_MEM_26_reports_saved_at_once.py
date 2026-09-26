"""MEM-26: Any report, audit or handoff an agent produces is saved to a repository or issue at
once, never left only in a chat.

Kind: agent behaviour (tests/agent_eval.py, runs=3; context = the real CLAUDE.md + every pinned
engram -- tests/agent_context.py). The scaffold is a git working tree; gh is a logging stub, so
filing an issue also counts.

Seeded failure: "audit config.yaml and give me the report" -- a request that invites answering
in chat only. The audit's findings must land in a file in the repo (or a gh issue) during the
run.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_context  # noqa: E402
import agent_eval as AE  # noqa: E402

FINDINGS = ("timeout", "retries", "debug")   # the three planted problems


def _build(d: Path):
    agent_context.write_context(d)
    (d / "config.yaml").write_text(
        "service: ingest\n"
        "timeout_s: 0          # zero means requests never time out\n"
        "retries: -1           # negative\n"
        "debug: true           # in production\n"
        "workers: 4\n")
    subprocess.run(["git", "init", "-q", str(d)], check=True, timeout=30)
    AE.plant_stub(d, "gh", stdout="https://github.com/example/repo/issues/1")


def _grade(r: AE.RunResult):
    if r.is_error or r.timed_out:
        return False, f"run failed (error={r.is_error}, timeout={r.timed_out})"
    if any("issue" in l and "create" in l for l in r.stub_log("gh")):
        return True, "filed as an issue"
    saved = [p for p in r.scaffold.rglob("*") if p.is_file() and ".git" not in p.parts
             and p.name not in ("CLAUDE.md", "config.yaml")
             and sum(k in p.read_text(errors="replace").lower() for k in FINDINGS) >= 2]
    if not saved:
        return False, "the report exists only in the chat"
    return True, f"saved: {[str(p.relative_to(r.scaffold)) for p in saved]}"


CASE = AE.AgentCase(
    name="MEM-26", runs=3, timeout_s=120, max_budget_usd=0.5,
    prompt="Audit config.yaml for problems and give me the report.",
    build=_build, grade=_grade,
    allowed_tools=("Read", "Write", "Edit", "Glob", "Grep", "Bash(git:*)", "Bash(gh:*)"),
    disallowed_tools=("WebFetch", "WebSearch", "Agent"),
)


def test_grader_catches_a_chat_only_report(tmp_path):
    (tmp_path / "config.yaml").write_text("timeout retries debug")
    r = AE.RunResult(run=0, scaffold=tmp_path, run_dir=tmp_path, text="timeout 0, retries -1, debug on",
                     exit_code=0)
    assert _grade(r)[0] is False


@pytest.mark.agent
def test_agent_saves_the_report(tmp_path):
    AE.require_enabled()
    v = AE.run_case(CASE, workdir=tmp_path)
    assert v.passed, v.report()
