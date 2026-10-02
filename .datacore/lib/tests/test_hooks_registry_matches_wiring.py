"""The hooks registry lists every hook actually wired, so rebuilding settings drops none.

Canvas finding (2026-10-01): registry/hooks.yaml says hooks_composer.py composes
.claude/settings.json from it, but it lacked hooks that are live -- in the project
settings (command suggestions, PLUR hook-inject, observation, receipts, the Bash
org guard, ralph-loop safety, the session archive, learn-check) and in the user
settings (the disclosure and integrity guards). Running the composer would have
silently dropped them.

Two checks, both reading hook commands only:
  * project: what the composer would write from the registry is exactly what
    .claude/settings.json has today (event, matcher, command, timeout, async);
  * user: every hook wired in this machine's ~/.claude/settings.json has a
    registry entry with scope "user" (skipped where there is no user settings
    file). A user-scope entry is documentation of what an installer wires; the
    composer never writes it into the project settings.
"""
from __future__ import annotations

import importlib.util
import json
import os
from collections import Counter
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
PROJECT_SETTINGS = ROOT / ".claude" / "settings.json"
USER_SETTINGS = Path(os.environ.get("CLAUDE_USER_SETTINGS", Path.home() / ".claude" / "settings.json"))

_spec = importlib.util.spec_from_file_location("hooks_composer", LIB / "hooks_composer.py")
composer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(composer)
composer.REGISTRY_PATH = str(ROOT / ".datacore" / "registry" / "hooks.yaml")


def _flat(settings: dict) -> Counter:
    rows = Counter()
    for event, groups in (settings.get("hooks") or {}).items():
        for group in groups:
            for hook in group.get("hooks", []):
                rows[(event, group.get("matcher", ""), hook.get("command") or hook.get("prompt"),
                      hook.get("timeout"), bool(hook.get("async")))] += 1
    return rows


def _script_form(command: str) -> str:
    """Path tokens reduced to their file names: the same hook wired from another
    checkout, or with this machine's home spelled out, is the same hook."""
    return " ".join(Path(t.strip('"')).name if "/" in t else t for t in (command or "").split())


def test_rebuilding_the_project_settings_from_the_registry_changes_nothing():
    if not PROJECT_SETTINGS.is_file():
        pytest.skip(f"no project settings at {PROJECT_SETTINGS}")
    wired = _flat(json.loads(PROJECT_SETTINGS.read_text()))
    composed = _flat(composer.compose_claude_settings(
        composer.merge_hooks(composer.load_registry(), composer.load_module_hooks())))
    assert not wired - composed, f"wired but missing from the registry (a rebuild drops them): {sorted(wired - composed)}"
    assert not composed - wired, f"in the registry but not wired (a rebuild adds them): {sorted(composed - wired)}"


def test_every_user_level_hook_is_in_the_registry():
    if not USER_SETTINGS.is_file():
        pytest.skip(f"no user settings at {USER_SETTINGS}")
    registry = composer.load_registry(scope="user")
    known = {(event, h.get("matcher", ""), _script_form(h.get("command", "")))
             for event, hooks in registry.items() for h in hooks}
    missing = sorted({(event, matcher, _script_form(cmd))
                      for (event, matcher, cmd, _t, _a) in _flat(json.loads(USER_SETTINGS.read_text()))
                      } - known)
    assert not missing, f"wired in {USER_SETTINGS} but not in the registry: {missing}"


def test_user_scope_hooks_are_never_composed_into_the_project_settings():
    user = composer.load_registry(scope="user")
    assert user, "the registry declares no user-level hooks"
    project = composer.load_registry()
    assert all(h.get("scope", "project") == "project" for hooks in project.values() for h in hooks)


def test_the_composer_accepts_the_registry_it_would_rebuild_from():
    """rebuild refuses on any validate error; a script path written in the
    portable form ("${CLAUDE_PROJECT_DIR:-...}"/.datacore/...) must resolve."""
    errors, _warnings = composer.validate(composer.merge_hooks(composer.load_registry(), composer.load_module_hooks()))
    assert errors == []
