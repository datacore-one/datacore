"""Liveness judges a principal's own-scheduler cadences from its signed records only (DIP-0050 P2)."""
import pytest
import hashlib, importlib.util, json, pathlib, subprocess, sys, time
from datetime import date
ROOT = pathlib.Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cl_sched", ROOT / ".datacore" / "lib" / "cadence_liveness.py")
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)

SLUG = "cadence-plur-cio-geo-research"
DAY = 86_400_000


def _space(tmp_path):
    sp = tmp_path / "5-plur"; (sp / "drafts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(sp)], check=True)
    subprocess.run(["git", "-C", str(sp), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "--allow-empty", "-m", "init"], check=True)
    return sp


def _commit(sp, rel, text):
    (sp / rel).write_text(text)
    subprocess.run(["git", "-C", str(sp), "add", rel], check=True)
    subprocess.run(["git", "-C", str(sp), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", rel], check=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _events(sp, records, sig="s"):
    d = sp / ".datacore" / "events"; d.mkdir(parents=True, exist_ok=True)
    with (d / "tris.jsonl").open("w") as fh:
        for i, (age_ms, payload) in enumerate(records):
            fh.write(json.dumps({"seq": i, "hlc": f"{int(time.time()*1000) - age_ms}.0000.tris", "actor": "tris",
                                 "type": "metric.attest", "payload": payload, "prev": "", "hash": "h", "sig": sig}) + "\n")


REG = {"metric": "cadence.registration", "slugs": {SLUG: "46 5 * * *"}}


@pytest.fixture(autouse=True)
def _placeholder_signatures(monkeypatch, request):
    """The fixtures sign with the placeholder "s"; real verification is tested below."""
    if "real_signatures" not in request.keywords:
        monkeypatch.setattr(L, "_sig_ok", lambda e: e.sig == "s")


def _state(sp):
    return L.scheduled_state(sp, "plur", "tris", "cio", "daily", "geo-research", date.today())


def test_unregistered_is_pending(tmp_path):
    assert _state(_space(tmp_path)) is None


def test_a_verified_run_is_green(tmp_path):
    sp = _space(tmp_path); sha = _commit(sp, "drafts/a.md", "# draft\n")
    _events(sp, [(3 * DAY, REG), (3600_000, {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "ok",
                                             "artifact": "drafts/a.md", "sha256": sha})])
    assert _state(sp)[0] == "green"


def test_a_claimed_run_without_its_artifact_in_git_is_late(tmp_path):
    sp = _space(tmp_path); _commit(sp, "drafts/a.md", "# draft\n")
    _events(sp, [(3 * DAY, REG), (3600_000, {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "ok",
                                             "artifact": "drafts/a.md", "sha256": "0" * 64})])
    assert _state(sp)[0] == "red"


def test_quota_is_amber_then_decays_to_late(tmp_path):
    sp = _space(tmp_path)
    _events(sp, [(3 * DAY, REG), (3600_000, {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "quota", "reason": "credits"})])
    assert _state(sp)[0] == "amber"
    _events(sp, [(5 * DAY, REG), (2 * DAY, {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "quota", "reason": "credits"})])
    assert _state(sp)[0] == "red"


def test_a_fresh_registration_is_not_late_yet(tmp_path):
    sp = _space(tmp_path)
    _events(sp, [(3600_000, REG)])
    assert _state(sp)[0] == "green"


def test_a_slug_missing_from_the_registration_is_not_registered(tmp_path):
    sp = _space(tmp_path)
    _events(sp, [(3 * DAY, {"metric": "cadence.registration", "slugs": {"cadence-plur-cio-other": "0 5 * * *"}})])
    st = _state(sp)
    assert st[0] == "red" and "not-registered" in st[2]


def test_unsigned_records_do_not_count(tmp_path):
    sp = _space(tmp_path)
    _events(sp, [(3 * DAY, REG)], sig="")
    assert _state(sp) is None


def test_an_artifact_in_another_space_is_verified_there(tmp_path):
    sp = _space(tmp_path)
    other = tmp_path / "0-personal"; (other / "drafts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    sha = _commit(other, "drafts/j.md", "## Daily Briefing\n")
    end = {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "ok",
           "artifact": "drafts/j.md", "sha256": sha, "artifact_space": "personal"}
    _events(sp, [(3 * DAY, REG), (3600_000, end)])
    assert _state(sp)[0] == "green"
    _events(sp, [(3 * DAY, REG), (3600_000, {**end, "artifact_space": "nowhere"})])
    assert _state(sp)[0] == "red", "an unresolvable space verifies nothing"
    assert L.space_named(tmp_path, "personal") == other and L.space_named(tmp_path, "plur") == sp and L.space_named(tmp_path, "firm") is None


@pytest.mark.real_signatures
def test_a_copied_hash_is_not_a_signature(tmp_path):
    """2026-09-25: an agent wrote sig = the event's own hash. The real verifier refuses it."""
    sp = _space(tmp_path); sha = _commit(sp, "drafts/a.md", "# draft\n")
    run = {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "ok",
           "artifact": "drafts/a.md", "sha256": sha}
    _events(sp, [(3 * DAY, REG), (3600_000, run)], sig="dabfeaef2c304d17b650")
    assert _state(sp) is None, "neither the registration nor the run is signed, so nothing counts"
