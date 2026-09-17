"""A publication and a transport autosave must never interleave on one repo.

2026-09-16 22:25:09: nightshift reserved a publication of 0-personal's journal
and inbox; the hourly phase-1 cycle, also at :25, autosaved and committed the
same files seconds later. The branch moved under the reservation, which could
then neither verify nor clear, and its pending record blocked every later
publication into 0-personal from that host for three nights.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))


def _repo(tmp_path):
    repo = tmp_path / "0-personal"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "notes.md").write_text("one\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    return repo


def _try_transport_lock(repo, timeout):
    """What converge does, from ANOTHER process: take the repo lock, briefly."""
    probe = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {str(LIB)!r})
        from pathlib import Path
        from file_utils import file_lock, private_state_directory
        lock = private_state_directory('locks') / {repo.name + '.lock'!r}
        try:
            with file_lock(lock, lock_path=lock, timeout={timeout}):
                print('ACQUIRED')
        except Exception as exc:
            print('BLOCKED', type(exc).__name__)
    """)
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         timeout=60, env=os.environ.copy())
    return out.stdout.strip() or out.stderr.strip()


def test_the_transport_cannot_take_the_repo_while_a_publication_is_reserved(tmp_path):
    from publication_state import reserve
    repo = _repo(tmp_path)
    with reserve(repo, "main", ["notes.md"]):
        assert _try_transport_lock(repo, 0.5).startswith("BLOCKED")
    assert _try_transport_lock(repo, 5) == "ACQUIRED"


def _stranded_record(repo, content):
    """A reservation whose branch moved underneath it -- the 2026-09-16 shape."""
    import json
    from publication_state import Reservation
    r = Reservation(repo, "main", ["notes.md"])
    r.create()
    (repo / "notes.md").write_text(content)
    subprocess.run(["git", "add", "notes.md"], cwd=repo, check=True)
    tree = subprocess.run(["git", "write-tree"], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()
    r.expected(tree)
    subprocess.run(["git", "reset", "-q"], cwd=repo, check=True)
    return Path(json.loads(json.dumps(str(r.path))))


def test_reconcile_clears_a_record_whose_content_landed_elsewhere(tmp_path):
    import publication_reconcile as PR
    from publication_state import require_clear
    import pytest
    repo = _repo(tmp_path)
    record = _stranded_record(repo, "two\n")
    # The autosave commits the same content, moving the branch.
    subprocess.run(["git", "commit", "-q", "-am", "ledger: autosave before converge"], cwd=repo, check=True)
    with pytest.raises(RuntimeError):
        require_clear(repo)
    assert PR.reconcile(repo, apply=True) == 0
    assert not record.exists()
    require_clear(repo)  # publication into the repo is possible again


def test_reconcile_refuses_when_the_intended_content_never_landed(tmp_path):
    import publication_reconcile as PR
    repo = _repo(tmp_path)
    record = _stranded_record(repo, "two\n")
    (repo / "notes.md").write_text("something else entirely\n")
    subprocess.run(["git", "commit", "-q", "-am", "unrelated edit"], cwd=repo, check=True)
    assert PR.reconcile(repo, apply=True) == 1
    assert record.exists(), "an unproven record must be left for a human"
