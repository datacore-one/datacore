"""A publication's private checkout must not depend on files it does not publish.

2026-09-30 22:21 UTC to 2026-10-01 07:10 UTC: every nightshift claim in 3-fds but
two was refused with "claim publication failed". A claim publishes one file, the
writer's events log, through `commit_to_branch` onto a private candidate branch.
That makes two private worktrees, and until this change both were FULL checkouts
of the space.

3-fds holds 179 brand images committed as raw PNGs under an LFS rule
(`filter=lfs`, but the stored blob is the image, not a pointer). In a fresh
checkout those files can be written in the same second as the index, so Git
re-runs the LFS clean filter on them, gets a pointer, and reports them as
modified. The candidate check then refused: "checkout hook changed publication
workspace". Whether a claim got through depended on the clock. The retained
candidate worktree of the 07:09:42 failure, read on the host, shows exactly
those PNGs as ` M`.

The other way the same dependency fails is an LFS object that is missing from
the LFS server and from the local cache: with `filter.lfs.required`, the
checkout itself fails, on a file the claim never touches.

Both are modelled here with a plain Git filter, so the eval does not need
git-lfs or a network. A real git-lfs variant runs when git-lfs is installed.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

EVENTS = '.datacore/events/nightshift.jsonl'
LFS_FILTER = (('clean', 'git-lfs clean -- %f'), ('smudge', 'git-lfs smudge -- %f'),
              ('process', 'git-lfs filter-process'), ('required', 'true'))


def _git(repo, *args, check=True):
    result = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(f'git {args}: {result.stderr}')
    return result.stdout.strip()


def _space(tmp_path, populate):
    """A space with an origin, its events log, and whatever `populate` adds."""
    origin = tmp_path / 'origin.git'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(origin)], check=True)
    repo = tmp_path / '3-fds'
    subprocess.run(['git', 'clone', '-q', str(origin), str(repo)], check=True,
                   capture_output=True)
    for key, value in (('user.email', 't@t'), ('user.name', 't')):
        _git(repo, 'config', key, value)
    (repo / '.datacore' / 'events').mkdir(parents=True)
    (repo / EVENTS).write_text('{"n": 1}\n')
    populate(repo)
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'base')
    _git(repo, 'branch', '-M', 'main')
    _git(repo, 'push', '-q', '-u', 'origin', 'main')
    _git(repo, 'remote', 'set-head', 'origin', 'main')
    return repo


def _publish_claim_shaped(repo):
    """What publish_claim_ref does: a candidate branch at the remote base, then
    an append-only publication of the events log onto it."""
    from knowledge_commit import commit_to_branch
    base = _git(repo, 'rev-parse', 'origin/main')
    branch = 'datacore/claim-candidates/nightshift/0123456789abcdef'
    _git(repo, 'update-ref', f'refs/heads/{branch}', base, '0' * len(base))
    with (repo / EVENTS).open('a') as log:
        log.write('{"n": 2}\n')
    sha = commit_to_branch(repo, branch, [EVENTS], 'ledger: nightshift claim',
                           push=False, append_only=True)
    return base, branch, sha


def _assert_published_only_the_log(repo, base, branch, sha):
    from publication_state import require_clear
    assert sha, 'the claim log was not committed'
    assert _git(repo, 'rev-parse', f'refs/heads/{branch}') == sha
    assert _git(repo, 'diff', '--name-only', base, sha) == EVENTS
    assert _git(repo, 'show', f'{sha}:{EVENTS}') == '{"n": 1}\n{"n": 2}'
    require_clear(repo)  # no pending record left behind


def _checked_out_in_private_worktrees(repo):
    """Every file the publication's private worktrees wrote, as retained."""
    root = repo / '.git' / 'datacore-publication-workspaces'
    found = set()
    for worktree in root.glob('*/retired-worktree-*/worktree'):
        for path in worktree.rglob('*'):
            rel = path.relative_to(worktree)
            if path.is_file() and rel.parts[0] != '.git':
                found.add(rel.as_posix())
    return found


def test_an_unrelated_file_whose_content_cannot_be_produced_does_not_stop_a_claim(tmp_path):
    """An LFS object missing from the server and the cache, without git-lfs:
    a required smudge filter that fails, on a file the claim never touches."""
    def populate(repo):
        (repo / '.gitattributes').write_text('assets/*.png filter=unavailable\n')
        (repo / 'assets').mkdir()
        (repo / 'assets' / 'FDS_new_day.png').write_bytes(b'\x89PNG not available\n')
        _git(repo, 'config', 'filter.unavailable.clean', 'cat')
        _git(repo, 'config', 'filter.unavailable.smudge', 'false')
        _git(repo, 'config', 'filter.unavailable.required', 'true')
    repo = _space(tmp_path, populate)
    _assert_published_only_the_log(repo, *_publish_claim_shaped(repo))


