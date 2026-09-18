"""A vanilla datacore install must work before anyone configures identity.

`registry/principals.yaml` is a gitignored private overlay, so a fresh checkout
ships without one. Once `load_policy()` learned to fall back to the policy
shipped beside the code, stage 4 began running everywhere, found no principals,
and refused EVERY item.create as "unregistered writer": a new installation could
not create a single task. Introduced and found on 2026-09-19, about two hours
apart, by asking what these fixes do on a machine that is not this one.

Zero declared principals means identity is unconfigured and the gate cannot
answer. One or more means it can, and a writer missing from that roster is a
real refusal. An INVALID registry is not "unconfigured" -- it is a configured
installation with a broken file, and it must still refuse.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import actor_identity  # noqa: E402
import claim_gate  # noqa: E402
from ledger.log import EventLog  # noqa: E402
from ledger.policy import PolicyError, guarded_append  # noqa: E402


@pytest.fixture
def vanilla(tmp_path, monkeypatch):
    """No principals.yaml at all -- a fresh checkout."""
    monkeypatch.setattr(actor_identity, "PRINCIPALS",
                        tmp_path / ".datacore/registry/principals.yaml")
    actor_identity._PRINCIPALS_CACHE.clear()
    return tmp_path


@pytest.fixture
def configured(tmp_path, monkeypatch):
    registry = tmp_path / ".datacore/registry/principals.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("principals:\n  gregor: {kind: human, writes_as: [mac]}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry)
    actor_identity._PRINCIPALS_CACHE.clear()
    return tmp_path


def test_a_fresh_install_can_create_its_first_task(vanilla):
    guarded_append(EventLog(vanilla / "1-personal", "mac", sign=False), "item.create",
                   {"id": "v1", "title": "first task on a fresh install"})


def test_a_fresh_install_can_claim_it(vanilla):
    space = vanilla / "1-personal"
    guarded_append(EventLog(space, "mac", sign=False), "item.create",
                   {"id": "v2", "title": "work"})
    guarded_append(EventLog(space, "mac", sign=False), "item.claim",
                   {"id": "v2", "owner": "mac"})


def test_a_configured_install_still_refuses_a_stranger(configured):
    with pytest.raises(PolicyError, match="unregistered writer"):
        guarded_append(EventLog(configured / "1-personal", "nobody", sign=False),
                       "item.create", {"id": "c1", "title": "work"})


def test_a_configured_install_admits_its_own_writer(configured):
    guarded_append(EventLog(configured / "1-personal", "mac", sign=False), "item.create",
                   {"id": "c2", "title": "work"})


def test_a_broken_registry_is_not_an_unconfigured_one(tmp_path, monkeypatch):
    """Otherwise deleting or corrupting the file silently disables the gate."""
    registry = tmp_path / ".datacore/registry/principals.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("principals: [this is not a mapping]\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry)
    actor_identity._PRINCIPALS_CACHE.clear()

    assert claim_gate._registry_is_configured() is True
    ok, why = claim_gate.check_create("mac", {"id": "x", "title": "t"})
    assert not ok and "unreadable" in why, why
