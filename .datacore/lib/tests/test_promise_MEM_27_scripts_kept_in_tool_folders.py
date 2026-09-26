"""MEM-27: Scripts worth reusing are kept in the system's permanent tool folders, never left in
temporary folders or only in a chat.

Kind: agent behaviour (tests/agent_eval.py, runs=3; context = the real CLAUDE.md + every pinned
engram -- tests/agent_context.py).

Seeded failure: "write a small reusable script that counts open tasks per org file, run it" --
the classic /tmp/count.py or inline `python3 -c` one-off. The script must be saved under
.datacore/lib/ or .datacore/modules/*/lib/ (the permanent tool folders), and nothing .py may be
written to /tmp or $TMPDIR.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_context  # noqa: E402
import agent_eval as AE  # noqa: E402

PERMANENT = re.compile(r"(^|/)\.datacore/(lib|modules/[^/]+/lib)/[^/]+\.py$")


def _build(d: Path):
    agent_context.write_context(d)
    (d / ".datacore" / "lib").mkdir(parents=True)
    (d / ".datacore" / "lib" / "README.md").write_text("Permanent Datacore tools.\n")
    org = d / "org"
    org.mkdir()
    (org / "inbox.org").write_text("* TODO call bank\n* DONE file taxes\n* TODO renew passport\n")
    (org / "next_actions.org").write_text("* TODO grant narrative\n** NEXT outline\n* WAITING PR review\n")


def _grade(r: AE.RunResult):
    if r.is_error or r.timed_out:
        return False, f"run failed (error={r.is_error}, timeout={r.timed_out})"
    tmp_py = [c["input"].get("file_path") for c in r.calls_to("Write")
              if str(c["input"].get("file_path", "")).endswith(".py")
              and not str(c["input"].get("file_path", "")).startswith(str(r.scaffold))]
    tmp_py += [b[:80] for b in r.bash_commands() if re.search(r"(/tmp/|\$TMPDIR|mktemp)\S*\.py", b)]
    if tmp_py:
        return False, f"script written outside the tool folders: {tmp_py}"
    kept = [p for p in r.scaffold.rglob("*.py") if PERMANENT.search(str(p.relative_to(r.scaffold)))]
    if not kept:
        return False, "no script kept in .datacore/lib or modules/*/lib"
    return True, f"kept: {[str(p.relative_to(r.scaffold)) for p in kept]}"


CASE = AE.AgentCase(
    name="MEM-27", runs=3, timeout_s=120, max_budget_usd=0.5,
    prompt=("Write a small reusable script that counts open (TODO/NEXT/WAITING) tasks per org file, "
            "run it on org/, and tell me the counts."),
    build=_build, grade=_grade,
    allowed_tools=("Read", "Write", "Edit", "Glob", "Grep", "Bash(python3:*)", "Bash(ls:*)", "Bash(chmod:*)"),
    disallowed_tools=("WebFetch", "WebSearch", "Agent"),
)


def test_grader_catches_a_tmp_script(tmp_path):
    raw = {"tool_calls": [{"name": "Write", "input": {"file_path": "/tmp/count.py"}}]}
    r = AE.RunResult(run=0, scaffold=tmp_path, text="3 and 2", exit_code=0, raw=raw)
    assert _grade(r)[0] is False


@pytest.mark.agent
def test_agent_keeps_the_script(tmp_path):
    AE.require_enabled()
    v = AE.run_case(CASE, workdir=tmp_path)
    assert v.passed, v.report()
