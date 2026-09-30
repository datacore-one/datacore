"""One heading that reaches two hosts gets ONE identity.

Fleet week simulation, finding 8 (2026-09-30): a heading committed without an
`:ID:` reached two hosts; each host's hourly ingest ran ensure-ids and minted
its own random UUID, both committed, and their next converge conflicted on the
`:ID:` line -- "human needed". Every space's first ingest on every sandbox host
did this.

The rule this keeps (org_transaction.new_org_id): identity is never inferred
from a title. Two captures with the same words are two tasks. What makes two
hosts' copies the SAME heading is that they arrived through the same commit,
so the identity of a heading that is already committed without an ID is
derived from the commit that introduced it, the file and its position among
identical headings. A heading that is not committed yet (a fresh local
capture) still gets an independent random identity.
"""
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import org_workspace_adapter as adapter  # noqa: E402
from org_workspace import OrgWorkspace  # noqa: E402

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], env=ENV, capture_output=True, text=True, timeout=60)


def _ids(path: Path) -> dict:
    ws = OrgWorkspace()
    ws.load(str(path))
    return [(n.heading, n.id()) for n in ws.all_nodes()]


def _fleet(tmp_path: Path, text: str) -> tuple[Path, Path, Path]:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], env=ENV, check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], env=ENV, check=True)
    (seed / "org").mkdir()
    (seed / "org" / "inbox.org").write_text(text)
    git(seed, "add", "-A")
    assert git(seed, "commit", "-qm", "capture without ids").returncode == 0
    assert git(seed, "push", "-q", "origin", "HEAD:main").returncode == 0
    a, b = tmp_path / "hostA", tmp_path / "hostB"
    for d in (a, b):
        subprocess.run(["git", "clone", "-q", str(bare), str(d)], env=ENV, check=True)
    return bare, a, b


def test_a_committed_heading_gets_the_same_id_on_two_clones_and_they_merge(tmp_path):
    _, a, b = _fleet(tmp_path, "* TODO Renew the domain\n* TODO Renew the domain\n* Someday\n")
    for d in (a, b):
        adapter.cmd_ensure_ids(Namespace(file=str(d / "org" / "inbox.org")))
    ia, ib = _ids(a / "org" / "inbox.org"), _ids(b / "org" / "inbox.org")
    assert all(i for _, i in ia), ia
    assert ia == ib, f"two hosts gave the same committed headings different ids:\n{ia}\n{ib}"
    assert len({i for _, i in ia}) == 3, "identical headings in one file still need distinct ids"
    # And the fleet converges without a person: both commit, both push/pull.
    for d in (a, b):
        git(d, "commit", "-qam", f"ids from {d.name}")
    assert git(a, "push", "-q", "origin", "HEAD:main").returncode == 0
    pull = git(b, "pull", "-q", "--no-rebase", "origin", "main")
    assert pull.returncode == 0, pull.stdout + pull.stderr


def test_an_uncommitted_capture_still_gets_an_independent_identity(tmp_path):
    """Same words typed on two hosts are two captures, not one."""
    _, a, b = _fleet(tmp_path, "* Someday\n")
    for d in (a, b):
        f = d / "org" / "inbox.org"
        f.write_text(f.read_text() + "* TODO Call the bank\n")
        adapter.cmd_ensure_ids(Namespace(file=str(f)))
    ia = dict(_ids(a / "org" / "inbox.org"))
    ib = dict(_ids(b / "org" / "inbox.org"))
    assert ia["Someday"] == ib["Someday"]
    assert ia["Call the bank"] != ib["Call the bank"]


def test_two_separate_commits_of_the_same_words_are_two_identities(tmp_path):
    _, a, b = _fleet(tmp_path, "* Someday\n")
    for d in (a, b):
        f = d / "org" / "inbox.org"
        f.write_text(f.read_text() + "* TODO Call the bank\n")
        git(d, "commit", "-qam", f"capture on {d.name}")
        adapter.cmd_ensure_ids(Namespace(file=str(f)))
    assert dict(_ids(a / "org" / "inbox.org"))["Call the bank"] != \
        dict(_ids(b / "org" / "inbox.org"))["Call the bank"]


def test_outside_git_ids_are_random_and_existing_ids_never_change(tmp_path):
    f = tmp_path / "inbox.org"
    f.write_text("* TODO One\n:PROPERTIES:\n:ID: keep-me\n:END:\n* TODO Two\n")
    adapter.cmd_ensure_ids(Namespace(file=str(f)))
    ids = dict(_ids(f))
    assert ids["One"] == "keep-me" and ids["Two"]
