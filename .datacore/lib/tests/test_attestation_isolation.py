"""A broken chain that carries no evidence must not silence the evidence.

`latest_jobs` answers one question -- when did each principal last pass a job
verification -- and it used to verify EVERY writer chain in EVERY space before
answering. Any single invalid chain anywhere made it raise; `claim_gate.absent`
catches that and reports the principal absent; so one bad event turned the
absence signal into a constant for the whole fleet, and every delegated item
was stamped `assignee_absent` for a reason unrelated to whether anyone had been
heard from.

Measured on the live installation, 2026-09-18: exactly one invalid chain --
`5-plur/tris`, 14 events, carrying no attestations at all -- and it made all
four principals read as absent. With it isolated, three read as verified 11h
ago and the fourth as genuinely 299h stale, which was true and had been
invisible.

The guarantee is unchanged: an attestation is only used if its OWN chain
verifies. What changed is that a chain contributing no attestation is no longer
allowed to veto the answer; its integrity is `v2_verify`'s hash-chain check to
report, and it does.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import actor_identity  # noqa: E402
from job_attestations import latest_jobs  # noqa: E402
from ledger.log import CorruptLogError, EventLog  # noqa: E402


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    registry = tmp_path / ".datacore/registry/principals.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("principals:\n"
                        "  worker: {kind: agent, writes_as: [worker]}\n"
                        "  bystander: {kind: agent, writes_as: [bystander]}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry)
    return tmp_path


def _attest(space: Path, actor: str, job: str, ok: bool = True):
    EventLog(space, actor, sign=False).append(
        "metric.attest", {"metric": "job.verify", "job": job, "ok": ok, "failures": []})


def _corrupt(space: Path, actor: str):
    """A chain whose payload no longer matches its recorded hash."""
    EventLog(space, actor, sign=False).append("item.create", {"id": "x", "title": "before"})
    log = space / ".datacore" / "events" / f"{actor}.jsonl"
    log.write_text(log.read_text().replace('"title": "before"', '"title": "after"')
                   .replace('"title":"before"', '"title":"after"'))


def test_a_corrupt_chain_carrying_no_evidence_does_not_hide_the_evidence(fleet):
    space = fleet / "1-work"
    _attest(space, "worker", "worker-daily")
    _corrupt(space, "bystander")

    jobs = latest_jobs(fleet, time.time())

    assert "worker" in jobs, "the attestation must still be readable"
    assert jobs["worker"]["worker-daily"].ok is True


def test_a_corrupt_chain_that_does_carry_evidence_still_raises(fleet):
    # The guarantee itself: an attestation from a chain that does not verify is
    # not evidence, and the reader is told rather than quietly given it.
    space = fleet / "1-work"
    _attest(space, "worker", "worker-daily")
    log = space / ".datacore" / "telemetry" / "worker.jsonl"  # the telemetry log (LED-8)
    log.write_text(log.read_text().replace('"job": "worker-daily"', '"job": "tampered"')
                   .replace('"job":"worker-daily"', '"job":"tampered"'))

    with pytest.raises(CorruptLogError):
        latest_jobs(fleet, time.time())


def _tamper_attestation(space: Path, actor: str):
    """An evidence-carrying chain whose attestation no longer matches its hash."""
    _attest(space, actor, f"{actor}-daily")
    log = space / ".datacore" / "telemetry" / f"{actor}.jsonl"
    log.write_text(log.read_text().replace(f'"job": "{actor}-daily"', '"job": "tampered"')
                   .replace(f'"job":"{actor}-daily"', '"job":"tampered"'))


def test_asking_about_one_principal_reads_only_its_own_evidence(fleet):
    # 2026-11-26: winston's and miles's evidence-carrying chains failed signature
    # checks after unregistered key rotations, and that made `absent("data")`
    # true -- every item delegated to Data was stamped `assignee_absent` while
    # Data's own chains verified. Whether Data has been heard from is a question
    # about Data's evidence only.
    space = fleet / "1-work"
    _attest(space, "worker", "worker-daily")
    _tamper_attestation(space, "bystander")

    jobs = latest_jobs(fleet, time.time(), principal="worker")

    assert set(jobs) == {"worker"}
    assert jobs["worker"]["worker-daily"].ok is True


def test_asking_about_the_principal_whose_evidence_is_broken_still_raises(fleet):
    # The guarantee, per principal: its own evidence from a chain that does not
    # verify is not evidence, and the reader is told.
    space = fleet / "1-work"
    _attest(space, "worker", "worker-daily")
    _tamper_attestation(space, "bystander")

    with pytest.raises(CorruptLogError):
        latest_jobs(fleet, time.time(), principal="bystander")


def test_absent_is_not_decided_by_another_principals_broken_chain(fleet, monkeypatch):
    import claim_gate
    space = fleet / "1-work"
    _attest(space, "worker", "worker-daily")
    _tamper_attestation(space, "bystander")

    is_absent, why = claim_gate.absent("worker", root=fleet)
    assert is_absent is False, why
    is_absent, why = claim_gate.absent("bystander", root=fleet)
    assert is_absent is True and "unreadable or invalid" in why


def test_a_void_already_in_the_history_is_honoured(fleet):
    # 2026-11-27: 6-meridian miles.jsonl seq 21140-21141 and 5-plur tris seq 5
    # were cancelled by owner voids in September, and `verify` accepts them.
    # `latest_jobs` verified chains without the space's voids, so those
    # long-settled events still read as "invalid verification event chain".
    from tests._legacy_void import append_legacy_void
    from ledger import voids
    from ledger.log import read_events

    registry = fleet / ".datacore/registry/principals.yaml"
    registry.write_text(registry.read_text() + "  owner: {kind: human, writes_as: [owner]}\n")
    space = fleet / "1-work"
    _attest(space, "worker", "first")
    _attest(space, "worker", "second")
    log = space / ".datacore" / "telemetry" / "worker.jsonl"
    log.write_text(log.read_text().replace('"job": "first"', '"job": "tampered"')
                   .replace('"job":"first"', '"job":"tampered"'))
    bad = next(e for e in read_events(space) if e.payload.get("job") == "tampered")
    append_legacy_void(EventLog(space, "owner", sign=False), {
        "log": bad.log, "seq": bad.seq, "hash": bad.hash,
        "reason": "test: hash never matched", "body_sha256": voids.body_hash(bad)})

    jobs = latest_jobs(fleet, time.time(), principal="worker")

    assert jobs["worker"]["second"].ok is True
    assert "tampered" not in jobs["worker"], "a voided attestation is not evidence"


def test_an_intact_fleet_still_reports_every_principal(fleet):
    space = fleet / "1-work"
    _attest(space, "worker", "worker-daily")
    _attest(space, "bystander", "bystander-daily")

    jobs = latest_jobs(fleet, time.time())

    assert set(jobs) == {"worker", "bystander"}
