"""MEM-69: A fresh install sets up memory completely, including automatic recall, with no
extra step for the new user.

Kind: deterministic (the fresh install as a new user gets it) + install-path contract.
Automatic recall is the prompt-time hook: on every UserPromptSubmit, PLUR's hook-inject
puts the relevant engrams in front of the model. Registering the MCP server only exposes
tools (ENG-2026-09-21-062).
  * fresh install: `git archive HEAD` unpacked in tmp (tests/_fresh_install.py, the
    harness INS-1 uses), HOME empty. The Claude Code settings it ships
    (.claude/settings.json and .datacore/settings.json, the file `datacore init`
    re-points at the install) must wire a UserPromptSubmit hook that runs PLUR injection;
    no user-level hook exists for a new user.
  * install path: the shipped installer (datacore-cli dist, what `datacore init` runs)
    performs PLUR's hook setup itself (`plur init`, or writes the recall hook) -- a new
    user is not left to run `plur init` by hand.
Control: the predicate recognises the recall hook `plur init` installs, as wired in this
machine's ~/.claude/settings.json (plur_inject_wrapper.py / plur-hook).

Seeded failure: a settings file whose UserPromptSubmit hooks are only session_firstmsg /
session_time_guardian / command_suggest -> no recall (that is the shipped state today).
"""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fresh_install as F  # noqa: E402

CLI_DIST = F.REAL / "3-fds" / "2-projects" / "datacore-cli" / "dist" / "index.js"
RECALL = re.compile(r"hook-inject|plur_inject|plur-hook\s+hook-(inject|prompt)|plur\s+hook\s+inject")


def recall_hooks(settings: dict) -> list[str]:
    out = []
    for m in (settings.get("hooks") or {}).get("UserPromptSubmit") or []:
        for h in m.get("hooks") or []:
            cmd = str(h.get("command", ""))
            if RECALL.search(cmd):
                out.append(cmd)
    return out


@pytest.fixture(scope="module")
def fresh(tmp_path_factory):
    return F.unpack(tmp_path_factory.mktemp("mem69"))


def test_control_the_predicate_sees_the_hook_plur_init_installs():
    user = Path.home() / ".claude" / "settings.json"
    if not user.is_file():
        pytest.fail("no ~/.claude/settings.json on this machine to calibrate against")
    s = json.loads(user.read_text())
    assert recall_hooks(s), "control: the owner's own recall hook is not recognised -- fix RECALL"
    shipped_like = {"hooks": {"UserPromptSubmit": [{"hooks": [
        {"command": "python3 ~/Data/.datacore/lib/session_firstmsg.py"},
        {"command": "python3 ~/Data/.datacore/lib/hooks/command_suggest.py"}]}]}}
    assert not recall_hooks(shipped_like)


def test_a_fresh_install_recalls_memory_on_every_prompt(fresh):
    wired = {}
    for rel in (".claude/settings.json", ".datacore/settings.json"):
        p = fresh.data / rel
        if p.is_file():
            wired[rel] = recall_hooks(json.loads(p.read_text()))
    user = fresh.home / ".claude" / "settings.json"
    assert not user.exists()
    assert any(wired.values()), (
        "a fresh install wires no prompt-time memory recall: the UserPromptSubmit hooks it ships "
        f"({', '.join(wired) or 'no settings file'}) never run PLUR hook-inject; only `plur init` adds it")


def test_the_installer_sets_up_plur_hooks_itself():
    assert CLI_DIST.is_file(), f"installer build not found at {CLI_DIST}"
    code = CLI_DIST.read_text(errors="replace")
    runs_plur_init = re.search(r"""["']plur["']\s*,\s*\[\s*["']init["']|plur init["'`]|["']plur-hook["']""", code)
    writes_hook = RECALL.search(code) and "UserPromptSubmit" in code
    assert runs_plur_init or writes_hook, (
        "datacore init installs @plur-ai/mcp and registers the MCP server but never runs `plur init` "
        "nor writes PLUR's UserPromptSubmit hook -- the new user must do it by hand")
