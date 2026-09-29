"""plur_store_sync: a machine's PLUR store converges with the shared remote even
when both sides edited engrams.yaml since they last met.

`plur sync` merges the 16 MB engrams.yaml line by line; two machines that both
wrote engrams since they last met conflict, the sync aborts, and that machine
never receives memory again (box stopped 2026-08-10, nightshift 2026-07-29).
plur_store_sync merges by engram id instead, so a correction learned on one
machine reaches the others (SPC-7).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

import pytest
import yaml

import plur_store_sync as pss


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _eng(i, **kw):
    return {"id": f"ENG-2026-09-01-{i:03d}", "statement": f"statement {i}", "scope": "global",
            "status": "active", "activation": {"retrieval_strength": 0.5}, **kw}


def _write(store, name, data):
    p = store / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False))


def _read(path):
    return yaml.safe_load(Path(path).read_text())


def _ids(lst):
    return [e["id"] for e in lst]


@pytest.fixture
def fleet(tmp_path):
    """A bare remote, the mac's store and a host's store, both at the seed."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    mac = tmp_path / "mac"
    mac.mkdir()
    _git(mac, "init", "-b", "main")
    for repo in (mac,):
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "t")
    _write(mac, "engrams.yaml", [_eng(i) for i in range(1, 6)])
    _write(mac, "episodes.yaml", [{"id": "EP-1", "summary": "one"}])
    _write(mac, "packs/p/engrams.yaml", {"name": "p", "engrams": [_eng(90)]})
    (mac / ".gitignore").write_text("*.db\n")
    _git(mac, "add", "-A")
    _git(mac, "commit", "-m", "seed")
    _git(mac, "remote", "add", "origin", str(remote))
    _git(mac, "push", "-u", "origin", "main")
    host = tmp_path / "host"
    subprocess.run(["git", "clone", str(remote), str(host)], check=True, capture_output=True)
    _git(host, "config", "user.email", "t@example.com")
    _git(host, "config", "user.name", "t")
    return remote, mac, host


def _run(store, tmp_path, name="state.json"):
    state = tmp_path / name
    rc = pss.main(["--store", str(store), "--state", str(state), "--no-index"])
    return rc, json.loads(state.read_text())


def test_diverged_stores_converge_and_keep_both_sides(fleet, tmp_path):
    remote, mac, host = fleet
    # The mac corrects an engram (supersedes it) and learns a new one.
    m = _read(mac / "engrams.yaml")
    m[1]["status"] = "retired"
    m.append(_eng(10, statement="deploy with deploy.sh"))
    _write(mac, "engrams.yaml", m)
    _git(mac, "commit", "-am", "mac learns")
    _git(mac, "push")
    # The host, meanwhile, committed its own learning and holds an uncommitted one
    # plus a scope:local engram that must never be pushed.
    h = _read(host / "engrams.yaml")
    h.append(_eng(20, statement="box learned this"))
    _write(host, "engrams.yaml", h)
    _git(host, "commit", "-am", "host learns")
    h.append(_eng(21, statement="uncommitted on the host"))
    h.append(_eng(22, statement="host-only secret", scope="local"))
    h[3]["activation"] = {"retrieval_strength": 0.9}
    _write(host, "engrams.yaml", h)
    # A plain git merge of these two edits conflicts -- the failure being fixed.
    merge = subprocess.run(["git", "-C", str(host), "fetch"], capture_output=True)
    assert merge.returncode == 0

    rc, state = _run(host, tmp_path)
    assert rc == 0, state
    assert state["in_sync"] is True
    remote_head = _git(remote, "rev-parse", "main")
    assert _git(host, "rev-parse", "HEAD") == remote_head, "the host's merge was not pushed"
    mac_head = _git(mac, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(host), "merge-base", "--is-ancestor", mac_head, "HEAD"], check=True)

    work = {e["id"]: e for e in _read(host / "engrams.yaml")}
    assert work["ENG-2026-09-01-002"]["status"] == "retired", "the mac's correction did not arrive"
    assert "ENG-2026-09-01-010" in work and "ENG-2026-09-01-020" in work and "ENG-2026-09-01-021" in work
    assert work["ENG-2026-09-01-004"]["activation"]["retrieval_strength"] == 0.9
    assert "ENG-2026-09-01-022" in work, "the host's scope:local engram was lost from its own store"
    pushed = yaml.safe_load(_git(remote, "show", "main:engrams.yaml"))
    assert "ENG-2026-09-01-022" not in _ids(pushed), "a scope:local engram was pushed"
    assert {"ENG-2026-09-01-020", "ENG-2026-09-01-021", "ENG-2026-09-01-010"} <= set(_ids(pushed))

    # The mac's next run picks up the host's learning by fast-forward.
    rc, state = _run(mac, tmp_path, "mac.json")
    assert rc == 0 and state["in_sync"] is True
    assert "ENG-2026-09-01-020" in _ids(_read(mac / "engrams.yaml"))


