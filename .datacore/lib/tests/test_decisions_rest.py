"""Owner decisions D2, D4 and D6 of the 2026-09-23 core verification.

D2  id_churn: no 25 % noise floor under a set (or no) baseline; any id newly
    churned since the acknowledged set is reported. A legacy COUNT baseline
    keeps its old behaviour, floor included, until it is re-acknowledged.
D4  actor_presence: `--acknowledge <actor>` accepts the current state of a
    MISSING/STALLED actor as the new baseline, recorded with who and when.
    Acknowledged-then-unchanged is ok; an unacknowledged failure stays sticky.
D6  context_merge: SPACE/TEAM layer content is not written into a composed
    file git would track in a repo with a public remote. Unknown = refuse.
"""
import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import types

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB)); sys.path.insert(0, str(LIB / "detectors"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AP = _load("ap_decisions", LIB / "detectors" / "actor_presence.py")
IC = _load("ic_decisions", LIB / "detectors" / "id_churn.py")
import context_merge  # noqa: E402


# ------------------------------------------------------------------ D2 id_churn

def _ic_space(tmp_path, monkeypatch, open_ids, org_ids, baseline=None):
    """A root with one space whose ledger folds to `open_ids` and whose org holds
    `org_ids`. The fold is faked: this is about the detector's arithmetic."""
    root = tmp_path / "root"
    sp = root / "0-a"
    (sp / "org").mkdir(parents=True)
    (sp / "org" / "next_actions.org").write_text(
        "".join(f"* TODO t\n:PROPERTIES:\n:ID: {i}\n:END:\n" for i in org_ids))
    import ledger.fold, ledger.log  # noqa: E401
    items = {i: types.SimpleNamespace(status="created") for i in open_ids}
    monkeypatch.setattr(ledger.fold, "fold", lambda evs: types.SimpleNamespace(items=items))
    monkeypatch.setattr(ledger.log, "read_events", lambda space: [])
    home = tmp_path / "home"
    if baseline is not None:
        p = home / ".datacore" / "state" / "id-churn.baseline.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(baseline))
    monkeypatch.setenv("HOME", str(home))
    return root, sp


def _ic_main(root, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ic", "--root", str(root), "--json"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = IC.main()
    out = buf.getvalue()
    return rc, json.loads(out[out.index("{"):])


def test_d2_scan_reports_churn_below_the_old_noise_floor(tmp_path, monkeypatch):
    open_ids = [f"id-{i}" for i in range(10)]
    _, sp = _ic_space(tmp_path, monkeypatch, open_ids, open_ids[1:])   # 1 in 10 lost
    r = IC.scan_space(sp)
    assert r is not None and r["orphaned_ids"] == ["id-0"], r


def test_d2_set_baseline_reports_one_new_churned_id(tmp_path, monkeypatch):
    open_ids = [f"id-{i}" for i in range(20)]
    root, _ = _ic_space(tmp_path, monkeypatch, open_ids, open_ids[2:],
                        baseline={"0-a": ["id-0"], "_acknowledged": "2026-09-23"})
    rc, out = _ic_main(root, monkeypatch)
    assert rc == 1
    assert out["findings"][0]["orphaned_ids"] == ["id-1"], out


def test_d2_set_baseline_exactly_acknowledged_is_clean(tmp_path, monkeypatch):
    open_ids = [f"id-{i}" for i in range(20)]
    root, _ = _ic_space(tmp_path, monkeypatch, open_ids, open_ids[1:],
                        baseline={"0-a": ["id-0"], "_acknowledged": "2026-09-23"})
    rc, out = _ic_main(root, monkeypatch)
    assert rc == 0 and out["findings"] == []


def test_d2_legacy_count_baseline_keeps_the_floor(tmp_path, monkeypatch):
    open_ids = [f"id-{i}" for i in range(10)]
    root, _ = _ic_space(tmp_path, monkeypatch, open_ids, open_ids[1:],
                        baseline={"0-a": 0, "9-z": 5, "_acknowledged": "2026-09-03"})
    rc, out = _ic_main(root, monkeypatch)
    assert rc == 0 and out["findings"] == [], "legacy: 10 % is below the floor, as before"


def test_d2_acknowledge_records_every_orphan_not_just_those_over_the_floor(tmp_path, monkeypatch):
    open_ids = [f"id-{i}" for i in range(10)]
    root, _ = _ic_space(tmp_path, monkeypatch, open_ids, open_ids[1:])
    monkeypatch.setattr(sys, "argv", ["ic", "--root", str(root), "--acknowledge"])
    with contextlib.redirect_stdout(io.StringIO()):
        assert IC.main() == 0
    base = json.loads((tmp_path / "home" / ".datacore" / "state" / "id-churn.baseline.json").read_text())
    assert base["0-a"] == ["id-0"]


# ------------------------------------------------------------------ D4 actor_presence

def _ev(seq):
    return json.dumps({"seq": seq, "hlc": f"{1_700_000_000_000 + seq}.0.x"}) + "\n"


def _ap_root(tmp_path):
    root = tmp_path / "root"
    (root / ".datacore" / "registry").mkdir(parents=True)
    (root / ".datacore" / "registry" / "infrastructure.yaml").write_text(
        "servers:\n  mac:\n    ledger_actors: [alice, bob]\n")
    for sp in ("0-a", "1-b"):
        (root / sp / ".datacore" / "events").mkdir(parents=True)
    return root


def _ap_run(root, state, monkeypatch, *extra):
    monkeypatch.setattr(AP, "STATE", state)
    monkeypatch.setattr(sys, "argv", ["ap", "--root", str(root), "--json", *extra])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = AP.main()
    out = buf.getvalue()
    if extra:
        return rc, out
    rows = json.loads(out)["rows"]
    return rc, {r["actor"]: r["status"] for r in rows}


def _logs(root, actor, seqs, spaces=("0-a",)):
    for sp in spaces:
        (root / sp / ".datacore" / "events" / f"{actor}.jsonl").write_text(
            "".join(_ev(i) for i in seqs))


def test_d4_acknowledged_stalled_actor_then_unchanged_is_ok(tmp_path, monkeypatch):
    root, state = _ap_root(tmp_path), tmp_path / "state.json"
    _logs(root, "alice", range(10)); _logs(root, "bob", range(4))
    assert _ap_run(root, state, monkeypatch)[1]["alice"] == "ok"
    _logs(root, "alice", range(4))                       # truncated 9 -> 3
    assert _ap_run(root, state, monkeypatch) == (1, {"alice": "stalled", "bob": "ok"})
    rc, out = _ap_run(root, state, monkeypatch, "--acknowledge", "alice")
    assert rc == 0, out
    entry = json.loads(state.read_text())["actors"]["alice"]
    ack = entry["acknowledged"]
    assert ack["status"] == "stalled" and ack["by"] and ack["at"]
    assert ack["previous"] == {"0-a": 9}
    assert entry["spaces"] == {"0-a": 3}
    assert _ap_run(root, state, monkeypatch) == (0, {"alice": "ok", "bob": "ok"})
    assert _ap_run(root, state, monkeypatch) == (0, {"alice": "ok", "bob": "ok"})


def test_d4_acknowledged_retired_actor_with_no_log_left_is_ok(tmp_path, monkeypatch):
    root, state = _ap_root(tmp_path), tmp_path / "state.json"
    _logs(root, "alice", range(5), spaces=("0-a", "1-b")); _logs(root, "bob", range(4))
    _ap_run(root, state, monkeypatch)
    for sp in ("0-a", "1-b"):
        (root / sp / ".datacore" / "events" / "alice.jsonl").unlink()
    assert _ap_run(root, state, monkeypatch)[1]["alice"] == "missing"
    assert _ap_run(root, state, monkeypatch, "--acknowledge", "alice")[0] == 0
    assert _ap_run(root, state, monkeypatch) == (0, {"alice": "ok", "bob": "ok"})
    # A later log is held to the baseline again from where it restarts.
    _logs(root, "alice", range(2))
    assert _ap_run(root, state, monkeypatch)[1]["alice"] == "ok"
    (root / "0-a" / ".datacore" / "events" / "alice.jsonl").unlink()
    assert _ap_run(root, state, monkeypatch)[1]["alice"] == "missing"


def test_d4_unacknowledged_failure_stays_sticky(tmp_path, monkeypatch):
    root, state = _ap_root(tmp_path), tmp_path / "state.json"
    _logs(root, "alice", range(10)); _logs(root, "bob", range(10))
    _ap_run(root, state, monkeypatch)
    _logs(root, "alice", range(4)); _logs(root, "bob", range(4))
    _ap_run(root, state, monkeypatch)
    _ap_run(root, state, monkeypatch, "--acknowledge", "alice")
    for _ in range(2):
        rc, st = _ap_run(root, state, monkeypatch)
        assert rc == 1 and st == {"alice": "ok", "bob": "stalled"}, "acking alice clears only alice"


def test_d4_acknowledge_refuses_a_healthy_or_unknown_actor(tmp_path, monkeypatch):
    root, state = _ap_root(tmp_path), tmp_path / "state.json"
    _logs(root, "alice", range(10)); _logs(root, "bob", range(4))
    _ap_run(root, state, monkeypatch)
    before = state.read_text()
    rc, out = _ap_run(root, state, monkeypatch, "--acknowledge", "alice")
    assert rc == 2 and "not MISSING or STALLED" in out
    assert state.read_text() == before
    rc, out = _ap_run(root, state, monkeypatch, "--acknowledge", "carol")
    assert rc == 2 and "not rostered" in out
    assert state.read_text() == before


def test_d4_classify_acknowledged_empty_baseline_model():
    c = AP.classify
    assert c({}, {}, acknowledged=True) == ("ok", [])
    assert c({}, {}) == ("missing", [])                  # never without the flag
    assert c({"0-a": None}, {"0-a": 4}, acknowledged=True)[0] == "missing"


# ------------------------------------------------------------------ D6 context_merge

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _space_repo(tmp_path, monkeypatch, remote=None, protected=("acme/public-thing",),
                denylist=True):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    if remote:
        _git(repo, "remote", "add", "origin", remote)
    (repo / "CLAUDE.base.md").write_text("# Base\n")
    (repo / "CLAUDE.space.md").write_text("# Space\nteam contact list\n")
    dl = tmp_path / "public-repo-denylist.yaml"
    if denylist:
        dl.write_text("protected_repos:\n" + "".join(f"  - {p}\n" for p in protected))
    monkeypatch.setattr(context_merge, "PUBLIC_REPO_DENYLIST", dl)
    return repo


def test_d6_space_layer_refused_in_a_tracked_file_of_a_public_repo(tmp_path, monkeypatch):
    repo = _space_repo(tmp_path, monkeypatch, remote="git@github.com:acme/public-thing.git")
    ok, warnings = context_merge.rebuild_context(repo)
    assert ok is False and not (repo / "CLAUDE.md").exists()
    assert any("public" in w.lower() for w in warnings), warnings


def test_d6_https_remote_form_is_recognised(tmp_path, monkeypatch):
    repo = _space_repo(tmp_path, monkeypatch, remote="https://github.com/acme/public-thing")
    assert context_merge.rebuild_context(repo)[0] is False


def test_d6_unknown_visibility_is_refused(tmp_path, monkeypatch):
    repo = _space_repo(tmp_path, monkeypatch, remote="git@github.com:acme/x.git", denylist=False)
    ok, warnings = context_merge.rebuild_context(repo)
    assert ok is False and not (repo / "CLAUDE.md").exists()
    assert any("cannot tell" in w for w in warnings), warnings


def test_d6_private_remote_or_no_remote_is_written(tmp_path, monkeypatch):
    repo = _space_repo(tmp_path, monkeypatch, remote="git@github.com:acme/private-thing.git")
    assert context_merge.rebuild_context(repo) == (True, [])
    assert "team contact list" in (repo / "CLAUDE.md").read_text()


def test_d6_ignored_output_in_a_public_repo_is_written(tmp_path, monkeypatch):
    repo = _space_repo(tmp_path, monkeypatch, remote="git@github.com:acme/public-thing.git")
    (repo / ".gitignore").write_text("CLAUDE.md\n")
    assert context_merge.rebuild_context(repo) == (True, [])


def test_d6_any_public_remote_counts(tmp_path, monkeypatch):
    repo = _space_repo(tmp_path, monkeypatch, remote="git@github.com:acme/private-thing.git")
    _git(repo, "remote", "add", "upstream", "https://github.com/acme/public-thing.git")
    assert context_merge.rebuild_context(repo)[0] is False


def test_d6_base_only_is_not_subject_to_the_guard(tmp_path, monkeypatch):
    repo = _space_repo(tmp_path, monkeypatch, remote="git@github.com:acme/public-thing.git")
    (repo / "CLAUDE.space.md").unlink()
    assert context_merge.rebuild_context(repo) == (True, [])
