"""A repair must not be completable by weakening the thing that judges it.

job_verify now delegates a recurring failure to an agent instead of waking the
operator, and the item it writes says "make this job verify". That instruction
has a cheap wrong answer: loosen the regex, widen exit_ok, raise max_age_hours,
or delete the job. All four turn the check green.

This is not hypothetical and it is not subtle. Measured 2026-09-20: with
mac-seq-gap's regex changed from "0 with unpublished events, 0 error" to
"unpublished events", `job_verify --machine mac` reported OK 19 jobs 19
artifacts while the fleet had an unpublished-events gap. The verifier cannot
catch this, by construction -- it is doing what the contract says, and the
contract is what moved.

So the boundary is checked separately, and these tests are what keep it real.
Failure mode 3 of the loop-design gate in CLAUDE.md, stated as tests.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
if str(LIB / "jobs") not in sys.path:
    sys.path.insert(0, str(LIB / "jobs"))

import fix_check  # noqa: E402

FIX_CHECK = LIB / "jobs" / "fix_check.py"


@pytest.fixture
def manifest(tmp_path) -> Path:
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump({"jobs": [{
        "name": "fixture-job", "machine": "mac", "schedule": "0 * * * *",
        "cmd": "true", "exit_ok": [0], "on_fail": "log",
        "artifacts": [{"path": "~/x.log", "check": "regex", "arg": "^OK exactly$"}],
    }]}))
    return p


def _run(job: str, sha: str, manifest: Path):
    return subprocess.run(
        [sys.executable, str(FIX_CHECK), "--job", job, "--machine", "mac",
         "--contract-sha", sha, "--manifest", str(manifest)],
        capture_output=True, text=True, timeout=300)


def test_a_loosened_regex_is_refused(manifest):
    """The exact 2026-09-20 attack: make the check pass by making it weaker."""
    before = fix_check.contract_sha("fixture-job", manifest)
    data = yaml.safe_load(manifest.read_text())
    data["jobs"][0]["artifacts"][0]["arg"] = "OK"       # matches far more
    manifest.write_text(yaml.safe_dump(data))

    proc = _run("fixture-job", before, manifest)
    assert proc.returncode == 1
    assert "contract for fixture-job changed" in proc.stderr


def test_a_widened_exit_ok_is_refused(manifest):
    before = fix_check.contract_sha("fixture-job", manifest)
    data = yaml.safe_load(manifest.read_text())
    data["jobs"][0]["exit_ok"] = [0, 1, 2]
    manifest.write_text(yaml.safe_dump(data))
    assert _run("fixture-job", before, manifest).returncode == 1


def test_a_raised_max_age_is_refused(manifest):
    """Staleness is a contract term: a job that may be 30 days old asserts
    nothing about whether it still runs."""
    before = fix_check.contract_sha("fixture-job", manifest)
    data = yaml.safe_load(manifest.read_text())
    data["jobs"][0]["artifacts"][0]["max_age_hours"] = 720
    manifest.write_text(yaml.safe_dump(data))
    assert _run("fixture-job", before, manifest).returncode == 1


def test_deleting_the_job_is_refused(manifest):
    """The cheapest wrong answer of all, and it must not read as success."""
    before = fix_check.contract_sha("fixture-job", manifest)
    manifest.write_text(yaml.safe_dump({"jobs": []}))

    proc = _run("fixture-job", before, manifest)
    assert proc.returncode == 1
    assert "no longer in the manifest" in proc.stderr
    assert "Deleting the check is not repairing the producer" in proc.stderr


def test_reformatting_the_manifest_is_not_a_violation(manifest):
    """The boundary guards VALUES, not bytes.

    Hashing the file text would fail on a comment, a reordered key or a
    different quote style, and a boundary that cries wolf gets removed. The
    hash is taken from the parsed entry re-serialised canonically.
    """
    before = fix_check.contract_sha("fixture-job", manifest)
    data = yaml.safe_load(manifest.read_text())
    manifest.write_text("# a comment nobody should be punished for\n"
                        + yaml.safe_dump(data, default_flow_style=True))
    assert fix_check.contract_sha("fixture-job", manifest) == before


def test_an_unrelated_job_changing_is_not_a_violation(manifest):
    """One repair must not be blocked by an unrelated edit elsewhere."""
    before = fix_check.contract_sha("fixture-job", manifest)
    data = yaml.safe_load(manifest.read_text())
    data["jobs"].append({"name": "other-job", "machine": "box", "cmd": "true",
                         "artifacts": [{"path": "~/y.log", "check": "exists"}]})
    manifest.write_text(yaml.safe_dump(data))
    assert fix_check.contract_sha("fixture-job", manifest) == before


def _repair(monkeypatch, *, kind, closed_at, job="drill-job"):
    import autofix
    monkeypatch.setattr(autofix, "repairs", lambda root: [{
        "id": "autofix-x", "status": "dismissed", "closed_kind": kind,
        "closed_reason": "gave up after 3 failed attempts", "job": job,
        "assignee": "miles", "owner": "miles", "closed_at": closed_at,
    }])
    return autofix


def test_a_dead_letter_escalates_while_it_is_recent(monkeypatch):
    now = 1_800_000_000_000.0
    autofix = _repair(monkeypatch, kind="dropped", closed_at=f"{int(now - 3600_000)}.0000.mac")
    assert autofix.escalations(Path("/x"), now_ms=now) == [
        "drill-job: miles gave up — gave up after 3 failed attempts"]


def test_an_old_dead_letter_stops_escalating(monkeypatch):
    """Otherwise one permanent daily alert becomes one permanent hourly alert.

    A dismissal cannot be undone, so without a window this reports the same
    dead-letter forever. It is safe to age out because the JOB is the record of
    the problem: if it still fails, a fresh repair is filed with today's date
    and escalates on its own.
    """
    now = 1_800_000_000_000.0
    old = f"{int(now - 30 * 24 * 3600_000)}.0000.mac"
    autofix = _repair(monkeypatch, kind="dropped", closed_at=old)
    assert autofix.escalations(Path("/x"), now_ms=now) == []


def test_a_repair_that_finished_never_escalates(monkeypatch):
    now = 1_800_000_000_000.0
    autofix = _repair(monkeypatch, kind="done", closed_at=f"{int(now)}.0000.mac")
    assert autofix.escalations(Path("/x"), now_ms=now) == []


def test_an_undateable_dead_letter_is_reported_not_aged_out(monkeypatch):
    """Cannot-tell is not a pass. An unreadable timestamp must not become a
    way for an escalation to vanish quietly."""
    now = 1_800_000_000_000.0
    autofix = _repair(monkeypatch, kind="dropped", closed_at="not-an-hlc")
    assert len(autofix.escalations(Path("/x"), now_ms=now)) == 1


def test_the_delegated_item_tells_the_agent_the_boundary():
    """The instruction has to SAY it, not only enforce it after the fact.

    An agent that learns the rule by being refused has already spent one of its
    three attempts finding out what it was not allowed to do. Every way of
    cheating that fix_check rejects must also be named in the text the agent
    reads first, or the two drift apart and the refusal becomes a surprise.
    """
    import types

    import autofix

    job = types.SimpleNamespace(name="fixture-job", machine="mac",
                                cmd="run-me.sh", schedule="0 * * * *")
    body = autofix.repair_body(
        job, ["~/x.log: regex did not match"],
        {"consecutive": 3, "first_failed": "2026-09-20"})

    assert "FIX THE PRODUCER, NOT THE CHECK." in body
    for cheat in ("regex", "exit_ok", "max_age_hours", "removing"):
        assert cheat in body, f"the boundary text never mentions {cheat}"
    # It must also say what to do when the contract really is the wrong one,
    # otherwise the only path left is to cheat or to fail three times.
    assert "human" in body

    # And the failure detail has to survive into the item, or the agent starts
    # by rediscovering what the verifier already knew.
    assert "regex did not match" in body
    assert "run-me.sh" in body


def _open_repair(monkeypatch, *, status, opened_h_ago, now):
    import autofix
    monkeypatch.setattr(autofix, "_acked", lambda: set())
    monkeypatch.setattr(autofix, "repairs", lambda root: [{
        "id": "autofix-y", "status": status, "closed_kind": None, "closed_reason": None,
        "job": "stuck-job", "assignee": "miles", "owner": None, "closed_at": None,
        "opened_at": f"{int(now - opened_h_ago * 3600_000)}.0000.mac",
    }])
    return autofix


def test_a_repair_nobody_finishes_reaches_the_operator(monkeypatch):
    """The condition the docstring promised and the first version lacked.

    An unclaimed repair is not dismissed, so it matched no escalation rule and
    would have sat in `created` forever: withheld from the operator, handed to
    nobody, reported by nothing.
    """
    now = 1_800_000_000_000.0
    autofix = _open_repair(monkeypatch, status="created", opened_h_ago=30, now=now)
    rows = autofix.escalations(Path("/x"), now_ms=now)
    assert len(rows) == 1 and "stuck-job" in rows[0] and "not finished" in rows[0]


def test_a_repair_in_flight_is_not_news(monkeypatch):
    now = 1_800_000_000_000.0
    autofix = _open_repair(monkeypatch, status="claimed", opened_h_ago=2, now=now)
    assert autofix.escalations(Path("/x"), now_ms=now) == []


# ── refused up front: what the repairer cannot check, and what opts out ────────

def _job(name="box-x", machine="box", delegate=True):
    import types
    return types.SimpleNamespace(name=name, machine=machine, delegate=delegate, cmd="x", schedule="x")


def _roster(tmp_path):
    p = tmp_path / "infrastructure.yaml"
    p.write_text("servers:\n  winston: {manifest_machine: box, ledger_actors: [winston]}\n  nightshift: {ledger_actors: [nightshift, miles]}\n")
    return p


def test_a_job_whose_repository_cannot_be_named_is_refused_before_anything_is_written(tmp_path, monkeypatch):
    """Two stages need a repository to merge into. Without one there is no
    done-condition, and a delegation with no done-condition is what dead-lettered
    every box and mac repair on 2026-09-21/22."""
    import autofix
    monkeypatch.setattr(autofix, "contract_sha", lambda name, manifest: "abc")
    monkeypatch.setattr(autofix, "repo_for", lambda job, root: None)
    state, why = autofix.delegate(_job("box-x", "box"), ["f"], {}, root=tmp_path, roster=_roster(tmp_path))
    assert state == "refused" and "cannot name the repository" in why
    assert not (tmp_path / "2-datacore").exists(), "a refusal must write nothing"


def test_a_job_that_opts_out_is_refused(tmp_path):
    import autofix
    state, why = autofix.delegate(_job("box-autofix-escalation", "box", delegate=False), ["f"], {},
                                  root=tmp_path, roster=_roster(tmp_path))
    assert state == "refused" and "opts out" in why


def test_a_job_on_the_repairers_own_host_is_still_delegated(tmp_path):
    import autofix
    state, why = autofix.delegate(_job("nightshift-x", "nightshift"), ["f"], {}, root=tmp_path,
                                  roster=_roster(tmp_path), dry=True)
    assert state == "refused" and "not in the manifest" in why, "reached the manifest check: host and opt-out passed"


def test_the_delegation_machinery_opts_out_in_the_real_manifest():
    import yaml
    jobs = {j["name"]: j for j in yaml.safe_load((LIB / "jobs" / "manifest.yaml").read_text())["jobs"]}
    for name in ("box-autofix-escalation", "box-delegation-canary", "nightshift-delegation-drill"):
        assert jobs[name].get("delegate") is False, f"{name} would be handed to the agent it checks"


# ── two stages when the repairer is elsewhere ─────────────────────────────────

def _capture_delegation(monkeypatch, tmp_path, job):
    """Run delegate() against fakes for the ledger, the actor, the repo and the manifest."""
    import autofix
    import ledger.policy
    import actor_identity
    captured = {}
    monkeypatch.setattr(ledger.policy, "guarded_append", lambda log, kind, payload: captured.update(payload))
    monkeypatch.setattr(actor_identity, "this_actor", lambda: "winston")
    monkeypatch.setattr(autofix, "contract_sha", lambda name, manifest: "abc")
    monkeypatch.setattr(autofix, "repo_for", lambda job, root: "datacore-one/datacore")
    roster = tmp_path / "infrastructure.yaml"
    roster.write_text("servers:\n"
                      "  winston: {manifest_machine: box, kind: server, ledger_actors: [winston], access: {actor: winston}}\n"
                      "  nightshift: {kind: server, ledger_actors: [nightshift, miles], access: {actor: miles}}\n"
                      "  mac: {kind: workstation, ledger_actors: [mac], access: {actor: mac}}\n")
    from jobs import awake
    monkeypatch.setattr(awake, "always_on", lambda m, r=None: m != "mac")
    (tmp_path / "2-datacore" / ".datacore" / "events").mkdir(parents=True)
    state, why = autofix.delegate(job, ["f"], {"first_failed": "2026-09-22"}, root=tmp_path, roster=roster)
    return state, why, captured


def test_a_box_job_is_a_merge_for_miles_then_a_verify_for_winston(tmp_path, monkeypatch):
    state, why, p = _capture_delegation(monkeypatch, tmp_path, _job("box-x", "box"))
    assert state == "delegated", why
    assert "--stage merged" in p["check"] and "--repo datacore-one/datacore" in p["check"] and f"--item {p['id']}" in p["check"]
    assert p["stage"] == "merged" and "MERGED pull request" in p["body"] and "merge rights" in p["body"]
    then = p["then"]
    assert then["assignee"] == "winston" and then["id"] == p["id"] + "-verify"
    assert then["check"].startswith("python3 .datacore/lib/jobs/fix_check.py --job box-x --machine box")
    assert "--stage" not in then["check"], "the follow-up is the ordinary verification on the box"


def test_a_mac_job_is_a_merge_with_no_follow_up(tmp_path, monkeypatch):
    """A visitor pulls on wake; its contract passes by itself."""
    state, why, p = _capture_delegation(monkeypatch, tmp_path, _job("mac-x", "mac"))
    assert state == "delegated" and "--stage merged" in p["check"] and "then" not in p
    assert "pulls on its own schedule" in p["body"]


def test_a_nightshift_job_is_still_one_stage(tmp_path, monkeypatch):
    state, why, p = _capture_delegation(monkeypatch, tmp_path, _job("nightshift-x", "nightshift"))
    assert state == "delegated" and "--stage" not in p["check"] and "then" not in p and p["stage"] == "verify"


def test_roster_names_resolve_the_manifests_machine_names(tmp_path):
    """The manifest says `box`; the roster says `winston` with manifest_machine: box."""
    import autofix
    r = _roster(tmp_path)
    assert autofix.host_of("miles", r) == "nightshift"
    assert autofix.host_of("winston", r) == "box"
    assert autofix.actor_of("box", r) == "winston"


def test_an_agent_may_merge_only_into_the_core_repository(tmp_path, monkeypatch):
    """The owner's boundary, 2026-09-22: 'this is only for datacore'. A module's
    repository, or anything under another organisation, is refused whatever
    push rights the account holds."""
    import autofix
    assert autofix.MERGE_REPOS == frozenset({"datacore-one/datacore"})
    monkeypatch.setattr(autofix, "contract_sha", lambda name, manifest: "abc")
    for repo in ("datacore-one/datacore-nightshift", "plur-ai/plur", "plur9/module-personal-finance"):
        monkeypatch.setattr(autofix, "repo_for", lambda job, root, r=repo: r)
        state, why = autofix.delegate(_job("box-x", "box"), ["f"], {}, root=tmp_path, roster=_roster(tmp_path))
        assert state == "refused" and repo in why and "owner's boundary" in why, repo
    assert not (tmp_path / "2-datacore").exists()


# ── an escalation clears when its job recovers ────────────────────────────────

def _one_drop(monkeypatch, closed_ms):
    import autofix
    monkeypatch.setattr(autofix, "repairs", lambda root: [{
        "id": "autofix-box-x-20260922", "status": "dismissed", "closed_kind": "dropped",
        "closed_at": f"{closed_ms}.0000.miles", "closed_reason": "gave up after 3 failed attempts",
        "job": "box-x", "assignee": "miles", "opened_at": None}])
    monkeypatch.setattr(autofix, "_acked", lambda: set())


def _verify_event(root, job, ok, ms):
    import json
    ev = root / "2-datacore" / ".datacore" / "events"; ev.mkdir(parents=True, exist_ok=True)
    with (ev / "winston.jsonl").open("a") as fh:
        fh.write(json.dumps({"actor": "winston", "hlc": f"{ms}.0000.winston",
                             "payload": {"metric": "job.verify", "job": job, "ok": ok, "failures": []}}) + "\n")


def test_a_dead_letter_whose_job_has_since_passed_is_history(tmp_path, monkeypatch):
    import autofix
    now = 1_790_000_000_000
    _one_drop(monkeypatch, closed_ms=now - 3_600_000)
    _verify_event(tmp_path, "box-x", ok=True, ms=now - 600_000)          # passed after the drop
    assert autofix.escalations(tmp_path, now_ms=now) == []


def test_a_pass_before_the_drop_does_not_count(tmp_path, monkeypatch):
    import autofix
    now = 1_790_000_000_000
    _one_drop(monkeypatch, closed_ms=now - 3_600_000)
    _verify_event(tmp_path, "box-x", ok=True, ms=now - 7_200_000)        # passed, then dropped
    _verify_event(tmp_path, "box-x", ok=False, ms=now - 300_000)
    out = autofix.escalations(tmp_path, now_ms=now)
    assert len(out) == 1 and "gave up" in out[0]
