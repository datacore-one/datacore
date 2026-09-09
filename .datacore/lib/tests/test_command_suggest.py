"""The command that already exists must surface when the work is asked for.

Built after 2026-09-09, when an agent asked to do weekly planning improvised a
plan because /weekly-plan was not in the registry. The registry gap is closed
and gated by test_command_registry_complete.py; this covers the other half —
not relying on the agent to remember to look.

The first version of the hook FAILED this file's first test: the request said
"weekly planning" and the entry said "Shape the week", and exact token matching
never connected week/weekly or plan/planning. It scored zero on its own
motivating example. Prefix matching from four characters fixed it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HOOK = REPO / ".datacore" / "lib" / "hooks" / "command_suggest.py"


def run(prompt: str) -> str:
    r = subprocess.run([sys.executable, str(HOOK)],
                       input=json.dumps({"prompt": prompt}),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"hook must always exit 0, got {r.returncode}: {r.stderr}"
    if not r.stdout.strip():
        return ""
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def suggestions(prompt: str) -> list[str]:
    return [l.split("**")[1].lstrip("/")
            for l in run(prompt).splitlines() if l.startswith("- **")]


def test_the_exact_request_that_caused_this_surfaces_weekly_plan():
    got = suggestions("I want to work on the weekly planning, should do it already yesterday")
    assert "weekly-plan" in got, got
    assert got[0] == "weekly-plan", f"it must rank first, got {got}"


def test_other_phrasings_of_the_same_intent_also_find_it():
    for prompt in ("let's plan the week", "run the weekly plan", "shape the week ahead"):
        assert "weekly-plan" in suggestions(prompt), prompt


def test_it_stays_silent_when_nothing_matches():
    assert run("fix this shit") == ""
    assert run("3 is done, else work on 2 wouldn't be possible") == ""


def test_it_stays_silent_when_a_command_is_named_explicitly():
    """Discovery is not the problem once the user has typed the command."""
    assert run("run /weekly-plan properly please") == ""


def test_it_never_returns_more_than_a_handful():
    for prompt in ("weekly planning and review of the week ahead",
                   "check the trading and health and news and github status today"):
        assert len(suggestions(prompt)) <= 4, prompt


def test_malformed_input_fails_open():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == ""
