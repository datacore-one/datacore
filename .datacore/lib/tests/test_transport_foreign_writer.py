"""A host never commits another principal's writer log (DIP-0044).

2026-09-06/07: a cadence on hermes appended geo tasks to 5-plur's
`winston.jsonl`; the transport's autosave committed the file under Tris's
name and the verifier flagged "5-plur/winston by tris" the next morning.
The autosave now unstages a log that a different declared principal owns,
commits the rest, and stops with the file named."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
import ledger_transport as lt  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout


@pytest.fixture()
def repo_pair(tmp_path: Path, monkeypatch):
    """A clone with a real origin, registered so the transport will act on it
    (the same shape test_ledger_transport.py uses)."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    git(work, "config", "user.email", "t@t")
    git(work, "config", "user.name", "t")
    git(work, "config", "core.hooksPath", str(work / ".git" / "hooks"))
    (work / "seed.txt").write_text("seed\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "seed")
    git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(work, "branch", "-M", "main")
    git(work, "branch", "--set-upstream-to=origin/main", "main")
    monkeypatch.setattr(lt, "classify",
                        lambda space, root=None: lt.Result(True, "knowledge", {"entry": {}}))
    return work

PRINCIPALS = {"winston": "winston", "bridge": "winston", "tris": "tris", "mac": "gregor"}


def _hermes(monkeypatch):
    """This host writes as tris; the registry knows winston, bridge, tris, mac."""
    monkeypatch.setattr(lt, "_own_principal", lambda: ("tris", "tris"))
    monkeypatch.setattr(lt, "_principal_of", lambda w: PRINCIPALS.get(w))


def _dirty_log(space: Path, writer: str) -> Path:
    p = space / ".datacore" / "events" / f"{writer}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write('{"actor":"%s","type":"item.create"}\n' % writer)
    return p


def _committed(space: Path, rel: str) -> bool:
    return subprocess.run(["git", "-C", str(space), "ls-files", "--error-unmatch", rel],
                          capture_output=True).returncode == 0


def test_foreign_writer_log_is_unstaged_named_and_stops_the_converge(repo_pair, monkeypatch):
    _hermes(monkeypatch)
    _dirty_log(repo_pair, "tris")
    foreign = _dirty_log(repo_pair, "winston")
    res = lt.converge(repo_pair)
    assert not res.ok
    assert res.reason.startswith("foreign writer log left uncommitted: .datacore/events/winston.jsonl belongs to winston")
    assert "this host writes as tris for tris" in res.reason
    assert res.context["foreign"] == [".datacore/events/winston.jsonl"]
    # Tris's own log was autosaved; winston's stayed in the working tree, uncommitted.
    assert _committed(repo_pair, ".datacore/events/tris.jsonl")
    assert not _committed(repo_pair, ".datacore/events/winston.jsonl")
    assert foreign.exists()
    status = git(repo_pair, "status", "--porcelain")
    assert "?? .datacore/events/winston.jsonl" in status


def test_own_and_undeclared_writer_logs_still_autosave(repo_pair, monkeypatch):
    _hermes(monkeypatch)
    _dirty_log(repo_pair, "tris")
    _dirty_log(repo_pair, "transporter")      # hostname-derived, declared by nobody
    res = lt.converge(repo_pair)
    assert res.ok, res.reason
    assert _committed(repo_pair, ".datacore/events/tris.jsonl")
    assert _committed(repo_pair, ".datacore/events/transporter.jsonl")


def test_a_writes_as_alias_is_not_foreign_to_its_principal(repo_pair, monkeypatch):
    monkeypatch.setattr(lt, "_own_principal", lambda: ("winston", "winston"))
    monkeypatch.setattr(lt, "_principal_of", lambda w: PRINCIPALS.get(w))
    _dirty_log(repo_pair, "bridge")           # winston's pre-DIP-0044 log
    res = lt.converge(repo_pair)
    assert res.ok, res.reason
    assert _committed(repo_pair, ".datacore/events/bridge.jsonl")


def test_unresolved_identity_refuses_nothing(repo_pair, monkeypatch):
    monkeypatch.setattr(lt, "_own_principal", lambda: (None, ""))
    monkeypatch.setattr(lt, "_principal_of", lambda w: PRINCIPALS.get(w))
    _dirty_log(repo_pair, "winston")
    assert lt.foreign_writer_logs(repo_pair) == []


def test_writer_log_pattern_matches_only_event_logs():
    m = lt._WRITER_LOG.search
    assert m(".datacore/events/winston.jsonl").group(1) == "winston"
    assert m("5-plur/.datacore/events/tris.jsonl").group(1) == "tris"
    assert m("org/next_actions.org") is None
    assert m(".datacore/events/winston.jsonl.bak") is None
    assert m("notes/.datacore/events.jsonl") is None


# ── registry fallback ───────────────────────────────────────────────────────

def test_registry_falls_back_to_the_shipped_copy(tmp_path):
    """hermes: `--root ~/Data` is a directory of space clones with no
    `.datacore/registry`; the registry that ships with the code tree applies."""
    assert not (tmp_path / ".datacore" / "registry" / "repositories.yaml").exists()
    reg = lt._registry(tmp_path)
    assert isinstance(reg, dict) and reg, "the shipped registry is non-empty"
    assert "<root>" in reg or any(k[0].isdigit() for k in reg)


def test_root_copy_wins_over_the_shipped_one(tmp_path):
    reg = tmp_path / ".datacore" / "registry"
    reg.mkdir(parents=True)
    (reg / "repositories.yaml").write_text("repositories:\n  9-only:\n    category: knowledge\n")
    assert lt._registry(tmp_path) == {"9-only": {"category": "knowledge"}}


def test_sync_all_says_when_nothing_registered_is_present(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(lt, "_registry", lambda root: {"5-plur": {"category": "knowledge"}})
    assert lt.sync_all(tmp_path) == 0
    out = capsys.readouterr().out
    assert "no registered repository is checked out under" in out
    assert "1 registered" in out
