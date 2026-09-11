"""Privacy gates must inspect committed bytes, including merges and odd paths."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from git_privacy import changed_paths, validate_public_tree

LIB = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("pre_push_under_test", LIB / "pre_push_scan.py")
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "core.hooksPath", "/dev/null")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def commit(repo, message="test"):
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def test_scanner_preserves_unusual_filenames_and_reads_committed_content(repo):
    name = 'odd\n"$(touch SHOULD_NOT_EXIST) €.base.md'
    (repo / name).write_text("contact: private@example.com\n")
    head = commit(repo)
    (repo / name).write_text("clean working tree\n")
    added, modified = scan.collect_changes([head])
    assert name in added
    assert any(path == name and "private@example.com" in line for path, line in scan.added_lines_by_file([head]))
    assert validate_public_tree(repo, head)
    assert not (repo / "SHOULD_NOT_EXIST").exists()


def test_scanner_git_failure_is_not_a_pass(repo):
    with pytest.raises(ValueError):
        scan.collect_changes(["f" * 40])
    with pytest.raises(ValueError):
        list(scan.added_lines_by_file(["f" * 40]))


def test_merge_only_addition_is_scanned(repo):
    (repo / "base").write_text("base\n")
    commit(repo)
    git(repo, "checkout", "-qb", "other")
    (repo / "other").write_text("other\n")
    commit(repo)
    git(repo, "checkout", "-q", "main")
    (repo / "main").write_text("main\n")
    commit(repo)
    git(repo, "merge", "--no-commit", "--no-ff", "other")
    (repo / "merge-only.base.md").write_text("private@example.com\n")
    head = commit(repo, "merge")
    assert "merge-only.base.md" in scan.collect_changes([head])[0]
    assert any(path == "merge-only.base.md" for path, _ in scan.added_lines_by_file([head]))


def test_phantom_gitlink_checked_against_committed_gitmodules(repo):
    (repo / "base").write_text("base\n")
    base = commit(repo)
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},phantom")
    git(repo, "commit", "-qm", "gitlink")
    assert any("gitlink" in message for message in validate_public_tree(repo, "HEAD"))


def test_deleted_file_is_not_scanned_as_existing(repo):
    (repo / "gone").write_text("gone\n")
    commit(repo)
    (repo / "gone").unlink()
    head = commit(repo)
    assert changed_paths(repo, head) == (set(), set())


def test_new_branch_scans_history_beyond_fifty_commits(repo):
    from git_privacy import outgoing_commits
    (repo / 'secret.base.md').write_text('private@example.com\n')
    first = commit(repo)
    for number in range(55):
        git(repo, 'commit', '--allow-empty', '-qm', str(number))
    head = git(repo, 'rev-parse', 'HEAD')
    commits = outgoing_commits(repo, 'origin', [f'refs/heads/main {head} refs/heads/main {"0" * 40}'])
    assert first in commits and len(commits) == 56
    with pytest.raises(ValueError):
        outgoing_commits(repo, 'origin', [f'refs/heads/main {head} refs/heads/main {"f" * 40}'])


def test_staged_private_layer_cannot_hide_behind_clean_working_copy(repo):
    (repo / 'private.base.md').write_text('private@example.com\n')
    git(repo, 'add', '.')
    (repo / 'private.base.md').write_text('clean\n')
    assert validate_public_tree(repo, ':index')


def test_global_hook_scans_all_new_history_and_treats_policy_arguments_as_data(repo, tmp_path):
    import os
    import shutil
    import yaml
    data = repo / 'tooling'
    lib = data / '.datacore' / 'lib'
    lib.mkdir(parents=True)
    for name in ['git_privacy.py', 'context_merge.py']:
        shutil.copyfile(LIB / name, lib / name)
    scanner = lib / 'pre_push_scan.py'
    scanner.write_text('import os,sys,pathlib\npathlib.Path(os.environ["CAPTURE"]).write_text(sys.stdin.read())\n')
    config = data / '.datacore' / 'config'
    config.mkdir()
    name = "test/repo');__import__('pathlib').Path('PWNED').touch();#"
    (config / 'public-repo-denylist.yaml').write_text(yaml.safe_dump({'protected_repos': [name]}))
    (repo / '.gitignore').write_text('tooling/\n')
    first = commit(repo)
    for n in range(55):
        git(repo, 'commit', '--allow-empty', '-qm', str(n))
    head = git(repo, 'rev-parse', 'HEAD')
    capture = repo / 'captured'
    refs = f'refs/heads/main {head} refs/heads/main {"0" * 40}\n'
    script = LIB.parents[0] / 'githooks' / 'pre-push'
    result = subprocess.run(['bash', str(script), 'origin', f'https://github.com/{name}.git'],
                            cwd=repo, input=refs, text=True, capture_output=True,
                            env=dict(os.environ, DATA_DIR=str(data), CAPTURE=str(capture), SKIP_LOCAL_CI='1'))
    assert result.returncode == 0, result.stderr
    assert first in capture.read_text().splitlines()
    assert len(capture.read_text().splitlines()) == 56
    assert not (repo / 'PWNED').exists()


def test_repository_hook_passes_same_ref_list_to_lfs(repo):
    import os
    (repo / 'content').write_text('safe\n')
    head = commit(repo)
    bindir = repo / 'bin'
    bindir.mkdir()
    lfs = bindir / 'git-lfs'
    lfs.write_text('#!/usr/bin/env python3\nimport os,sys,pathlib\npathlib.Path(os.environ["CAPTURE"]).write_text(sys.stdin.read())\n')
    lfs.chmod(0o700)
    capture = repo / 'lfs-input'
    refs = f'refs/heads/main {head} refs/heads/main {"0" * 40}\nrefs/heads/other {head} refs/heads/other {"0" * 40}\n'
    script = LIB.parents[0] / 'hooks' / 'pre-push'
    result = subprocess.run(['bash', str(script), 'origin', 'https://example.com/repo.git'], cwd=repo,
                            input=refs, text=True, capture_output=True,
                            env=dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'], CAPTURE=str(capture),
                                     DATACORE_ROOT=str(LIB.parents[1])))
    assert result.returncode == 0, result.stderr
    assert capture.read_text() == refs
