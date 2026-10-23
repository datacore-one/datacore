"""Each agent claims work only from its own lane, and stops when its budget for
the period is spent (task W5).

  * the lane of an item comes from what the item says about itself: an
    explicit `lane`, its `role` (the same role names as the venture roles and
    the role templates the role-pool work loads), its `:AI:<kind>:` tags, or a
    priority id the owner mapped to a lane. An item with no such
    marker is in no other agent's lane (who may take it is the dispatcher's
    rule: addressed items only);
  * the budget is a number of claims per period, counted from the ledger's
    `item.claim` events across every space, by every writer the principal owns;
  * budgets and lanes live in ONE file the owner edits,
    `.datacore/config/agent_lanes.yaml`, shipped conservative;
  * claim_gate.check_claim -- the gate every ledger claim passes -- applies it.
"""
from __future__ import annotations

import datetime as dt
import types
from pathlib import Path

import pytest
import yaml

import agent_lanes

SHIPPED = Path(__file__).resolve().parents[2] / "config" / "agent_lanes.yaml"
NOW = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc)

PRINCIPALS = """\
version: 1
principals:
  owner: {kind: human, writes_as: [mac]}
  winston: {kind: agent, writes_as: [winston]}
  miles: {kind: agent, writes_as: [miles, nightshift]}
  tris: {kind: agent, writes_as: [tris]}
  data: {kind: agent, writes_as: [data]}
"""

LANES = """\
version: 1
period: day
lanes:
  miles:
    roles: [coo, cto, dev, operator]
    tags: [technical, pm, data]
    priorities: [p-build]
    budget: {tasks: 3}
  tris:
    roles: [cio, researcher, scout]
    tags: [research]
    priorities: [p-research]
    budget: {tasks: 2}
  data:
    roles: [cmo, comms, bizdev]
    tags: [content]
    priorities: [p-voice]
    budget: {tasks: 1}
"""


@pytest.fixture
def lanes(tmp_path, monkeypatch):
    import actor_identity
    reg = tmp_path / "principals.yaml"
    reg.write_text(PRINCIPALS)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", reg)
    cfg = tmp_path / "agent_lanes.yaml"
    cfg.write_text(LANES)
    monkeypatch.setattr(agent_lanes, "DEFAULT_LANES_FILE", cfg)
    return cfg


def no_claims(*_a, **_k):
    return 0


# (item payload, the agent whose lane it is in)
ITEMS = [
    ({"id": "a", "title": "deploy the fix", "role": "cto"}, "miles"),
    ({"id": "b", "title": "fix the job", "tags": ["AI", "technical"]}, "miles"),
    ({"id": "c", "title": "size the market", "role": "researcher"}, "tris"),
    ({"id": "d", "title": "competitor scan", "tags": ["AI:research"]}, "tris"),
    ({"id": "e", "title": "release thread", "role": "cmo"}, "data"),
    ({"id": "f", "title": "newsletter", "tags": ":AI:content:"}, "data"),
    ({"id": "g", "title": "whatever", "lane": "tris"}, "tris"),
    ({"id": "h", "title": "q4 thing", "priority": "p-voice"}, "data"),
    # a role says more than the assignee: delegated research is still research
    ({"id": "i", "title": "research for miles", "role": "researcher", "assignee": "miles"}, "tris"),
]


@pytest.mark.parametrize("item, owner", ITEMS, ids=[i["id"] for i, _ in ITEMS])
@pytest.mark.parametrize("agent", ["miles", "tris", "data"])
def test_each_agent_claims_only_from_its_own_lane(lanes, agent, item, owner):
    ok, why = agent_lanes.check_lane(agent, item, now=NOW, usage=no_claims)
    assert ok == (agent == owner), f"{agent} on {item}: {why}"
    if not ok:
        assert owner in why, why          # the refusal names whose lane it is


