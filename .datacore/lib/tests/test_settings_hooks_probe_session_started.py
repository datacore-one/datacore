"""The hook probe models a session already under way.

PLUR's session guard refuses the first tool call of a session that has not
started its memory session, once, as a nudge. A probe that starts every call
in a fresh session therefore had every write refused by that nudge, whatever
the rule under test decided: an "is allowed" check failed for the wrong
reason, and an "is refused" check passed for the wrong reason.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings_hooks  # noqa: E402

# A stand-in for the PLUR guard: refuses unless the session's start marker exists.
GUARD = ('python3 -c "import os,sys,json; sys.stdin.read(); '
         "p=os.path.join(os.environ['TMPDIR'],'plur-session-promise-eval-probe'); "
         "print(json.dumps({} if os.path.exists(p) else "
         "{'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny'}}))\"")


def _settings(tmp_path: Path) -> Path:
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Write", "hooks": [{"type": "command", "command": GUARD}]}]}}))
    return s


def test_a_probe_runs_in_a_session_whose_memory_session_has_started(tmp_path):
    out = settings_hooks.probe("PreToolUse", {"tool_input": {"file_path": "/x", "content": "y"}},
                               tool="Write", settings=_settings(tmp_path))
    assert out.ran, "the stand-in guard never ran"
    assert out.decision != "deny", "the probe's session looks unstarted to PLUR's session guard"
