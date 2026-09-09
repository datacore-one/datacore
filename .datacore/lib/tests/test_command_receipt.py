"""A command with no receipt was not run.

On 2026-09-09 the owner asked whether the weekly plan had been produced with the
saved /weekly-plan method. It had not — it was improvised — and nothing in the
repository could have shown that. The only witness was the agent, whose first
answer was wrong.

Nightshift writes an execution record per task; commands wrote nothing. This
records invocation from the harness hook, before the agent acts, so the evidence
does not depend on the agent reporting honestly.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HOOK = REPO / ".datacore" / "lib" / "hooks" / "command_receipt.py"


def run(args, home, stdin=""):
    return subprocess.run([sys.executable, str(HOOK), *args], input=stdin,
                          capture_output=True, text=True, timeout=30,
                          env={"HOME": str(home), "PATH": "/usr/bin:/bin"})


def test_an_invocation_is_recorded_and_findable():
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        payload = json.dumps({"tool_name": "Skill",
                              "tool_input": {"skill": "weekly-plan"}})
        assert run([], home, payload).returncode == 0
        r = run(["--check", "weekly-plan"], home)
        assert r.returncode == 0, r.stdout
        assert "invoked 1x" in r.stdout


def test_a_command_never_invoked_fails_the_check():
    """The whole point: absence is reportable, and it is not a soft signal."""
    with tempfile.TemporaryDirectory() as td:
        r = run(["--check", "weekly-plan"], Path(td))
        assert r.returncode == 1
        assert "was NOT invoked" in r.stdout


def test_a_leading_slash_is_the_same_command():
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        run([], home, json.dumps({"tool_name": "SlashCommand",
                                  "tool_input": {"command": "/weekly-plan"}}))
        assert run(["--check", "weekly-plan"], home).returncode == 0
        assert run(["--check", "/weekly-plan"], home).returncode == 0


def test_the_hook_never_blocks_on_bad_input():
    with tempfile.TemporaryDirectory() as td:
        assert run([], Path(td), "not json").returncode == 0
        assert run([], Path(td), json.dumps({"tool_name": "Skill"})).returncode == 0


def test_listing_a_day_shows_what_ran():
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        for name in ("today", "weekly-plan", "wrap-up"):
            run([], home, json.dumps({"tool_name": "Skill",
                                      "tool_input": {"skill": name}}))
        out = run(["--list"], home).stdout
        assert "3 invocation(s)" in out
        for name in ("today", "weekly-plan", "wrap-up"):
            assert f"/{name}" in out
