"""The hub as the front door for READS (plan 2026-10-31, phase D part 2).

Written before the code. One test per "Done when" line of phase D that this
part owns:

  (a) claims and folds read the hub's admitted log when the hub answers, and
      the git copy when it does not -- and a hub answer is never trusted more
      than the git copy: what it sends goes through the same admission
      (signatures, chains, signing domain) as events that arrived by git;
  (c) a daytime task is claimable within one claim interval with no publish
      step: `materialize` sends the new item to the hub, and the claimer's
      planning, reading the hub, sees it on its next tick although its own
      git copy has not moved.

(b), the seal-age job contract, is tested in test_ledger_seal_age_check.py.
"""
from __future__ import annotations

import io
import json
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from tests import test_ledger_hub as T
from ledger import hub as H
from ledger.events import Event, from_line, to_line
from ledger.log import read_task_events


@pytest.fixture
def env(tmp_path, monkeypatch):
    import ledger.keys as keys
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "runtime"))
    monkeypatch.delenv("DATACORE_HUB_URL", raising=False)
    monkeypatch.delenv("DATACORE_HUB_TOKEN_ID", raising=False)
    e = T.Env(tmp_path, monkeypatch)
    # Writers that do not take keys_dir/registry_path (materialize, the claim
    # loop) use the defaults: point them at the test's keys and registry.
    monkeypatch.setattr(keys, "DEFAULT_KEYS_DIR", e.keys_dir)
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", e.registry)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # The daytime delegator and the claimer are declared writers (the create gate).
    import actor_identity
    import ledger.policy
    principals = tmp_path / "principals.yaml"
    principals.write_text("principals:\n  boss: {kind: human}\n  own: {kind: agent}\n  wa: {kind: agent}\n")
    policy = tmp_path / "approvals.yaml"
    policy.write_text("version: 1\napprover: boss\ncosign_effects: [payment]\nprincipals:\n  own: {}\n  wa: {}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", principals)
    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH", policy)
    # Real time: events written by materialize carry the wall clock.
    e.clock.now = int(time.time() * 1000)
    yield e
    e.close()


def _stale_copy(env, name: str) -> Path:
    """Another host's git copy of the space, taken now and never pulled again."""
    space = env.tmp / f"{name}-copy"
    (space / ".datacore" / "events").mkdir(parents=True)
    T.git_pull(env.hub_dir, space)
    return space


def _state(space, events):
    return H.authoritative_state(space, events, registry_path=None)


# ── the hub's read route (library side of GET /ledger/hub/{space}/events) ───

def test_hub_events_answers_admitted_task_events_after_the_named_heads(env):
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    e1, _ = own.append("item.create", {"id": "t1", "title": "one"})
    e2, _ = own.append("item.create", {"id": "t2", "title": "two"})
    own.append("agent.checkin", {"agent": "own", "host": "h", "interval_s": 1800, "ts": env.clock()})

    full = h.events({})
    chains = {r["chain"] for r in full["events"]}
    assert "own" in chains
    assert not any(c.endswith(".telemetry") for c in chains), "task logs only, no telemetry"
    hashes = [r["event"]["hash"] for r in full["events"] if r["chain"] == "own"]
    assert hashes == [e1.hash, e2.hash]

    after = h.events({"own": e1.seq})
    assert [r["event"]["hash"] for r in after["events"] if r["chain"] == "own"] == [e2.hash]
    assert full["space"] == H.load_config(env.hub_dir).space_id


def test_local_transport_events_and_http_transport_url(env, monkeypatch):
    h = env.hub()
    assert "events" in H.LocalTransport(h).events({})
    t = H.HttpTransport("http://hub.invalid:1", "tok", "2-datacore", timeout_s=3)
    seen = {}
    monkeypatch.setattr(t, "_call", lambda method, path, body=None: seen.update(m=method, p=path) or {"events": []})
    t.events({"own": 4})
    assert seen["m"] == "GET"
    assert seen["p"].startswith("/ledger/hub/2-datacore/events?since=")
    import urllib.parse
    q = urllib.parse.parse_qs(urllib.parse.urlparse(seen["p"]).query)
    assert json.loads(q["since"][0]) == {"own": 4}


# ── (a) hub first, git when it does not answer ──────────────────────────────

def test_reader_takes_the_hubs_admitted_log_when_it_answers(env):
    h = env.hub()
    reader = _stale_copy(env, "wa")
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t1", "title": "made after the reader's last pull", "assignee": "wa"})

    events, source = H.task_events_hub_first(reader, H.LocalTransport(h))
    assert source == "hub"
    assert "t1" in _state(reader, events).items
    # The git copy alone does not have it: the hub is what answered.
    assert "t1" not in _state(reader, read_task_events(reader)).items


def test_reader_falls_back_to_the_git_copy_when_the_hub_is_down(env):
    h = env.hub()
    reader = _stale_copy(env, "wa")
    t = H.LocalTransport(h)
    t.down = True
    events, source = H.task_events_hub_first(reader, t)
    assert source == "git"
    assert [e.hash for e in events] == [e.hash for e in read_task_events(reader)]


