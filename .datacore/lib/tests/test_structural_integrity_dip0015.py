"""Structural integrity follows DIP-0015 as amended 2026-10-16.

DIPs are the source of truth: no Git LFS (binaries stay out of git), content/
is a valid space folder, the personal space uses 1-active/ and 2-code/, and
entries grandfathered in <space>/.datacore/structure-allow are known drift,
not fresh warnings. The checker must also finish on spaces full of
node_modules, and its headline must not say PASSED over hundreds of warnings.
"""
import os
import subprocess
from pathlib import Path

import pytest
from structural_integrity import StructuralIntegrityChecker, format_summary

HOOK = Path(__file__).resolve().parents[2] / 'hooks' / 'space-pre-commit'


def _git(repo, *args):
    subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)


def _space(tmp_path, name):
    space = tmp_path / name
    space.mkdir()
    _git(space, 'init', '-q')
    for d in ('org', '0-inbox', '3-knowledge'):
        (space / d).mkdir()
    return space


def _messages(checker):
    return [i.message for i in checker.issues]


def test_grandfathered_entries_are_not_warnings(tmp_path):
    space = _space(tmp_path, '3-team')
    (space / 'legacy-dir').mkdir()
    (space / 'OLD-NOTES.md').write_text('x')
    (space / '.datacore').mkdir()
    (space / '.datacore' / 'structure-allow').write_text('# legacy\nlegacy-dir\nOLD-NOTES.md\n')
    checker = StructuralIntegrityChecker(space)
    checker._check_folder_structure()
    flagged = [i for i in checker.issues if i.path.name in ('legacy-dir', 'OLD-NOTES.md')]
    assert flagged, 'grandfathered drift should still be visible'
    assert all(i.severity == 'info' for i in flagged)
    assert all('grandfathered' in i.message for i in flagged)


def test_content_folder_is_valid_in_team_space(tmp_path):
    space = _space(tmp_path, '5-team')
    (space / 'content').mkdir()
    checker = StructuralIntegrityChecker(space)
    checker._check_folder_structure()
    assert not [i for i in checker.issues if i.path.name == 'content']


def test_hook_root_files_are_valid(tmp_path):
    space = _space(tmp_path, '6-team')
    for name in ('mail.yaml', 'VISION.md', '.plur.yaml'):
        (space / name).write_text('x')
    checker = StructuralIntegrityChecker(space)
    checker._check_folder_structure()
    assert not [i for i in checker.issues if i.path.name in ('mail.yaml', 'VISION.md')]


def test_tracked_binary_is_reported_ignored_binary_is_not(tmp_path):
    space = _space(tmp_path, '2-team')
    (space / '3-knowledge' / 'deck.key').write_bytes(b'\0' * 10)
    (space / '3-knowledge' / 'clip.mp4').write_bytes(b'\0' * 10)
    (space / '.gitignore').write_text('*.mp4\n')
    _git(space, 'add', '-A')
    checker = StructuralIntegrityChecker(space)
    checker._check_binaries_in_git()
    paths = {i.path.name for i in checker.issues}
    assert paths == {'deck.key'}
    assert all(i.check_type == 'binary_in_git' and i.severity == 'error' for i in checker.issues)
    assert not any('lfs' in m.lower() for m in _messages(checker))
    assert all('git rm --cached' in i.fix_suggestion for i in checker.issues)


def test_full_run_never_mentions_lfs(tmp_path):
    space = _space(tmp_path, '4-team')
    big = space / '3-knowledge' / 'big.pptx'
    big.write_bytes(b'\0' * (11 * 1024 * 1024))
    (space / '.gitignore').write_text('*.pptx\n')
    result = StructuralIntegrityChecker(space).run_all_checks()
    assert not any('lfs' in i.message.lower() or i.check_type == 'lfs' for i in result.issues)


def test_node_modules_are_not_walked(tmp_path):
    space = _space(tmp_path, '7-team')
    nm = space / '2-projects' / 'app' / 'node_modules' / 'Some_Pkg'
    nm.mkdir(parents=True)
    (nm / 'Bad Name.key').write_text('x')
    (nm / 'broken.md').write_text('[[nowhere]]')
    result = StructuralIntegrityChecker(space).run_all_checks()
    # relative to the space: tmp_path itself is named after this test
    assert not any('node_modules' in i.path.resolve().relative_to(space.resolve()).parts
                   for i in result.issues)


