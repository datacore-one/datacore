"""The approvals policy's principals are the install's own (INS-3 batch 2).

The tracked approvals_policy.yaml ships neutral principals; the install's own
people and agents, and its arbitration order, live in the gitignored
`approvals_policy.local.yaml` beside it. load_policy merges them: a local
principal entry replaces the tracked one of that name, the rest are added, and
a local `arbitration` replaces the tracked one. The merged result is validated
exactly as a single file would be.
"""
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from ledger.policy import PolicyError, load_policy  # noqa: E402

TRACKED = """version: 1
approver: human
cosign_effects: [email.send]
principals:
  owner: {}
  assistant: {never_effects: [payment], may_delegate_to: []}
  auditor: {never_effects: [write], may_delegate_to: []}
arbitration: [owner, assistant]
"""


def test_without_a_local_file_the_tracked_policy_stands(tmp_path):
    (tmp_path / "approvals_policy.yaml").write_text(TRACKED)
    pol = load_policy(tmp_path / "approvals_policy.yaml")
    assert set(pol.principals) == {"owner", "assistant", "auditor"}
    assert pol.arbitration == ("owner", "assistant")


def test_the_local_file_adds_and_replaces_principals_and_arbitration(tmp_path):
    (tmp_path / "approvals_policy.yaml").write_text(TRACKED)
    (tmp_path / "approvals_policy.local.yaml").write_text(
        "principals:\n"
        "  boss: {}\n"
        "  assistant: {never_effects: [trading], may_delegate_to: [ops]}\n"
        "  ops: {never_effects: [payment], may_delegate_to: [assistant]}\n"
        "arbitration: [boss, assistant]\n")
    pol = load_policy(tmp_path / "approvals_policy.yaml")
    assert set(pol.principals) == {"owner", "assistant", "auditor", "boss", "ops"}
    assert pol.principals["assistant"] == {"never_effects": ["trading"], "may_delegate_to": ["ops"]}
    assert pol.principals["auditor"]["never_effects"] == ["write"], "tracked entries survive"
    assert pol.arbitration == ("boss", "assistant")
    assert pol.cosign_effects == frozenset({"email.send"}), "only principals/arbitration overlay"


def test_a_malformed_local_entry_is_refused_like_a_tracked_one(tmp_path):
    (tmp_path / "approvals_policy.yaml").write_text(TRACKED)
    (tmp_path / "approvals_policy.local.yaml").write_text("principals:\n  ops: {bogus: 1}\n")
    with pytest.raises(PolicyError):
        load_policy(tmp_path / "approvals_policy.yaml")


def test_an_unreadable_local_file_is_refused_not_ignored(tmp_path):
    (tmp_path / "approvals_policy.yaml").write_text(TRACKED)
    (tmp_path / "approvals_policy.local.yaml").write_text("principals: [a, b]\n")
    with pytest.raises(PolicyError):
        load_policy(tmp_path / "approvals_policy.yaml")
