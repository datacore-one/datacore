"""LED-3: Only the ledger writer can add to the history; a hand-written or
impostor record is refused before it is saved or shared.

Kind: deterministic. 2026-09-27: the agent-eval harness read a credential as
the test actor "fixture"; the credential broker's attestation wrote 12
`artifact.attest` events into the real 1-datafund space under that name, and
only the pre-push ownership guard stopped them. A record by an actor who is not
a declared member of the space must never be written at all.

Seeded failure: ledger_attest.attest appends for any actor (the member check
removed) -> the fixture actor's event lands in the space.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import ledger_attest  # noqa: E402


def _space(tmp_path, members):
    sp = tmp_path / "1-space"
    (sp / ".datacore" / "events").mkdir(parents=True)
    (sp / ".datacore" / "members.yaml").write_text("members:\n" + "".join(f"  - {m}\n" for m in members))
    return sp


def test_an_undeclared_actor_writes_nothing(tmp_path, monkeypatch):
    sp = _space(tmp_path, ["mac", "miles"])
    monkeypatch.setattr(ledger_attest, "_space", lambda space: sp)
    monkeypatch.setattr(ledger_attest, "_actor", lambda: "fixture")
    assert ledger_attest.attest("credential.read", ref="x", detail="y") is None
    assert not list(sp.glob(".datacore/**/fixture*.jsonl")), "an undeclared actor's log was created"


def test_a_declared_member_still_attests(tmp_path, monkeypatch):
    sp = _space(tmp_path, ["mac", "miles"])
    monkeypatch.setattr(ledger_attest, "_space", lambda space: sp)
    monkeypatch.setattr(ledger_attest, "_actor", lambda: "mac")
    assert ledger_attest.attest("credential.read", ref="x", detail="y")
    assert list(sp.glob(".datacore/**/mac*.jsonl")), "a member's attestation was not written"   # task log or telemetry log