def test_a_writer_alias_claims_in_its_principals_lane(lanes):
    assert agent_lanes.check_lane("nightshift", {"id": "x", "role": "dev"}, now=NOW, usage=no_claims)[0]
    assert not agent_lanes.check_lane("nightshift", {"id": "x", "role": "cmo"}, now=NOW, usage=no_claims)[0]


def test_an_item_with_no_lane_marker_is_in_no_other_lane(lanes):
    """Who may take an unmarked item is the dispatcher's rule (addressed items
    only); the lane refuses only what is positively in another lane."""
    assert agent_lanes.check_lane("tris", {"id": "x", "assignee": "tris"}, now=NOW, usage=no_claims)[0]
    assert agent_lanes.check_lane("tris", {"id": "x", "tags": ["AI", "megaphone"]}, now=NOW, usage=no_claims)[0]


def test_an_item_pointing_at_two_lanes_is_refused(lanes):
    ok, why = agent_lanes.check_lane("tris", {"id": "x", "tags": ["research", "content"]},
                                     now=NOW, usage=no_claims)
    assert not ok and "ambiguous" in why


def test_an_agent_without_a_lane_is_not_gated(lanes):
    assert agent_lanes.check_lane("winston", {"id": "x", "role": "cmo"}, now=NOW, usage=no_claims)[0]
    assert agent_lanes.check_lane("owner", {"id": "x", "role": "cmo"}, now=NOW, usage=no_claims)[0]


@pytest.mark.parametrize("agent, budget", [("miles", 3), ("tris", 2), ("data", 1)])
def test_each_agent_stops_when_its_budget_for_the_period_is_spent(lanes, agent, budget):
    item = {"id": "x", "assignee": agent}
    assert agent_lanes.check_lane(agent, item, now=NOW, usage=lambda *a, **k: budget - 1)[0]
    ok, why = agent_lanes.check_lane(agent, item, now=NOW, usage=lambda *a, **k: budget)
    assert not ok and "budget" in why and f"{budget}" in why


class _Ev(types.SimpleNamespace):
    pass


def _ev(actor, kind, when):
    return _Ev(actor=actor, type=kind, hlc=f"{int(when.timestamp() * 1000)}.0000.{actor}")


def test_the_budget_counts_ledger_claims_of_every_writer_across_spaces_this_period(lanes, tmp_path,
                                                                                    monkeypatch):
    """Counted from each space's writer logs: the principal's own log, its
    alias's log and the alias's run-branch log; other writers' logs, other
    event types and claims before the period are not counted."""
    import ledger.log
    root = tmp_path / "root"
    yesterday = NOW - dt.timedelta(days=1)
    logs = {
        ("1-alpha", "miles"): [_ev("miles", "item.claim", NOW), _ev("miles", "item.create", NOW),
                               _ev("miles", "item.claim", yesterday)],
        ("1-alpha", "nightshift"): [_ev("nightshift", "item.claim", NOW)],
        ("1-alpha", "tris"): [_ev("tris", "item.claim", NOW)],
        ("2-beta", "nightshift-run-2026-10-23"): [_ev("nightshift-run-2026-10-23", "item.claim",
                                                      NOW - dt.timedelta(hours=3))],
        ("2-beta", "data"): [_ev("data", "item.claim", NOW)],
    }
    for space, writer in logs:
        d = root / space / ".datacore" / "events"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{writer}.jsonl").write_text("{}\n")
    read = []

    def parse(raw, path):
        read.append(path.stem)
        return logs[(path.parents[2].name, path.stem)], len(raw)
    monkeypatch.setattr(ledger.log, "_parse_log_bytes", parse)
    assert agent_lanes.claims_in_period("miles", root=root, now=NOW) == 3
    assert "tris" not in read and "data" not in read, read
    assert agent_lanes.claims_in_period("tris", root=root, now=NOW) == 1
    ok, why = agent_lanes.check_lane("miles", {"id": "x", "assignee": "miles"}, root=root, now=NOW)
    assert not ok and "3 of 3" in why
    assert agent_lanes.check_lane("tris", {"id": "x", "assignee": "tris"}, root=root, now=NOW)[0]


