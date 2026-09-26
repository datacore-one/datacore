"""SPC-4: The context an AI session reads is rebuilt from its layers, and a stale
or hand-edited composed file is detected.

Kind: deterministic + production contract (local, read-only).

Deterministic (tmp component dir in a tmp git repo that ignores CLAUDE.md):
  * `context_merge.rebuild_context` writes exactly the merge of the layers;
  * after a layer changes, the context checker (`context_merge.py validate
    --path`, the one CLI that inspects a component) exits non-zero and says
    the composed file is stale;
  * after the composed file is edited by hand, the same check says so.

Production (@production, local):
  * a SessionStart hook in the Datacore settings rebuilds or checks the
    composed context (today only session_bootstrap and datacore_sync run);
  * every composed CLAUDE.md on this machine equals what its layers produce
    right now (same code path as `rebuild`, nothing written).

Seeded failure: a layer edited after the last rebuild -- today no command
notices, and a session reads the old composed text.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import context_merge

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]


@pytest.fixture
def component(tmp_path):
    d = tmp_path / "1-alpha"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True, timeout=30)
    (d / ".gitignore").write_text("CLAUDE.md\nAGENTS.md\nGEMINI.md\n*.local.md\n")
    (d / "CLAUDE.base.md").write_text("# Alpha\n\nBase rule.\n")
    (d / "CLAUDE.space.md").write_text("## Space\n\nSpace rule v1.\n")
    ok, msgs = context_merge.rebuild_context(d, "CLAUDE")
    assert ok, msgs
    return d


def _check(d: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(LIB / "context_merge.py"), "validate", "--path", str(d)],
                          capture_output=True, text=True, timeout=30)


def test_rebuild_is_exactly_the_layers(component):
    expected, _ = context_merge.merge_context(component, "CLAUDE")
    assert (component / "CLAUDE.md").read_text() == expected
    assert "Space rule v1." in expected and "Base rule." in expected


def test_a_stale_composed_file_is_detected(component):
    (component / "CLAUDE.space.md").write_text("## Space\n\nSpace rule v2.\n")
    r = _check(component)
    assert r.returncode != 0 and "stale" in (r.stdout + r.stderr).lower(), (
        f"a composed file older than its layers passed the check: rc={r.returncode} {r.stdout.strip()!r}")


def test_a_hand_edited_composed_file_is_detected(component):
    p = component / "CLAUDE.md"
    p.write_text(p.read_text() + "\nHand-added rule nobody layered.\n")
    r = _check(component)
    out = (r.stdout + r.stderr).lower()
    assert r.returncode != 0 and ("edited" in out or "stale" in out or "differs" in out), (
        f"a hand-edited composed file passed the check: rc={r.returncode} {r.stdout.strip()!r}")


@pytest.mark.production
def test_a_session_start_hook_rebuilds_or_checks_the_context():
    commands = []
    for p in (ROOT / ".claude" / "settings.json", ROOT / ".claude" / "settings.local.json",
              Path.home() / ".claude" / "settings.json"):
        try:
            hooks = (json.loads(p.read_text()).get("hooks") or {}).get("SessionStart") or []
        except (OSError, ValueError):
            continue
        commands += [h.get("command", "") for entry in hooks for h in entry.get("hooks", [])]
    assert any("context_merge" in c or "context rebuild" in c for c in commands), (
        f"no SessionStart hook rebuilds or checks the composed context: {commands}")


@pytest.mark.production
def test_every_composed_context_here_matches_its_layers():
    dirs = [ROOT, *sorted(p for p in ROOT.glob("[0-9]-*") if p.is_dir())]
    stale = []
    for d in dirs:
        if not (d / "CLAUDE.base.md").exists():
            continue
        expected, _ = context_merge.merge_context(d, "CLAUDE")
        if (d / ".datacore").is_dir() and context_merge.REGISTRY_MARKER.search(expected):
            expected = context_merge.inject_registries(expected, d / ".datacore")
        out = d / "CLAUDE.md"
        if not out.exists() or out.read_text() != expected:
            stale.append(d.name if d != ROOT else "<root>")
    assert not stale, f"composed CLAUDE.md is stale or hand-edited in: {', '.join(stale)}"
