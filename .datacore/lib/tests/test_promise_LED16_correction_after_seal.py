"""LED-16: A correction after the seal is accepted only from the owner or the
original writer, only for a sealed record, and always leaves an incident
naming the check that should have stopped the original.

Kind: deterministic (temporary hub spaces; never the live installation).

Seeded failure: the real round-5 audit finding C4, on the head that audit
reviewed (f90a125d): a forward correction by an unrelated writer, of an
unsealed original, or of an original the space does not hold, imported by
git, was confirmed although the front door would have refused it. It is the only promise of the ten whose defect was found before
round 6; the round-6 and round-7 audits found no compensation defect (C4
stayed closed). (No Lean theorem covers this promise yet.)

Promise, as evals: forward corrections of one completed task event -- by the
owner, by the original's writer, by an unrelated writer, of an unsealed
original, of a missing original, without a reason -- each sent over the
network and, separately, imported by git. Only the owner's and the writer's
corrections of the sealed original are accepted; each accepted one records
exactly one incident naming the write gate that should have stopped the
original; no refused one records an incident or gets a confirmed receipt; and
the original's own receipt stays "confirmed" throughout.
"""
from __future__ import annotations

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _confirmed, _void, env  # noqa: F401
from ledger import hub as H

CASES = {
    "owner": True, "original-writer": True,
    "unrelated-writer": False, "unsealed-original": False, "missing-original": False, "no-reason": False,
}


@pytest.mark.parametrize("path", ["network", "git"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_only_a_lawful_correction_after_the_seal_is_accepted(env, monkeypatch, case, path):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    if case != "unsealed-original":
        h.seal()
    wb = env.writer("wb", H.LocalTransport(h))
    writer = {"original-writer": wa, "unrelated-writer": wb}.get(case, owner)
    payload = _void(done, "the completion named the wrong task")
    if case == "missing-original":
        payload["hash"] = "0" * 64
    if case == "no-reason":
        payload["reason"] = ""
    c = T._signed_into(env, writer, "ledger.compensate", payload)
    accepted_at_door = None
    if path == "network":
        try:
            accepted_at_door = h.append(writer._submission(c))["status"] == "accepted"
        except H.HubRefused:
            accepted_at_door = False
    else:
        T.git_pull(writer.space_dir, env.hub_dir)
    h.seal()
    lawful = CASES[case]
    if path == "network":
        assert accepted_at_door == lawful, f"[{case}] the hub {'refused' if lawful else 'accepted'} it"
    got = writer.receipt_check(env.hub_dir, required=[c.hash])["status"]
    assert (got == "confirmed") == lawful, f"[{case}/{path}] the correction's receipt is {got!r}"
    rows = H.incidents(env.hub_dir, registry_path=env.registry, policy=T.TEST_POLICY)
    mine = [r for r in rows if r["correction"] == c.hash]
    if lawful:
        assert len(mine) == 1, f"[{case}/{path}] expected exactly one incident, found {len(mine)}"
        assert mine[0]["original"] == done.hash and "gate" in mine[0]["gate"], mine[0]
    else:
        assert mine == [], f"[{case}/{path}] a refused correction recorded an incident"
    if case != "unsealed-original":
        assert wa.receipt_check(env.hub_dir, required=[done.hash])["status"] == "confirmed", \
            f"[{case}/{path}] the sealed original stopped being confirmed"
    assert h._state()[1].items["t"].status == "completed"