def test_claim_gate_applies_the_lane_and_the_budget(lanes, tmp_path, monkeypatch):
    import claim_gate
    monkeypatch.setattr(agent_lanes, "claims_in_period", lambda *a, **k: 0)
    policy = types.SimpleNamespace(principals={"miles": {}, "tris": {}, "data": {}})
    space = tmp_path / "1-alpha"
    space.mkdir()
    ok, why = claim_gate.check_claim("miles", {"id": "x", "role": "researcher", "assignee": "miles"},
                                     policy=policy, space_dir=space)
    assert not ok and "tris" in why
    assert claim_gate.check_claim("tris", {"id": "x", "role": "researcher", "assignee": "tris"},
                                  policy=policy, space_dir=space)[0]
    monkeypatch.setattr(agent_lanes, "claims_in_period", lambda *a, **k: 2)
    ok, why = claim_gate.check_claim("tris", {"id": "x", "role": "researcher", "assignee": "tris"},
                                     policy=policy, space_dir=space)
    assert not ok and "budget" in why


def test_no_lane_file_means_no_lane_gate_and_a_broken_one_refuses(lanes, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_lanes, "DEFAULT_LANES_FILE", tmp_path / "absent.yaml")
    assert agent_lanes.check_lane("tris", {"id": "x", "role": "cmo"}, now=NOW, usage=no_claims)[0]
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nlanes:\n  tris: {budget: {tasks: -1}}\n")
    monkeypatch.setattr(agent_lanes, "DEFAULT_LANES_FILE", bad)
    ok, why = agent_lanes.check_lane("tris", {"id": "x", "assignee": "tris"}, now=NOW, usage=no_claims)
    assert not ok and "unreadable" in why


def test_one_role_belongs_to_one_lane(lanes, tmp_path, monkeypatch):
    dup = tmp_path / "dup.yaml"
    dup.write_text(LANES.replace("roles: [cio, researcher, scout]", "roles: [cio, researcher, cto]"))
    with pytest.raises(ValueError):
        agent_lanes.load_lanes(dup)


# ── the shipped file ────────────────────────────────────────────────────────

#: The venture lane roles (AGT-1): each is owned by exactly this agent.
VENTURE_LANES = {"cio": "tris", "cmo": "data", "comms": "data", "coo": "miles", "cto": "miles"}
#: Below Miles's measured 23-51 ledger claims a day (week to 2026-10-23).
CONSERVATIVE_MAX_TASKS_PER_DAY = 20


def test_the_shipped_lanes_are_one_owner_edited_file_and_conservative():
    raw = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    assert raw.get("owner_review"), "the budgets must be marked for the owner to set"
    cfg = agent_lanes.load_lanes(SHIPPED)
    assert cfg["period"] == "day"
    assert set(cfg["lanes"]) >= {"miles", "tris", "data"}
    for agent in ("miles", "tris", "data"):
        tasks = cfg["lanes"][agent]["budget"]["tasks"]
        assert isinstance(tasks, int) and 0 < tasks <= CONSERVATIVE_MAX_TASKS_PER_DAY, (agent, tasks)
    for role, agent in VENTURE_LANES.items():
        assert cfg["role_owner"].get(role) == agent, (role, cfg["role_owner"].get(role))


def test_the_shipped_lane_roles_are_real_roles():
    """Lane roles are the names the role-pool work loads from
    `.datacore/templates/roles/<role>.md`, or a venture lane role."""
    templates = SHIPPED.parents[1] / "templates" / "roles"
    cfg = agent_lanes.load_lanes(SHIPPED)
    unknown = [r for r in cfg["role_owner"]
               if r not in VENTURE_LANES and r != "cos" and not (templates / f"{r}.md").exists()]
    assert not unknown, unknown
