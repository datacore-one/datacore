"""CAD-4: Nobody can make a duty look done by hand. Edited logs, copied records
or unsigned entries never turn it green.

Kind: deterministic, with REAL ed25519 signatures. A tmp Data root, git-backed
spaces, a tmp signing key for tris registered as its proven key; the real
liveness judge (cadence_liveness.collect_states / scheduled_state).

Seeded failure: 2026-09-25, an agent hand-wrote two cadence.run "ok" records in
6-meridian with sig = the event's own hash (audit D2); Miles's duties are
judged from agent-editable cadence-log files (EXECUTING={"miles"}). Verified by
making _sig_ok accept any non-empty sig.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cl_cad4", ROOT / ".datacore" / "lib" / "cadence_liveness.py")
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)

import ledger.keys as K  # noqa: E402
from ledger.log import EventLog  # noqa: E402
from ledger.events import body_dict, compute_hash  # noqa: E402

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
SLUG = "cadence-plur-cio-geo-research"
OTHER = "cadence-plur-cio-geo-sov-scan"


@pytest.fixture
def world(tmp_path, monkeypatch):
    sp = tmp_path / "5-plur"; (sp / "drafts").mkdir(parents=True)
    (sp / "venture.yaml").write_text(yaml.safe_dump({"name": "plur", "stage": "growth", "nightshift": {"enabled": True}, "roles": {
        "cio": {"agent": "tris", "cadences": {"daily": ["geo-research", "geo-sov-scan"]}},
        "coo": {"agent": "miles", "cadences": {"daily": ["state-backup"]}}}}))
    subprocess.run([*GIT, "init", "-q", str(sp)], check=True)
    subprocess.run([*GIT, "-C", str(sp), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    keys, reg = tmp_path / "keys", tmp_path / "registry.yaml"
    pub = K.ensure_keypair("tris", keys_dir=keys, registry_path=reg)
    monkeypatch.setattr(K, "principals_verify_key", lambda actor: pub if actor == "tris" else None)
    monkeypatch.setattr(K, "DEFAULT_REGISTRY_PATH", reg)
    log = EventLog(sp, "tris", keys_dir=keys, registry_path=reg, sign=True)
    # Registered three days ago (a genuine, signed record with an old clock), so a duty
    # with no fresh run is late now.
    import ledger.hlc as H
    import time as _t
    with monkeypatch.context() as m:
        m.setattr(H, "time", type("Clock", (), {"time": staticmethod(lambda: _t.time() - 3 * 86400)}))
        log.append("metric.attest", {"metric": "cadence.registration", "slugs": {SLUG: "46 5 * * *", OTHER: "50 5 * * *"}})
    return tmp_path, sp, log


def _commit(sp, rel, text):
    (sp / rel).write_text(text)
    subprocess.run([*GIT, "-C", str(sp), "add", rel], check=True)
    subprocess.run([*GIT, "-C", str(sp), "commit", "-q", "-m", rel], check=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _state(sp, name="geo-research"):
    st = L.scheduled_state(sp, "plur", "tris", "cio", "daily", name, datetime.now(timezone.utc).date())
    return st[0] if st else None


def _lines(sp):
    return (sp / ".datacore" / "events" / "tris.jsonl").read_text().splitlines()


def _append_raw(sp, obj):
    with (sp / ".datacore" / "events" / "tris.jsonl").open("a") as fh:
        fh.write(json.dumps(obj) + "\n")


def test_control_nothing_ran_is_red_and_a_genuine_run_is_green(world):
    _root, sp, log = world
    assert _state(sp) == "red"
    sha = _commit(sp, "drafts/a.md", "# a real draft\n")
    log.append("metric.attest", {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "ok",
                                 "artifact": "drafts/a.md", "sha256": sha})
    assert _state(sp) == "green"


def test_a_hand_written_ok_record_does_not_count(world):
    _root, sp, _log = world
    sha = _commit(sp, "drafts/a.md", "# a real draft\n")
    last = json.loads(_lines(sp)[-1])
    import time as _t
    body = body_dict(last["seq"] + 1, f"{int(_t.time() * 1000) - 3600_000}.0000.tris", "tris", "metric.attest",
                     {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "ok",
                      "artifact": "drafts/a.md", "sha256": sha}, last["hash"])
    h = compute_hash(body)
    _append_raw(sp, {**body, "hash": h, "sig": h})          # the 2026-09-25 forgery: sig copied from hash
    _append_raw(sp, {**body, "seq": last["seq"] + 2, "hash": h, "sig": ""})   # and simply unsigned
    assert _state(sp) == "red"


def test_a_copied_record_does_not_count_for_another_duty(world):
    _root, sp, log = world
    sha = _commit(sp, "drafts/a.md", "# a real draft\n")
    log.append("metric.attest", {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "ok",
                                 "artifact": "drafts/a.md", "sha256": sha})
    genuine = json.loads(_lines(sp)[-1])
    forged = copy.deepcopy(genuine)
    forged["payload"]["slug"] = OTHER                        # same signature, another duty
    _append_raw(sp, forged)
    assert _state(sp, "geo-sov-scan") == "red"


def test_an_edited_record_does_not_count(world):
    _root, sp, log = world
    sha = _commit(sp, "drafts/a.md", "# a real draft\n")
    log.append("metric.attest", {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "failed",
                                 "reason": "no file"})
    lines = _lines(sp)
    ev = json.loads(lines[-1])
    ev["payload"].update(result="ok", artifact="drafts/a.md", sha256=sha)   # failed -> ok, by hand
    (sp / ".datacore" / "events" / "tris.jsonl").write_text("\n".join(lines[:-1] + [json.dumps(ev)]) + "\n")
    assert _state(sp) == "red"


def test_a_hand_edited_cadence_log_does_not_turn_a_miles_duty_green(world):
    root, sp, _log = world

    def red():
        rows, _ = L.collect_states(root, grace=0, today=datetime.now(timezone.utc).date())
        return [r for r in rows if "state-backup" in str(r[4])]
    assert red(), "control: a Miles duty that never ran is red"
    p = sp / ".datacore" / "state" / "venture" / "cadence-log.yaml"; p.parent.mkdir(parents=True)
    p.write_text(yaml.safe_dump({"coo": {"daily": {"state-backup": {
        "last_run": datetime.now(timezone.utc).date().isoformat(), "result": "ok"}}}}))
    assert red(), "one hand-typed line in cadence-log.yaml turned Miles's duty green"
