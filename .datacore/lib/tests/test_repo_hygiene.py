"""Invariants about THIS checkout that no unit test would notice."""
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _ignored(path: str) -> bool:
    r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", path], capture_output=True)
    return r.returncode == 0


def test_every_symlink_directly_under_modules_is_ignored():
    """`.datacore/modules/*/` matches directories only. A SYMLINK there (health
    -> a space project) stayed untracked-but-not-ignored: one `git add -A`
    from the public repo. Independent review 2026-09-03."""
    mods = ROOT / ".datacore" / "modules"
    if not mods.exists():
        pytest.skip("no modules dir")
    links = [p for p in mods.iterdir() if p.is_symlink()]
    not_ignored = [p.name for p in links if not _ignored(str(p.relative_to(ROOT)))]
    assert not not_ignored, f"symlinks under .datacore/modules not gitignored: {not_ignored}"


def test_nested_module_repos_are_not_tracked_by_root():
    """Root-tracked mirrors of nested-repo files overwrote the nested repo on
    every checkout (audit 2026-09-03). Zero such paths may be tracked."""
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", ".datacore/modules"], capture_output=True, text=True)
    tracked_dirs = {line.split("/")[2] for line in r.stdout.split() if line.count("/") >= 2}
    nested = {p.name for p in (ROOT / ".datacore" / "modules").iterdir() if (p / ".git").exists()}
    overlap = sorted(tracked_dirs & nested)
    assert not overlap, f"nested repos with root-tracked files: {overlap}"
