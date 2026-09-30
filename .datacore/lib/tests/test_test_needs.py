"""config/test-needs.yaml may only name what a bare checkout genuinely lacks.

The file lets a test that checks an INSTALLATION go uncollected where the
installation is missing -- a CI runner. That is only honest while every need is
something the repository cannot supply. A need the checkout does have would be
a way to hide a red, so these checks hold the line: a `file:` need is a path the
repository does not track, a `module:` need is a module it does not contain, and
every entry points at a test that exists.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import needs_gate as ng

ENTRIES = ng.load()
AUDITED = {re.split(r"[=<>\[ ]", line.strip(), 1)[0].lower().replace("_", "-")
           for line in (ng.ROOT / ".datacore/lib/requirements-audit.txt").read_text().splitlines()
           if line.strip() and not line.startswith("#")}


def _tracked(path: str) -> bool:
    r = subprocess.run(["git", "-C", str(ng.ROOT), "ls-files", "--", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("not a git checkout")
    return bool(r.stdout.strip())


def test_the_declarations_exist_and_are_not_empty():
    assert ENTRIES, "config/test-needs.yaml declares nothing"


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: ng.keys(e)[0])
def test_every_entry_names_a_real_test_with_a_reason(entry):
    path = ng.ROOT / entry["test"]
    assert path.is_file(), f"{entry['test']} does not exist"
    assert str(entry.get("why") or "").strip(), f"{entry['test']}: no `why`"
    assert entry.get("needs"), f"{entry['test']}: no needs"
    source = path.read_text(encoding="utf-8")
    for name in entry.get("only") or []:
        func = name.split("[", 1)[0]
        assert re.search(rf"^\s*(async\s+)?def {re.escape(func)}\(", source, re.M), (
            f"{entry['test']} has no test {func}")


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: ng.keys(e)[0])
def test_every_need_is_one_the_repository_cannot_supply(entry):
    for need in entry["needs"]:
        kind, _, arg = str(need).partition(":")
        assert kind in ng.KINDS, f"{entry['test']}: unknown need {need!r}"
        if kind == "file" and not arg.startswith("~"):
            assert arg and not _tracked(arg), (
                f"{entry['test']}: {arg} is tracked -- every checkout has it, so it is not a need")
        if kind == "module":
            assert arg and not _tracked(f".datacore/modules/{arg}"), (
                f"{entry['test']}: module {arg} ships in this repository -- not a need")
        if kind == "cli":
            assert arg, f"{entry['test']}: cli need without a command"
        if kind == "python":
            dist = arg.split(".", 1)[0].replace("_", "-").lower()
            assert arg and dist not in AUDITED, (
                f"{entry['test']}: {arg} is in requirements-audit.txt -- CI installs it, so it is not a need")


def test_a_met_need_leaves_the_test_in(tmp_path):
    (tmp_path / "install.yaml").write_text("x")
    entries = [{"test": "t.py", "needs": ["file:install.yaml"], "why": "w"}]
    assert ng.unmet_by_test(root=tmp_path, env={}, entries=entries) == {}
    assert ng.unmet_by_test(root=tmp_path / "bare", env={}, entries=entries) == {
        "t.py": ["file:install.yaml"]}


def test_the_scoreboard_collects_everything(tmp_path):
    entries = [{"test": "t.py", "needs": ["module:nowhere", "agent"], "why": "w"}]
    assert ng.unmet_by_test(root=tmp_path, env={"PROMISE_EVALS_ALL": "1"}, entries=entries) == {}
    assert ng.unmet_by_test(root=tmp_path, env={}, entries=entries) == {"t.py": ["module:nowhere", "agent"]}


def test_only_names_single_tests_and_their_parameters():
    entry = {"test": "a/t.py", "only": ["test_x"], "needs": ["agent"]}
    (key,) = ng.keys(entry)
    assert ng.matches("a/t.py::test_x", key)
    assert ng.matches("a/t.py::test_x[p1-p2]", key)
    assert not ng.matches("a/t.py::test_x_other", key)
    assert ng.matches("a/t.py::test_y", "a/t.py")
    assert not ng.matches("a/t2.py::test_y", "a/t.py")


def test_an_unknown_need_is_an_error_not_a_skip(tmp_path):
    with pytest.raises(ValueError):
        ng.met("hosts", tmp_path, {})


def test_a_role_need_finds_the_space_by_its_role_not_its_folder(tmp_path):
    """A path inside a space is declared by the space's ROLE: the folder name
    and its number are this install's own, and a fresh install must not ship
    them (INS-3). 2026-09-29: a file: need named the system space's folder."""
    (tmp_path / "7-sys" / ".datacore").mkdir(parents=True)
    (tmp_path / "7-sys" / "daemon").mkdir()
    (tmp_path / "install.yaml").write_text("roles:\n  system: sys\n")
    assert ng.met("role:system/daemon", root=tmp_path)
    assert not ng.met("role:system/missing", root=tmp_path)
    assert not ng.met("role:product/daemon", root=tmp_path)      # role not declared
    assert not ng.met("role:system/daemon", root=tmp_path / "bare")


def test_a_role_need_works_without_lib_on_the_import_path(tmp_path):
    """Finding 9 of the fleet week simulation (2026-09-30): the conftest loads
    needs_gate by file path, and a `role:` need then did `import spaces`, which
    only works when .datacore/lib is on sys.path (this Mac adds it through a
    user-site .pth). pytest started from anywhere else crashed at conftest
    import with ModuleNotFoundError: spaces. `python -s` drops the user site."""
    import sys
    code = ("import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('needs_gate', {str(ng.ROOT / '.datacore/lib/needs_gate.py')!r})\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "assert not any(p.endswith('.datacore/lib') for p in sys.path), sys.path\n"
            "print(m.met('role:no-such-role/x'))\n")
    r = subprocess.run([sys.executable, "-s", "-c", code], cwd=tmp_path, capture_output=True, text=True,
                       timeout=60, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stderr[-1500:]
    assert r.stdout.strip() == "False"