def test_reader_falls_back_when_no_hub_is_named_or_the_space_has_none(env, tmp_path):
    reader = _stale_copy(env, "wa")
    events, source = H.task_events_hub_first(reader, None)
    assert source == "git" and [e.hash for e in events] == [e.hash for e in read_task_events(reader)]

    plain = tmp_path / "plain"
    (plain / ".datacore" / "events").mkdir(parents=True)

    class Never:
        def events(self, since):
            raise AssertionError("a space without a hub never asks one")

    assert H.task_events_hub_first(plain, Never()) == ([], "git")


def test_a_hub_answer_is_admitted_exactly_like_git_never_trusted_more(env):
    """A forged or tampered event in the hub's answer is refused by the same
    admission rule the git copy goes through: a hub answer adds no trust."""
    h = env.hub()
    reader = _stale_copy(env, "wa")
    own = env.writer("own", H.LocalTransport(h))
    real, _ = own.append("item.create", {"id": "t1", "title": "real"})

    class Forging(H.LocalTransport):
        def events(self, since):
            ans = super().events(since)
            out = []
            for r in ans["events"]:
                if r["event"]["hash"] == real.hash:
                    r = {**r, "event": {**r["event"], "payload": {"id": "t1", "title": "FORGED"}}}
                out.append(r)
            # A whole chain the hub made up, unsigned, by a writer nobody knows.
            out.append({"chain": "evil", "event": {"seq": 0, "hlc": real.hlc, "actor": "evil",
                                                   "type": "item.create", "payload": {"id": "t9"},
                                                   "prev": "GENESIS", "hash": "0" * 64, "sig": ""}})
            return {**ans, "events": out}

    events, source = H.task_events_hub_first(reader, Forging(h))
    state = _state(reader, events)
    assert "t9" not in state.items, "an unsigned event from the hub is not admitted"
    assert "t1" not in state.items or state.items["t1"].payload.get("title") != "FORGED"


def test_a_hub_event_that_disagrees_with_the_git_copy_does_not_replace_it(env):
    """The local copy holds own's seq 0; a hub answering a different event at
    that seq is not merged over it (the git copy is never overridden)."""
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    real, _ = own.append("item.create", {"id": "t1", "title": "real"})
    reader = _stale_copy(env, "wa")       # holds `real`

    class Other(H.LocalTransport):
        def events(self, since):
            fake = {**json.loads(to_line(real)), "payload": {"id": "t1", "title": "other"}}
            return {"events": [{"chain": "own", "event": fake}]}

    events, _ = H.task_events_hub_first(reader, Other(h))
    mine = [e for e in events if (getattr(e, "log", None) or e.actor) == "own" and e.seq == real.seq]
    assert [e.hash for e in mine] == [real.hash]


def test_ledger_claim_names_where_it_read_from(env, monkeypatch):
    import ledger_claim
    h = env.hub()
    reader = _stale_copy(env, "wa")
    out = io.StringIO()
    with redirect_stdout(out):
        assert ledger_claim.main(["--space", str(reader), "--actor", "wa"], transport=H.LocalTransport(h)) == 0
    assert "read from: hub" in out.getvalue()
    out = io.StringIO()
    with redirect_stdout(out):
        assert ledger_claim.main(["--space", str(reader), "--actor", "wa"]) == 0
    assert "read from: git copy" in out.getvalue()


# ── (c) a daytime task is claimable on the claimer's next tick, no publish ──

def test_a_daytime_delegation_is_claimable_on_the_next_tick_with_no_publish(env):
    import ledger_claim
    from briefing.actions import materialize
    h = env.hub()
    claimer = _stale_copy(env, "wa")          # the claimer's git copy: never pulled again
    daytime = _stale_copy(env, "own")         # the daytime host that delegates

    res = materialize([{"text": "Daytime: check the backups", "assignee": "wa",
                        "check": "true"}], daytime, "own", transport=H.LocalTransport(h))
    assert len(res.created) == 1 and not res.blocked
    iid = res.created[0].payload["id"]
    assert res.hub == "sent"
    # The hub holds it: no git publish happened between the two hosts.
    assert any(r["event"]["payload"].get("id") == iid for r in h.events({})["events"])

    out = io.StringIO()
    with redirect_stdout(out):
        assert ledger_claim.main(["--space", str(claimer), "--actor", "wa"], transport=H.LocalTransport(h)) == 0
    text = out.getvalue()
    assert "read from: hub" in text
    assert "would claim" in text and "Daytime: check the backups" in text, text

    # Without the hub the same tick on the same copy sees nothing: the hub path
    # is what removed the publish step.
    out = io.StringIO()
    with redirect_stdout(out):
        ledger_claim.main(["--space", str(claimer), "--actor", "wa"])
    assert "would claim" not in out.getvalue()


def test_materialize_with_the_hub_down_still_creates_locally(env):
    from briefing.actions import materialize
    h = env.hub()
    daytime = _stale_copy(env, "own")
    t = H.LocalTransport(h)
    t.down = True
    res = materialize([{"text": "offline delegation", "assignee": "wa"}], daytime, "own", transport=t)
    assert len(res.created) == 1
    assert res.hub == "buffered"
    assert any(e.payload.get("id") == res.created[0].payload["id"] for e in read_task_events(daytime))
