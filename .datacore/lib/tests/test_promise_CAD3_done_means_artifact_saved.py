"""CAD-3: A duty counts as done only when the report or file it promised exists
and is saved. The agent saying "done" is not enough.

Kind: deterministic. The real runner judgement (ventures cadence_run.main with a
fake agent runtime) and the real liveness judge (cadence_liveness.collect_states)
against a tmp Data root with git-backed spaces.

Seeded failure: the judge takes the agent's word -- a run record claiming "ok"
whose artifact is not in git, a reply saying "done" with no file, or (Miles's
path, EXECUTING={"miles"}) a cadence-log entry saying the duty ran. Verified by
making _artifact_in_git answer True and evidence() accept any reply.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cl_cad3", ROOT / ".datacore" / "lib" / "cadence_liveness.py")
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)   # puts ventures/lib on sys.path
import cadence_run as R  # noqa: E402

DAY = 86_400_000
GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
TRIS_SLUG = "cadence-plur-cio-geo-research"


def _data_root(tmp_path):
    sp = tmp_path / "5-plur"; (sp / "drafts").mkdir(parents=True)
    (sp / "venture.yaml").write_text(yaml.safe_dump({"name": "plur", "stage": "growth", "nightshift": {"enabled": True}, "roles": {
        "cio": {"agent": "tris", "cadences": {"daily": ["geo-research"]}},
        "coo": {"agent": "miles", "cadences": {"daily": ["state-backup"]}}}}))
    subprocess.run([*GIT, "init", "-q", str(sp)], check=True)
    subprocess.run([*GIT, "-C", str(sp), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return tmp_path, sp


def _commit(sp, rel, text):
    (sp / rel).write_text(text)
    subprocess.run([*GIT, "-C", str(sp), "add", rel], check=True)
    subprocess.run([*GIT, "-C", str(sp), "commit", "-q", "-m", rel], check=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _events(sp, actor, records):
    d = sp / ".datacore" / "events"; d.mkdir(parents=True, exist_ok=True)
    with (d / f"{actor}.jsonl").open("w") as fh:
        for i, (age_ms, payload) in enumerate(records):
            fh.write(json.dumps({"seq": i, "hlc": f"{int(time.time() * 1000) - age_ms}.0000.{actor}", "actor": actor,
                                 "type": "metric.attest", "payload": payload, "prev": "", "hash": "h", "sig": "s"}) + "\n")


REG = {"metric": "cadence.registration", "slugs": {TRIS_SLUG: "46 5 * * *"}}


@pytest.fixture(autouse=True)
def _placeholder_signatures(monkeypatch):
    # Signature verification is CAD-4's subject; here "s" stands for a valid signature.
    monkeypatch.setattr(L, "_sig_ok", lambda e: e.sig == "s")


def _red(root, what):
    rows, _grey = L.collect_states(root, grace=0, today=datetime.now(timezone.utc).date())
    return [r for r in rows if what in str(r[4]) or what == r[4]]


def test_a_claimed_run_whose_artifact_is_not_saved_is_not_done(tmp_path):
    root, sp = _data_root(tmp_path)
    (sp / "drafts" / "a.md").write_text("# a draft that was never committed\n")
    sha = hashlib.sha256((sp / "drafts" / "a.md").read_bytes()).hexdigest()
    _events(sp, "tris", [(3 * DAY, REG), (3600_000, {"metric": "cadence.run", "slug": TRIS_SLUG, "phase": "end",
                                                     "result": "ok", "artifact": "drafts/a.md", "sha256": sha})])
    assert _red(root, "geo-research"), "an 'ok' record whose file is not saved in git counted as done"


def test_a_saved_artifact_makes_it_done(tmp_path):
    root, sp = _data_root(tmp_path)
    sha = _commit(sp, "drafts/a.md", "# a real, saved draft\n")
    _events(sp, "tris", [(3 * DAY, REG), (3600_000, {"metric": "cadence.run", "slug": TRIS_SLUG, "phase": "end",
                                                     "result": "ok", "artifact": "drafts/a.md", "sha256": sha})])
    assert not _red(root, "geo-research")


def test_a_miles_duty_is_not_done_because_a_log_says_so(tmp_path):
    """Miles's duties are judged from the cadence log (EXECUTING={'miles'}): the agent's word."""
    root, sp = _data_root(tmp_path)
    assert _red(root, "state-backup"), "control: a duty that never ran is red"
    log = sp / ".datacore" / "state" / "venture" / "cadence-log.yaml"; log.parent.mkdir(parents=True)
    today = datetime.now(timezone.utc).date().isoformat()
    log.write_text(yaml.safe_dump({"coo": {"daily": {"state-backup": {"last_run": today, "result": "ok"}}}}))
    assert _red(root, "state-backup"), \
        "a cadence-log line saying state-backup ran, with no artifact anywhere, counted as done"


def _run(tmp_path, monkeypatch, agent):
    root, sp = _data_root(tmp_path)
    firm = tmp_path / "8-firm"; (firm / ".datacore").mkdir(parents=True)
    (firm / "venture.yaml").write_text(yaml.safe_dump({"name": "firm", "stage": "growth", "roles": {}}))
    (firm / ".datacore" / "cadence-control.yaml").write_text(yaml.safe_dump(
        {"ceilings": {"tris": 3}, "executors": {"tris": "hermes"}}))
    tpl = tmp_path / "tpl"; tpl.mkdir()
    (tpl / "geo-research.md").write_text("---\ncadence: geo-research\nevidence:\n  path: drafts/*-{date}.md\n"
                                         "  require: ['^# ']\n  min_bytes: 20\n---\nDo the research.\n")
    written = []

    class Log:
        def __init__(self, *a, **k): pass
        def append(self, t, payload): written.append(dict(payload))
    import cadence_engine, executors, actor_identity, ledger.log
    monkeypatch.setattr(cadence_engine, "TEMPLATES_DIR", tpl)
    monkeypatch.setattr(ledger.log, "EventLog", Log)
    monkeypatch.setattr(R, "key_ok", lambda actor: None)
    monkeypatch.setattr(R, "report", lambda *a, **k: "not published (eval)")
    monkeypatch.setattr(executors, "get_executor", lambda n: SimpleNamespace(run=lambda prompt, **kw: agent(kw["cwd"])))
    monkeypatch.setattr(actor_identity, "this_actor", lambda strict=True: "tris")
    monkeypatch.setattr(sys, "argv", ["cadence_run.py", TRIS_SLUG, "--root", str(root)])
    code = R.main()
    return code, written[-1]


def test_an_agent_that_only_says_done_fails(tmp_path, monkeypatch):
    code, end = _run(tmp_path, monkeypatch, lambda cwd: SimpleNamespace(
        text="Done. I researched the topic and saved the report to drafts/.", error=None))
    assert end["phase"] == "end" and end["result"] == "failed" and code == 1, end


def test_an_agent_that_leaves_the_promised_file_succeeds(tmp_path, monkeypatch):
    def agent(cwd):
        day = datetime.now(timezone.utc).date().isoformat()
        (cwd / "drafts" / f"topic-{day}.md").write_text("# Topic\n\nA researched draft.\n")
        return SimpleNamespace(text="done", error=None)
    code, end = _run(tmp_path, monkeypatch, agent)
    assert end["result"] == "ok" and end["artifact"].startswith("drafts/topic-") and len(end["sha256"]) == 64
