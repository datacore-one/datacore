"""Agent check-ins through the hub (task 53c26c5c-f205-45e0-ac55-7b57ef68205c).

Written before the code. Why: on 2026-10-18 the 15-minute host probe (TCP 22)
said Data's machine was up all morning while the Data agent itself was stuck.
A host answering is not an agent working, so each agent now says so itself,
as a signed ledger event, and Winston's 6-hourly check reads who stopped.

One test per DONE_WHEN line:
  1. each agent (Miles, Tris, Data, Winston) sends a signed check-in event to
     the hub on its schedule;
  2. one agent's check-ins stop for longer than its interval: the next 6-hourly
     check names that agent in an alert (one cycle), and only once;
  3. its check-ins resume: the check sends a clear message;
  4. the hub is down: the check reads the git copy instead;
  5. an unsigned check-in is refused by the hub and not counted from git;
  6. the daily briefing shows one status line per agent.

Nothing is sent anywhere: the alert sender is a list the test reads. Keys are
generated in tmp dirs; the real ~/.datacore/keys is never touched.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ledger import hub as hubmod
from ledger.events import EVENT_TYPES, to_line
from ledger.log import EventLog, read_events

import agent_presence_check as check
from ledger import checkin, presence

HUB_ID = "hub-checkin-test"
GRANTOR = "hubd"
#: The space's owner, who signs the hub.enable record (ledger hub audit finding 4).
OWNER = "owner"
AGENTS = ("miles", "tris", "data", "winston")
INTERVAL = 1800          # every agent's heartbeat timer: 30 minutes
CYCLE = 6 * 3600         # Winston's check


class Clock:
    def __init__(self, start: int = 1_800_000_000_000):
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += int(seconds * 1000)


def git_pull(src: Path, dst: Path) -> None:
    """The published copy of the space reaching another machine."""
    for sub in ("events", "telemetry"):
        s = src / ".datacore" / sub
        if not s.is_dir():
            continue
        d = dst / ".datacore" / sub
        d.mkdir(parents=True, exist_ok=True)
        for f in s.glob("*.jsonl"):
            t = d / f.name
            if not t.exists() or len(f.read_bytes()) >= len(t.read_bytes()):
                shutil.copyfile(f, t)
    (dst / hubmod.CONFIG).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src / hubmod.CONFIG, dst / hubmod.CONFIG)


class Env:
    def __init__(self, tmp: Path, monkeypatch):
        import ledger.keys as keys
        self.tmp = tmp
        self.keys_dir = tmp / "keys"
        self.registry = tmp / "registry.yaml"
        monkeypatch.setattr(keys, "DATACORE_ROOT", tmp / "root")
        monkeypatch.setenv("DATACORE_ACTOR", "checkin-host")
        monkeypatch.delenv("DATACORE_SEQUENCER", raising=False)
        monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
        self.clock = Clock()
        self.hub_dir = tmp / "hub-space"
        (self.hub_dir / ".datacore" / "events").mkdir(parents=True)
        hubmod.enable(self.hub_dir, hub_id=HUB_ID, grantor=GRANTOR, owner=OWNER,
                      keys_dir=self.keys_dir, registry_path=self.registry)
        # A fixed policy, not this machine's approvals_policy.yaml (finding 12).
        from ledger.hub_policy import PolicyInputs
        self.policy = PolicyInputs(approver="boss", cosign_effects=frozenset({"payment"}))
        self.hub = hubmod.Hub(self.hub_dir, GRANTOR, keys_dir=self.keys_dir, registry_path=self.registry,
                              state_dir=tmp / "hubstate", clock=self.clock, policy=self.policy)
        self.transport = hubmod.LocalTransport(self.hub)
        # Winston's machine: its own git copy of the space.
        self.box_dir = tmp / "box-space"
        (self.box_dir / ".datacore" / "events").mkdir(parents=True)
        self.sent: list[str] = []
        self.state_file = tmp / "presence-alerts.json"

    def space_of(self, agent: str) -> Path:
        space = self.tmp / f"{agent}-space"
        if not space.exists():
            (space / ".datacore" / "events").mkdir(parents=True)
            git_pull(self.hub_dir, space)
        return space

    def checkin(self, agent: str, transport=None) -> dict:
        return checkin.send(self.space_of(agent), agent=agent, actor=agent, interval_s=INTERVAL,
                            host=f"{agent}-host", transport=transport or self.transport,
                            keys_dir=self.keys_dir, registry_path=self.registry,
                            state_dir=self.tmp / "wstate" / agent, clock=self.clock)

    def send(self, text: str) -> bool:
        self.sent.append(text)
        return True

    def run_check(self, transport="hub") -> dict:
        git_pull(self.hub_dir, self.box_dir)
        return check.run(self.box_dir, agents=AGENTS, transport=self.transport if transport == "hub" else transport,
                         state_path=self.state_file, send=self.send, now_ms=self.clock(),
                         registry_path=self.registry)


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    yield e
    e.hub.close()


def all_check_in(env):
    for a in AGENTS:
        env.checkin(a)


# ── 1. each agent sends a signed check-in to the hub ────────────────────────

def test_agent_checkin_is_a_registered_event_type():
    assert "agent.checkin" in EVENT_TYPES


def test_each_agent_sends_a_signed_checkin_to_the_hub(env):
    receipts = [env.checkin(a) for a in AGENTS]
    assert [r["status"] for r in receipts] == ["accepted"] * 4, receipts

    held = [e for e in read_events(env.hub_dir) if e.type == "agent.checkin"]
    assert sorted(e.payload["agent"] for e in held) == sorted(AGENTS)
    for e in held:
        assert e.sig and e.hub == HUB_ID
        assert set(e.payload) == {"agent", "host", "interval_s", "ts"}
        assert e.payload["interval_s"] == INTERVAL and e.payload["ts"] == env.clock()

    p = env.hub.presence(agents=AGENTS)
    assert {a: p["agents"][a]["state"] for a in AGENTS} == {a: "ok" for a in AGENTS}


def test_a_checkin_on_schedule_keeps_the_agent_ok(env):
    for _ in range(4):
        all_check_in(env)
        env.clock.advance(INTERVAL)
    assert all(v["state"] == "ok" for v in env.hub.presence(agents=AGENTS)["agents"].values())


def test_a_checkin_while_the_hub_is_down_is_kept_and_replayed(env):
    env.transport.down = True
    r = env.checkin("data")
    assert r["status"] == "buffered"
    assert not [e for e in read_events(env.hub_dir) if e.type == "agent.checkin"]
    env.transport.down = False
    env.clock.advance(INTERVAL)
    assert env.checkin("data")["status"] == "accepted"
    held = [e for e in read_events(env.hub_dir) if e.type == "agent.checkin"]
    assert len(held) == 2                       # the buffered one arrived too


# ── 2/3. stopped → named in an alert within one cycle; resumed → cleared ────

def test_a_stopped_agent_is_named_in_one_alert_within_one_cycle_and_cleared_on_resume(env):
    all_check_in(env)
    first = env.run_check()
    assert first["alerted"] == [] and env.sent == []

    # Data's check-ins stop; the other three keep their schedule for one cycle.
    for _ in range(CYCLE // INTERVAL):
        env.clock.advance(INTERVAL)
        for a in AGENTS:
            if a != "data":
                env.checkin(a)
    result = env.run_check()
    assert result["alerted"] == ["data"]
    assert len(env.sent) == 1 and "data" in env.sent[0].lower()
    for other in ("miles", "tris", "winston"):
        assert other not in env.sent[0].lower()

    # Still silent at the next cycle: no second alert for the same silence.
    env.clock.advance(CYCLE)
    for a in AGENTS:
        if a != "data":
            env.checkin(a)
    assert env.run_check()["alerted"] == [] and len(env.sent) == 1

    # Data resumes: the next check sends a clear message, once.
    env.checkin("data")
    result = env.run_check()
    assert result["cleared"] == ["data"]
    assert len(env.sent) == 2 and "data" in env.sent[1].lower()
    assert env.run_check()["cleared"] == [] and len(env.sent) == 2


def test_an_alert_that_could_not_be_sent_is_tried_again_next_cycle(env):
    all_check_in(env)
    env.clock.advance(CYCLE)
    git_pull(env.hub_dir, env.box_dir)
    failed = check.run(env.box_dir, agents=AGENTS, transport=env.transport, state_path=env.state_file,
                       send=lambda text: False, now_ms=env.clock(), registry_path=env.registry)
    assert failed["alerted"] == [] and sorted(failed["unsent"]) == sorted(AGENTS)
    assert sorted(env.run_check()["alerted"]) == sorted(AGENTS)


def test_an_agent_that_never_checked_in_is_named(env):
    for a in AGENTS:
        if a != "tris":
            env.checkin(a)
    assert env.run_check()["alerted"] == ["tris"]


# ── 4. hub down → the check reads the git copy ──────────────────────────────

def test_with_the_hub_down_the_check_reads_the_git_copy(env):
    all_check_in(env)
    env.clock.advance(CYCLE)
    for a in AGENTS:
        if a != "miles":
            env.checkin(a)
    env.transport.down = True
    result = env.run_check()
    assert result["source"] == "git"
    assert result["alerted"] == ["miles"]
    assert "git" in env.sent[0].lower()          # the alert says where it read from


def test_without_any_hub_configured_the_check_reads_the_git_copy(env):
    all_check_in(env)
    result = env.run_check(transport=None)
    assert result["source"] == "git" and result["alerted"] == []


# ── 5. unsigned check-ins are refused and never counted ─────────────────────

def _unsigned_checkin(env, agent: str):
    scratch = env.tmp / f"scratch-{agent}"
    (scratch / ".datacore" / "events").mkdir(parents=True, exist_ok=True)
    return EventLog(scratch, agent, sign=False, hub=HUB_ID).append(
        "agent.checkin", {"agent": agent, "host": "x", "interval_s": INTERVAL, "ts": env.clock()})


def test_the_hub_refuses_an_unsigned_checkin(env):
    e = _unsigned_checkin(env, "data")
    with pytest.raises(hubmod.HubRefused) as exc:
        env.hub.append({"event": json.loads(to_line(e))})
    assert exc.value.code == "unsigned"
    assert env.hub.presence(agents=AGENTS)["agents"]["data"]["state"] == "silent"


def test_an_unsigned_or_forged_checkin_in_the_git_copy_is_not_counted(env):
    all_check_in(env)
    env.clock.advance(CYCLE)
    # An unsigned check-in for Data, and one whose payload was edited after
    # signing, land in the git copy by some other route.
    git_pull(env.hub_dir, env.box_dir)
    tel = env.box_dir / ".datacore" / "telemetry"
    (tel / "data-unsigned.jsonl").write_text(to_line(_unsigned_checkin(env, "data")) + "\n")
    signed = json.loads((tel / "data.jsonl").read_text().splitlines()[0])
    signed["payload"]["ts"] = env.clock()           # moved forward; the signature no longer matches
    (tel / "data-edited.jsonl").write_text(json.dumps(signed, sort_keys=True, separators=(",", ":")) + "\n")
    p = presence.presence(read_events(env.box_dir), env.clock(), agents=AGENTS, registry_path=env.registry)
    assert p["data"]["state"] == "silent"


# ── 6. the briefing: one status line per agent ──────────────────────────────

def test_the_briefing_shows_one_status_line_per_agent(env):
    env.checkin("miles")
    env.checkin("winston")
    env.clock.advance(3 * 3600)
    env.checkin("winston")
    env.checkin("data")
    git_pull(env.hub_dir, env.box_dir)
    lines = presence.briefing_lines(
        presence.presence(read_events(env.box_dir), env.clock(), agents=AGENTS, registry_path=env.registry))
    assert len(lines) == len(AGENTS)
    for agent, line in zip(sorted(AGENTS), lines):
        assert line.lower().startswith(agent)
    by = {ln.split()[0].lower().rstrip(":"): ln for ln in lines}
    assert "ok" in by["winston"].lower() and "ok" in by["data"].lower()
    assert "silent" in by["miles"].lower()                    # 3 h on a 30-minute schedule
    assert "never" in by["tris"].lower()


def test_the_briefing_reads_through_the_same_presence_read_as_the_check(env):
    all_check_in(env)
    lines, source = check.briefing(env.box_dir, agents=AGENTS, transport=env.transport,
                                   now_ms=env.clock(), registry_path=env.registry)
    assert source == "hub" and len(lines) == 4


def test_dry_run_sends_nothing_and_leaves_the_state_alone(env, monkeypatch, capsys):
    """Added after a manual smoke run of `check` on the workstation went out
    through the real alert route: --dry-run is the safe way to look."""
    sent = env.tmp / "sent.txt"
    monkeypatch.setenv("DATACORE_ALERT_COMMAND", f"cat >> {sent}")
    monkeypatch.delenv("DATACORE_HUB_URL", raising=False)
    rc = check.main(["check", "--dry-run", "--space", str(env.box_dir), "--agents", "miles",
                     "--state", str(env.state_file)])
    assert rc == 0
    assert "WOULD SEND" in capsys.readouterr().out
    assert not sent.exists() and not env.state_file.exists()