def test_summary_does_not_say_passed_over_warnings(tmp_path):
    space = _space(tmp_path, '8-team')
    (space / 'stray-dir').mkdir()
    result = StructuralIntegrityChecker(space, quick_mode=True).run_all_checks()
    assert result.warnings and not result.errors
    assert 'PASSED' not in format_summary(result)


def _hook(repo, staged):
    for rel in staged:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('x')
    _git(repo, 'add', '-A')
    env = {**os.environ, 'SAFETY_ONLY': '0'}
    return subprocess.run(['sh', str(HOOK)], cwd=repo, capture_output=True, text=True, env=env)


def test_hook_allows_personal_dip_folders(tmp_path):
    repo = tmp_path / '0-personal'
    repo.mkdir()
    _git(repo, 'init', '-q')
    r = _hook(repo, ['1-active/a.md', '2-code/b.md', 'content/reports/c.md', 'notes/journals/d.md'])
    assert r.returncode == 0, r.stdout + r.stderr


def test_hook_allows_content_in_team_space(tmp_path):
    repo = tmp_path / '3-team'
    repo.mkdir()
    _git(repo, 'init', '-q')
    assert _hook(repo, ['content/topic/x.md']).returncode == 0


def test_hook_still_refuses_unknown_root_dir(tmp_path):
    repo = tmp_path / '3-team'
    repo.mkdir()
    _git(repo, 'init', '-q')
    assert _hook(repo, ['random-dir/x.md']).returncode != 0


def test_hook_refuses_personal_folders_in_team_space(tmp_path):
    repo = tmp_path / '3-team'
    repo.mkdir()
    _git(repo, 'init', '-q')
    assert _hook(repo, ['1-active/x.md']).returncode != 0


def test_gitignored_entries_at_space_root_are_not_drift(tmp_path):
    """Files git ignores (an agent workspace, caches) are not content drift."""
    space = _space(tmp_path, '2-team')
    (space / '.gitignore').write_text('SOUL.md\nagent-cache/\n')
    (space / 'SOUL.md').write_text('x')
    (space / 'agent-cache').mkdir()
    (space / 'STRAY.md').write_text('x')
    checker = StructuralIntegrityChecker(space)
    checker._check_folder_structure()
    flagged = {i.path.name for i in checker.issues if i.severity == 'warning'}
    assert 'SOUL.md' not in flagged
    assert 'agent-cache' not in flagged
    assert 'STRAY.md' in flagged, 'untracked, unignored files are still drift'


def test_gitignored_entries_at_datacore_root_are_not_drift(tmp_path):
    from structural_integrity import check_root_directory
    root = tmp_path / 'Data'
    root.mkdir()
    _git(root, 'init', '-q')
    (root / '.gitignore').write_text('.cursor/\nnode_modules/\n')
    for d in ('.cursor', 'node_modules', 'stray-dir', '.ruff_cache'):
        (root / d).mkdir()
    result = check_root_directory(root)
    flagged = {i.path.name for i in result.issues}
    assert '.cursor' not in flagged
    assert 'node_modules' not in flagged
    assert 'stray-dir' in flagged
    assert '.ruff_cache' in flagged, 'an unignored cache dir is real root drift'


def test_root_check_reads_the_shared_root_allowlist(tmp_path):
    """The root hook and CI share .datacore/config/root-allowlist.txt; the checker must too."""
    from structural_integrity import check_root_directory
    root = tmp_path / 'Data'
    (root / '.datacore' / 'config').mkdir(parents=True)
    _git(root, 'init', '-q')
    (root / '.datacore' / 'config' / 'root-allowlist.txt').write_text('# policy docs\nPOLICY\\.md\n')
    (root / 'POLICY.md').write_text('x')
    (root / 'STRAY.md').write_text('x')
    (root / '.plur').mkdir()
    result = check_root_directory(root)
    flagged = {i.path.name for i in result.issues}
    assert 'POLICY.md' not in flagged
    assert '.plur' not in flagged, 'the root hook allows .plur/'
    assert 'STRAY.md' in flagged