def test_three_way_rules_per_field(fleet, tmp_path):
    remote, mac, host = fleet
    m = _read(mac / "engrams.yaml")
    m[0]["statement"] = "mac edit"          # same field changed on both sides: remote wins
    m[1]["status"] = "retired"              # only remote changed this field
    m = [e for e in m if e["id"] != "ENG-2026-09-01-005"]  # remote deleted, host unchanged
    _write(mac, "engrams.yaml", m)
    _git(mac, "commit", "-am", "mac")
    _git(mac, "push")
    h = _read(host / "engrams.yaml")
    h[0]["statement"] = "host edit"
    h[1]["tags"] = ["host-tag"]             # different field on the same engram: both kept
    h[2]["statement"] = "host only edit"    # only the host changed it
    _write(host, "engrams.yaml", h)

    rc, state = _run(host, tmp_path)
    assert rc == 0, state
    work = {e["id"]: e for e in _read(host / "engrams.yaml")}
    assert work["ENG-2026-09-01-001"]["statement"] == "mac edit"
    assert work["ENG-2026-09-01-002"]["status"] == "retired"
    assert work["ENG-2026-09-01-002"]["tags"] == ["host-tag"]
    assert work["ENG-2026-09-01-003"]["statement"] == "host only edit"
    assert "ENG-2026-09-01-005" not in work, "an engram the remote deleted came back"


def test_packs_episodes_and_gitignore_merge(fleet, tmp_path):
    remote, mac, host = fleet
    _write(mac, "episodes.yaml", [{"id": "EP-1", "summary": "one"}, {"id": "EP-2", "summary": "mac"}])
    _write(mac, "packs/p/engrams.yaml", {"name": "p", "engrams": [_eng(90), _eng(91)]})
    (mac / ".gitignore").write_text("*.db\nstore.pglite/\n")
    _git(mac, "commit", "-am", "mac")
    _git(mac, "push")
    _write(host, "episodes.yaml", [{"id": "EP-1", "summary": "one"}, {"id": "EP-3", "summary": "host"}])
    _write(host, "packs/p/engrams.yaml", {"name": "p", "engrams": [_eng(90), _eng(92)]})
    (host / ".gitignore").write_text("*.db\nbackups/\n")
    _git(host, "commit", "-am", "host")

    rc, state = _run(host, tmp_path)
    assert rc == 0, state
    assert _ids(_read(host / "episodes.yaml")) == ["EP-1", "EP-2", "EP-3"]
    assert _ids(_read(host / "packs/p/engrams.yaml")["engrams"]) == [
        "ENG-2026-09-01-090", "ENG-2026-09-01-091", "ENG-2026-09-01-092"]
    assert set((host / ".gitignore").read_text().split()) == {"*.db", "store.pglite/", "backups/"}
    assert "<<<<<<<" not in _git(remote, "show", "main:.gitignore")


def test_only_behind_fast_forwards_without_a_merge_commit(fleet, tmp_path):
    remote, mac, host = fleet
    m = _read(mac / "engrams.yaml")
    m.append(_eng(10))
    _write(mac, "engrams.yaml", m)
    _git(mac, "commit", "-am", "mac")
    _git(mac, "push")
    rc, state = _run(host, tmp_path)
    assert rc == 0 and state["in_sync"] is True
    assert _git(host, "rev-parse", "HEAD") == _git(mac, "rev-parse", "HEAD")
    assert _git(host, "status", "--porcelain") == ""


def test_a_live_plur_writer_holding_the_lock_is_not_overwritten(fleet, tmp_path, monkeypatch):
    remote, mac, host = fleet
    m = _read(mac / "engrams.yaml")
    m.append(_eng(10))
    _write(mac, "engrams.yaml", m)
    _git(mac, "commit", "-am", "mac")
    _git(mac, "push")
    before = (host / "engrams.yaml").read_text()
    (host / "engrams.yaml.lock").write_text(f"{socket.gethostname()}:{os.getpid()}:1:0")
    monkeypatch.setattr(pss, "LOCK_WAIT_S", 0.3)
    rc, state = _run(host, tmp_path)
    assert rc != 0 and state["in_sync"] is False and "lock" in state["error"]
    assert (host / "engrams.yaml").read_text() == before
    assert (host / "engrams.yaml.lock").exists(), "the live writer's lock was stolen"


