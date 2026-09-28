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
