"""A space numbered 10 or higher is found wherever spaces are enumerated.

Spaces are numbered folders at the install root. The first two-digit space
(`10-…`) was invisible to every caller that enumerated spaces with the
one-digit glob `[0-9]-*`: the task audit, the review board, the ledger seal,
the hook discovery default, knowledge lint and many more. The canonical
discovery is `spaces.py` (`discover_spaces`, `LEGACY_GLOB = "[0-9]*-*"`); a
caller that cannot import it uses the same multi-digit pattern.

Done when: with a tmp install root holding `9-a/` and `10-b/` (each with
`.datacore/config.yaml`), every sampled discovery site finds both, and the
one-digit glob survives in tracked Datacore code only as a documented
exception.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import spaces

NAMES = ("9-a", "10-b")

ORG = """* REVIEW Check the draft of {name}
:PROPERTIES:
:ID: {name}-t1
:END:
"""


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "Data"
    for name in NAMES:
        sp = root / name
        (sp / ".datacore" / "events").mkdir(parents=True)
        (sp / ".datacore" / "config.yaml").write_text(
            f"space:\n  name: {name.split('-', 1)[1]}\n  type: team\n")
        (sp / ".datacore" / "events" / "tester.jsonl").write_text("")
        (sp / "org").mkdir()
        (sp / "org" / "next_actions.org").write_text(ORG.format(name=name))
        (sp / "org" / "inbox.org").write_text("")
        (sp / "3-knowledge").mkdir()
        (sp / "roadmap.yaml").write_text("items: []\n")
    # Not a space: the install's own system folder.
    (root / ".datacore").mkdir()
    return root


def _names(paths) -> set[str]:
    return {Path(p).name for p in paths}


def test_spaces_py_discovers_both(root):
    assert {"9-a", "10-b"} <= {s.path.name for s in spaces.discover_spaces(root)}


def test_hook_discover_spaces_default_pattern(root, monkeypatch):
    import hooks
    (root / ".datacore" / "state").mkdir()
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    monkeypatch.setattr(hooks, "DATACORE_ROOT", root)
    monkeypatch.setattr(hooks, "HOOK_STATE_PATH", root / ".datacore" / "state" / "hook_state.yaml")
    # No pattern given: the default is what is under test. TTL 0 skips any
    # space list cached by an earlier run.
    found = hooks.HookExecutor()._hook_discover_spaces({"cache_ttl_minutes": 0}).data["spaces"]
    assert {"9-a", "10-b"} <= set(found)
    assert ".datacore" not in found


def test_ledger_seal_spaces(root):
    import ledger_seal
    assert _names(ledger_seal._spaces(root)) == {"9-a", "10-b"}


def test_ledger_invariants_spaces(root):
    import ledger_invariants
    assert _names(ledger_invariants._spaces(root)) == {"9-a", "10-b"}


def test_ledger_union_spaces(root):
    (root / "10-b" / ".datacore" / "events" / "tester.jsonl").write_text("{}\n")
    (root / "9-a" / ".datacore" / "events" / "tester.jsonl").write_text("{}\n")
    from ledger.union import ledger_spaces
    assert _names(ledger_spaces(root)) == {"9-a", "10-b"}


def test_knowledge_lint_spaces(root):
    import knowledge_lint
    assert _names(knowledge_lint._spaces(root)) == {"9-a", "10-b"}


def test_org_state_lint_files(root):
    import org_state_lint
    assert {p.parts[-3] for p in org_state_lint._live_org_files(root)} == {"9-a", "10-b"}


def test_delegation_roadmap_spaces(root):
    import delegation_requirements
    assert delegation_requirements.roadmap_spaces(root) == {"9-a", "10-b"}


def test_task_audit_loads_both(root, monkeypatch):
    pytest.importorskip("org_workspace")
    import task_audit
    monkeypatch.chdir(root)
    tasks, _ = task_audit.load_tasks()
    assert {t["space"] for t in tasks} == {"9-a", "10-b"}


def test_review_board_finds_both(root, monkeypatch):
    pytest.importorskip("org_workspace")
    import review_board
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    found, _ = review_board.discover(root)
    assert {Path(f).parts[-3] for f, _ in found} == {"9-a", "10-b"}


def test_commit_hook_dispatches_a_two_digit_space(tmp_path):
    """The commit-time dispatcher (githooks/pre-commit) gives `10-b` the full
    space rules, as it gives `9-a`. Outside them a repo gets only the safety
    rules, and a folder outside the space allowlist would be accepted."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_githooks_credential_store import _commit, _install
    install = _install(tmp_path)
    for name in NAMES:
        (install / name / "stray").mkdir(parents=True)
        r = _commit(install / name, install, "stray/notes.md", "hello\n")
        assert r.returncode != 0 and "unexpected root directory" in (r.stdout + r.stderr), (
            name, (r.stdout + r.stderr)[-300:])


# --- the one-digit glob is gone from tracked Datacore code ------------------

#: Files that may still spell the one-digit glob, and why.
EXCEPTIONS = {
    # spaces.py documents the glob it replaces.
    ".datacore/lib/spaces.py": "documents the legacy glob it replaced",
    # Comments recording why the multi-digit pattern exists.
    ".datacore/lib/ledger_ingest_org.py": "comment: the glob the code no longer uses",
    ".datacore/lib/ledger_transport.py": "comment: the registry replaced the glob",
}

ONE_DIGIT = re.compile(r"\[0-9\]-\*")
CODE_SUFFIXES = {".py", ".sh", ".yaml", ".yml", ".md", ""}


def _tracked(repo: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "--", ".datacore"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return out.stdout.splitlines()


def test_one_digit_glob_only_in_documented_exceptions():
    repo = Path(__file__).resolve().parents[3]
    offenders = []
    for rel in _tracked(repo):
        p = Path(rel)
        if p.suffix not in CODE_SUFFIXES or rel in EXCEPTIONS:
            continue
        # Tests may name the old glob in fixtures and docstrings; promise evals
        # are not ours to edit.
        if "/tests/" in rel or rel.startswith(".datacore/tests/"):
            continue
        try:
            text = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if ONE_DIGIT.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not offenders, "one-digit space glob still in use:\n" + "\n".join(offenders)
