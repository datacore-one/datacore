"""Every module must live in exactly one repository, and say which.

The policy was written in the root .gitignore and enforced nowhere: a module
declaring `repository:` is an independent clone and stays excluded; one that
declares none is core and belongs in the allowlist. `github` satisfied neither
from April to 2026-08-10 and was in no repository at all — two bugs fixed in it
that day had no history and no backup until someone noticed by accident.

These tests pin the classifier so "noticed by accident" becomes "exits 1".
"""

import importlib.util
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "lib" / "module_placement_audit.py"


@pytest.fixture()
def audit():
    spec = importlib.util.spec_from_file_location("module_placement_audit_t", LIB)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["module_placement_audit_t"] = mod
    spec.loader.exec_module(mod)
    return mod


def _module(tmp_path, name, *, repository=None, clone=False, files=True):
    d = tmp_path / "modules" / name
    d.mkdir(parents=True)
    if files:
        body = f"name: {name}\nversion: 0.1.0\n"
        if repository:
            body += f"repository: {repository}\n"
        (d / "module.yaml").write_text(body)
    if clone:
        (d / ".git").mkdir()
    return d


def _gitignore(tmp_path, allowlisted=()):
    g = tmp_path / ".gitignore"
    lines = [".datacore/modules/*/"]
    lines += [f"!.datacore/modules/{n}/" for n in allowlisted]
    g.write_text("\n".join(lines) + "\n")
    return g


def _run(audit, tmp_path, allowlisted=()):
    return {r["module"]: r for r in
            audit.audit(tmp_path / "modules", _gitignore(tmp_path, allowlisted))}


# --- the two healthy shapes -------------------------------------------------

def test_core_module_is_ok(audit, tmp_path):
    _module(tmp_path, "gtd")
    r = _run(audit, tmp_path, allowlisted=["gtd"])["gtd"]
    assert r["verdict"] == "core" and r["ok"]


def test_independent_module_is_ok(audit, tmp_path):
    _module(tmp_path, "mail", repository="https://github.com/x/datacore-mail", clone=True)
    r = _run(audit, tmp_path)["mail"]
    assert r["verdict"] == "independent" and r["ok"]


# --- the failure this was written for ---------------------------------------

def test_orphan_is_flagged(audit, tmp_path):
    """The github case: no repository:, not allowlisted, not a clone."""
    _module(tmp_path, "github")
    r = _run(audit, tmp_path)["github"]
    assert r["verdict"] == "ORPHAN"
    assert not r["ok"]
    assert "no repository" in r["detail"].lower()


def test_github_style_orphan_becomes_ok_once_allowlisted(audit, tmp_path):
    """The fix applied on 2026-08-10 must actually clear the finding."""
    _module(tmp_path, "github")
    assert _run(audit, tmp_path)["github"]["verdict"] == "ORPHAN"
    assert _run(audit, tmp_path, allowlisted=["github"])["github"]["verdict"] == "core"


# --- the other drift shapes -------------------------------------------------

def test_declared_but_not_cloned_is_flagged(audit, tmp_path):
    _module(tmp_path, "dev", repository="https://github.com/x/datacore-dev", clone=False)
    r = _run(audit, tmp_path)["dev"]
    assert r["verdict"] == "UNCLONED" and not r["ok"]


def test_clone_without_a_declaration_is_flagged(audit, tmp_path):
    """datacore-campaigns: a real repo that nothing records the origin of."""
    _module(tmp_path, "campaigns", clone=True)
    r = _run(audit, tmp_path)["campaigns"]
    assert r["verdict"] == "UNDECLARED" and not r["ok"]


def test_both_rules_at_once_is_contradictory(audit, tmp_path):
    """research: allowlisted AND declaring a remote."""
    _module(tmp_path, "research", repository="https://github.com/x/module-research", clone=True)
    r = _run(audit, tmp_path, allowlisted=["research"])["research"]
    assert r["verdict"] == "CONTRADICTORY" and not r["ok"]


def test_empty_stub_is_not_a_problem(audit, tmp_path):
    """An uncloned module leaves an empty dir; that is not data loss."""
    _module(tmp_path, "datacore-campaigns", files=False)
    r = _run(audit, tmp_path)["datacore-campaigns"]
    assert r["verdict"] == "EMPTY-STUB" and r["ok"]


# --- hygiene ----------------------------------------------------------------

def test_non_module_directories_are_skipped(audit, tmp_path):
    _module(tmp_path, "node_modules")
    _module(tmp_path, "state")
    _module(tmp_path, "real")
    assert set(_run(audit, tmp_path)) == {"real"}


def test_allowlist_parsing_ignores_the_exclude_line(audit, tmp_path):
    (tmp_path / "modules").mkdir(parents=True)
    g = tmp_path / ".gitignore"
    g.write_text(".datacore/modules/*/\n!.datacore/modules/gtd/\n!.datacore/modules/outbox/\n")
    assert audit.allowlisted_modules(g) == {"gtd", "outbox"}


def test_repository_field_is_read_without_quotes(audit, tmp_path):
    d = _module(tmp_path, "m")
    (d / "module.yaml").write_text('name: m\nrepository: "https://github.com/x/y"\n')
    assert audit.declared_repository(d) == "https://github.com/x/y"


def test_missing_manifest_is_not_a_crash(audit, tmp_path):
    d = tmp_path / "modules" / "weird"
    d.mkdir(parents=True)
    (d / "stray.txt").write_text("x")
    r = _run(audit, tmp_path)["weird"]
    assert r["verdict"] == "ORPHAN"
