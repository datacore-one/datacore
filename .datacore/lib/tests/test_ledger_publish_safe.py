"""ledger_publish_safe publishes machine-written ledger paths and nothing else."""
import importlib.util, pathlib, subprocess

ROOT = pathlib.Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("lps", ROOT / ".datacore" / "lib" / "ledger_publish_safe.py")
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)


def _fleet(tmp_path):
    origin = tmp_path / "origin.git"; _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    root = tmp_path / "root"; root.mkdir()
    space = root / "1-space"; _git(tmp_path, "clone", "-q", str(origin), str(space))
    _git(space, "config", "user.email", "t@t"); _git(space, "config", "user.name", "t")
    (space / ".datacore" / "events").mkdir(parents=True); (space / "org").mkdir()
    (space / ".datacore" / "events" / "mac.jsonl").write_text('{"seq":1}\n')
    (space / "org" / "next_actions.org").write_text("* Tasks\n")
    _git(space, "add", "-A"); _git(space, "commit", "-q", "-m", "base"); _git(space, "push", "-q", "-u", "origin", "main")
    return root, space, origin


def test_only_machine_written_paths_qualify():
    assert L.only_machine_written([".datacore/events/mac.jsonl"])
    assert not L.only_machine_written([".datacore/events/mac.jsonl", "org/next_actions.org"])
    assert not L.only_machine_written([])
    assert L.split([".datacore/events/mac.jsonl", "org/next_actions.org"]) == ([".datacore/events/mac.jsonl"], ["org/next_actions.org"])


def test_machine_files_publish_while_human_work_stays_uncommitted(tmp_path, monkeypatch):
    """The 2026-09-04 shape: 0-personal and 1-datafund always have a human file
    dirty on the workstation, so 'skip the whole space' meant 'never publish'."""
    root, space, origin = _fleet(tmp_path); monkeypatch.setattr(L, "ROOT", root)
    (space / ".datacore" / "events" / "mac.jsonl").write_text('{"seq":1}\n{"seq":2}\n')
    (space / "org" / "next_actions.org").write_text("* Tasks\n** half-typed\n")
    assert L.main([]) == 0
    dirty = _git(space, "status", "--porcelain").stdout
    assert "org/next_actions.org" in dirty and "events" not in dirty, "human file untouched, ledger committed"
    assert "half-typed" in (space / "org" / "next_actions.org").read_text()
    assert '"seq":2' in _git(origin, "show", "main:.datacore/events/mac.jsonl").stdout, "the ledger reached origin"
    assert "half-typed" not in _git(origin, "show", "main:org/next_actions.org").stdout


def test_nothing_machine_written_means_nothing_happens(tmp_path, monkeypatch):
    root, space, origin = _fleet(tmp_path); monkeypatch.setattr(L, "ROOT", root)
    (space / "org" / "next_actions.org").write_text("* Tasks\n** only human\n")
    before = _git(space, "rev-parse", "HEAD").stdout
    assert L.main([]) == 0
    assert _git(space, "rev-parse", "HEAD").stdout == before


def test_publish_brings_upstream_in_when_behind(tmp_path, monkeypatch):
    root, space, origin = _fleet(tmp_path); monkeypatch.setattr(L, "ROOT", root)
    other = tmp_path / "other"; _git(tmp_path, "clone", "-q", str(origin), str(other))
    _git(other, "config", "user.email", "o@o"); _git(other, "config", "user.name", "o")
    (other / "README.md").write_text("upstream\n"); _git(other, "add", "-A"); _git(other, "commit", "-q", "-m", "upstream"); _git(other, "push", "-q", "origin", "main")
    (space / ".datacore" / "events" / "mac.jsonl").write_text('{"seq":1}\n{"seq":2}\n')
    assert L.main([]) == 0
    assert (space / "README.md").exists(), "upstream merged before the push"
    assert '"seq":2' in _git(origin, "show", "main:.datacore/events/mac.jsonl").stdout


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    root, space, origin = _fleet(tmp_path); monkeypatch.setattr(L, "ROOT", root)
    (space / ".datacore" / "events" / "mac.jsonl").write_text('{"seq":1}\n{"seq":2}\n')
    before = _git(space, "rev-parse", "HEAD").stdout
    assert L.main(["--dry-run"]) == 0
    assert _git(space, "rev-parse", "HEAD").stdout == before