def test_a_shared_remote_is_refused_not_pushed_as_personal(fleet, tmp_path):
    remote, mac, host = fleet
    (host / "config.yaml").write_text("sync:\n  remote_type: shared\n")
    rc, state = _run(host, tmp_path)
    assert rc != 0 and "shared" in state["error"]


def test_state_names_no_engram_content(fleet, tmp_path):
    remote, mac, host = fleet
    rc, state = _run(host, tmp_path)
    assert rc == 0
    text = json.dumps(state)
    assert "statement" not in text
    assert set(state) >= {"ts", "host", "head", "remote_head", "in_sync", "engrams"}


def test_a_file_only_this_machine_changed_is_committed_byte_for_byte(fleet, tmp_path):
    remote, mac, host = fleet
    m = _read(mac / "engrams.yaml")
    m.append(_eng(10))
    _write(mac, "engrams.yaml", m)
    _git(mac, "commit", "-am", "mac")
    _git(mac, "push")
    # The host rewrote its pack in PLUR's own formatting and added a record.
    text = json.dumps({"name": "p", "engrams": [_eng(90), _eng(93)]}, indent=1) + "\n"
    (host / "packs/p/engrams.yaml").write_text(text)
    rc, state = _run(host, tmp_path)
    assert rc == 0, state
    assert _git(remote, "show", "main:packs/p/engrams.yaml") + "\n" == text
    assert "packs/p/engrams.yaml" not in _git(host, "status", "--porcelain")


def test_one_id_minted_on_two_machines_for_different_engrams_keeps_both(fleet, tmp_path, monkeypatch):
    """PLUR ids are a per-machine date sequence, so two machines that learned on
    the same day mint the same id for different engrams. The remote's keeps the
    id; this machine's is renamed and every reference on this side follows it.
    Found 2026-09-29: the first merge blended 1,633 such pairs."""
    remote, mac, host = fleet
    monkeypatch.setattr(pss.socket, "gethostname", lambda: "Night-Shift.local")
    cid = "ENG-2026-09-02-001"
    m = _read(mac / "engrams.yaml")
    m.append(_eng(0, id=cid, statement="the mac's engram", content_hash="aaa"))
    m.append(_eng(40, associations=[{"target": cid}]))
    _write(mac, "engrams.yaml", m)
    _git(mac, "commit", "-am", "mac")
    _git(mac, "push")
    h = _read(host / "engrams.yaml")
    h.append(_eng(0, id=cid, statement="the host's engram", content_hash="bbb"))
    h.append(_eng(41, associations=[{"target": cid}], rationale=f"see {cid}"))
    # The same engram reached both machines under one id: it is one engram.
    same = _eng(50, content_hash="ccc")
    h.append(same)
    _write(host, "engrams.yaml", h)
    _write(host, "episodes.yaml", [{"id": "EP-1", "summary": "one"},
                                   {"id": "EP-9", "summary": f"learned {cid}"}])
    m.append(same)
    _write(mac, "engrams.yaml", m)
    _git(mac, "commit", "-am", "mac2")
    _git(mac, "push")

    rc, state = _run(host, tmp_path)
    assert rc == 0, state
    assert state["renamed"] == 1
    new = f"{cid}-nightshift"
    for doc in (_read(host / "engrams.yaml"), yaml.safe_load(_git(remote, "show", "main:engrams.yaml"))):
        by = {e["id"]: e for e in doc}
        assert by[cid]["statement"] == "the mac's engram"
        assert by[new]["statement"] == "the host's engram", "the host's engram was lost or blended"
        assert by["ENG-2026-09-01-040"]["associations"] == [{"target": cid}], "the mac's reference moved"
        assert by["ENG-2026-09-01-041"]["associations"] == [{"target": new}]
        assert by["ENG-2026-09-01-041"]["rationale"] == f"see {new}"
        assert [e["id"] for e in doc].count("ENG-2026-09-01-050") == 1
    eps = {e["id"]: e for e in _read(host / "episodes.yaml")}
    assert eps["EP-9"]["summary"] == f"learned {new}"