def test_raw_files_under_a_converting_filter_do_not_make_the_candidate_look_changed(tmp_path):
    """The 3-fds shape: files committed raw under a rule whose clean filter
    rewrites them. A fresh checkout of them reads as modified."""
    def populate(repo):
        brand = repo / '1-tracks' / 'comms' / 'brand'
        brand.mkdir(parents=True)
        for n in range(40):
            (brand / f'style-{n}.png').write_bytes(b'RAW IMAGE BYTES %d\n' % n)
        # Committed BEFORE the rule exists, as the images were in 3-fds.
        _git(repo, 'add', '-A')
        _git(repo, 'commit', '-q', '-m', 'images committed raw')
        (repo / '.gitattributes').write_text('*.png filter=pointer\n')
        _git(repo, 'config', 'filter.pointer.clean', 'tr A-Z a-z')
        _git(repo, 'config', 'filter.pointer.smudge', 'cat')
    repo = _space(tmp_path, populate)
    _assert_published_only_the_log(repo, *_publish_claim_shaped(repo))
    # Whether Git re-reads a raw file in a fresh checkout depends on the clock
    # (same-second index write), so the deterministic check is the property
    # itself: nothing but the published path (and the attribute files that
    # decide how it is stored) was checked out privately.
    assert _checked_out_in_private_worktrees(repo) <= {EVENTS, '.gitattributes'}


@pytest.mark.skipif(shutil.which('git-lfs') is None, reason='git-lfs is not installed')
def test_a_missing_lfs_object_does_not_stop_a_claim(tmp_path):
    """The brief's reproduction with real git-lfs: the history holds an LFS
    pointer whose object is on no server and in no local cache."""
    oid = 'ee44e99e' + '0' * 56
    def populate(repo):
        # The filter settings `git lfs install` writes, as on the nightshift
        # host; its hooks are left out so the setup push uploads nothing.
        for key, value in LFS_FILTER:
            _git(repo, 'config', f'filter.lfs.{key}', value)
        # The object never reached any server: let the setup push go without it.
        _git(repo, 'config', 'lfs.allowincompletepush', 'true')
        assets = repo / '1-tracks' / 'comms' / 'assets'
        assets.mkdir(parents=True)
        # A pointer committed as it would be, with no object behind it.
        (assets / 'FDS_new_day.png').write_text(
            f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 4242\n')
        _git(repo, 'add', '-A')
        _git(repo, 'commit', '-q', '-m', 'pointer only')
        (repo / '.gitattributes').write_text('*.png filter=lfs diff=lfs merge=lfs -text\n')
    repo = _space(tmp_path, populate)
    (tmp_path / 'empty-lfs-server').mkdir()
    _git(repo, 'config', 'lfs.url', (tmp_path / 'empty-lfs-server').as_uri())
    assert not list((repo / '.git' / 'lfs').rglob(oid)), 'the object must be in no cache'
    server = (tmp_path / 'empty-lfs-server').as_uri()
    settings = [f'filter.lfs.{key}={value}' for key, value in LFS_FILTER] + [f'lfs.url={server}']
    fresh = subprocess.run(['git', *(arg for item in settings for arg in ('-c', item)), 'clone',
                            str(tmp_path / 'origin.git'), str(tmp_path / 'fresh')],
                           capture_output=True, text=True)
    # Git reports this as "Clone succeeded, but checkout failed".
    assert 'checkout failed' in fresh.stderr, 'a full checkout of this history must fail, as on the host'
    _assert_published_only_the_log(repo, *_publish_claim_shaped(repo))


def test_the_private_checkout_still_publishes_into_an_existing_tree_exactly(tmp_path):
    """Checking out only the published paths must not drop anything else from
    the commit: the candidate's tree is the base tree plus the change."""
    def populate(repo):
        (repo / 'journal').mkdir()
        (repo / 'journal' / 'a.md').write_text('a\n')
        (repo / 'org').mkdir()
        (repo / 'org' / 'inbox.org').write_text('* x\n')
    repo = _space(tmp_path, populate)
    base, branch, sha = _publish_claim_shaped(repo)
    _assert_published_only_the_log(repo, base, branch, sha)
    assert _git(repo, 'ls-tree', '-r', '--name-only', sha).splitlines() == sorted(
        [EVENTS, 'journal/a.md', 'org/inbox.org'])


def test_both_private_worktrees_stay_reclaimable_once_the_publication_is_on_origin(tmp_path):
    """publication_workspace_gc reclaims a retired worktree only when its index
    is a tree on origin. A checkout of only the published paths must keep the
    whole tree in both worktrees' indexes, or they are retained forever."""
    import publication_workspace_gc as gc
    from knowledge_commit import commit_to_branch

    def populate(repo):
        (repo / 'journal').mkdir()
        (repo / 'journal' / 'a.md').write_text('a\n')
    repo = _space(tmp_path, populate)
    _git(repo, 'checkout', '-q', '-b', 'feature')
    (repo / 'journal' / 'b.md').write_text('b\n')
    sha = commit_to_branch(repo, 'main', ['journal/b.md'], 'journal: b', push=False)
    _git(repo, 'push', '-q', 'origin', f'{sha}:refs/heads/main')
    assert gc.reclaim(repo, apply=True)[:2] == (2, 0)
    assert not _checked_out_in_private_worktrees(repo)
