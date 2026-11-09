"""A guard run as a git hook blocks with exit 1; editing it is not unplugging it.

2026-11-08: the protection hook refused every edit of log_ownership_guard.py as
"a do-nothing stub", because it only recognised the Claude-hook way of blocking
(exit 2 / "deny"). The ownership guard and the ledger commit gate are git hooks
(pre-push, pre-commit) and refuse with exit 1, so even the unchanged file
"could not block". Owner-approved fix: for those two, exit 1 counts as
blocking. A rewrite that leaves them unable to fail is still refused.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import config_protection as C  # noqa: E402

HOOKS = Path(__file__).resolve().parents[1] / "hooks"


def _write(name, content):
    return C.switch_off("Write", {"file_path": str(HOOKS / name), "content": content})


def test_the_real_ownership_guard_counts_as_able_to_block():
    assert _write("log_ownership_guard.py", (HOOKS / "log_ownership_guard.py").read_text()) is None


def test_a_git_hook_guard_that_returns_1_counts_as_blocking():
    src = "def main():\n    if bad:\n        return 1\n    return 0\nraise SystemExit(main())\n"
    assert _write("log_ownership_guard.py", src) is None


def test_a_git_hook_guard_rewritten_to_always_pass_is_still_refused():
    assert _write("log_ownership_guard.py", "import sys\nsys.exit(0)\n")
    assert _write("ledger_write_gate.py", "def main():\n    return 0\n")


def test_a_claude_hook_guard_still_needs_exit_2():
    # exit 1 from a PreToolUse hook does not block a tool call, so it is no guard.
    assert _write("redaction_guard.py", "import sys\nsys.exit(1)\n")
