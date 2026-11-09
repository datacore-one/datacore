"""AGT-13: When an agent creates a task for another agent, the assigned agent picks it up
and does it. Every pair of agents allowed to hand each other work is checked round robin,
and the record is in the ledger.

Owner's words (2026-10-27): "other agents create a task and assigned agent picks it up -
we should have a round robin test".

Two parts.

Kind: deterministic (the controls). In a scratch fleet (delegation_drill's fixture, its
local executor, the real ledger_claim loop and the real policy gate), for EVERY pair the
fixture policy allows, the giver creates a self-validating task for the taker, the taker's
own loop claims it and completes it, and the completion in the ledger carries the check
that passed and the commit it passed on. Every pair the policy does not allow is refused
and leaves nothing in the ledger. The same grader then sees a round where the takers never
run their loops and must call every pair missing -- that is the proof it bites.

Kind: production (read-only). For every pair of agents the install's own policy allows,
the ledger of some space holds a round of the delegation exercise for that pair that is no
older than 8 days: created by the giver, addressed to the taker, completed by the taker
with its check passing. Red names each pair with no fresh completed round. Could-not-tell
(no policy, no agents, no spaces, an unreadable ledger) is red, never skipped.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

MARK = "delegation-fleet-exercise"   # the exercise's item marker (delegation_fleet_exercise.MARK)
FRESH_DAYS = 8
TERMINAL_DONE = ("completed", "verified")


# ── the grader both parts use ────────────────────────────────────────────────

def _principal(writer: str) -> str:
    import actor_identity
    return actor_identity.principal_of(str(writer))[0] or str(writer)


def _rounds(space: Path) -> list[dict]:
    """Every exercise round in one space's ledger, as the ledger records it.

    One row per item.create carrying the exercise marker: who created it, who it was
    for, its folded status, and the completion event (if any) with the check and commit.
    """
    from ledger.hub import authoritative_events, authoritative_state
    from ledger.log import read_events
    events, _ = authoritative_events(space, read_events(space))
    state = authoritative_state(space, events)
    creates, completes = {}, {}
    for e in events:
        p = e.payload or {}
        iid = p.get("id")
        if not isinstance(iid, str):
            continue
        if e.type == "item.create" and (p.get("drill") == MARK or iid.startswith(MARK + "-")):
            creates[iid] = e
        elif e.type == "item.complete":
            completes.setdefault(iid, []).append(e)
    rows = []
    for iid, ce in creates.items():
        it = state.items.get(iid)
        rows.append({"id": iid, "space": space, "created_by": _principal(ce.actor),
                     "for": _principal((ce.payload or {}).get("assignee") or ""),
                     "check": (ce.payload or {}).get("check"),
                     "status": getattr(it, "status", None), "created_ms": _hlc_ms(ce),
                     "completes": completes.get(iid, [])})
    return rows


def _hlc_ms(event) -> int:
    try:
        return int(str(event.hlc).split(".")[0])
    except (ValueError, AttributeError):
        return 0


def _done_round(row: dict, frm: str, to: str, since_ms: int = 0):
    """The completion event that proves `frm` handed this to `to` and `to` did it, or None."""
    if row["created_by"] != frm or row["for"] != to or row["status"] not in TERMINAL_DONE:
        return None
    if not row["check"]:
        return None
    for ev in row["completes"]:
        p = ev.payload or {}
        if (_principal(ev.actor) == to and p.get("check") == row["check"]
                and p.get("artifact_commit") and _hlc_ms(ev) >= since_ms):
            return ev
    return None


def _missing_pairs(spaces, pairs, since_ms: int = 0) -> list[tuple[str, str, str]]:
    """(giver, taker, what the ledger shows instead) for every pair with no done round."""
    rows = [r for s in spaces for r in _rounds(s)]
    missing = []
    for frm, to in pairs:
        if any(_done_round(r, frm, to, since_ms) for r in rows):
            continue
        mine = [r for r in rows if r["created_by"] == frm and r["for"] == to]
        if not mine:
            seen = "no round was ever created for this pair"
        else:
            last = max(mine, key=lambda r: r["created_ms"])
            stale = any(_done_round(r, frm, to, 0) for r in mine)
            seen = (f"latest round {last['id']} is {last['status']}"
                    + (f"; the last completed one is older than {FRESH_DAYS} days" if stale else ""))
        missing.append((frm, to, seen))
    return missing


def _allowed_pairs(policy_principals: dict, agents: set[str]) -> list[tuple[str, str]]:
    """Every (giver, taker) between two agents that the policy allows."""
    pairs = []
    for frm in sorted(agents):
        allowed = (policy_principals.get(frm) or {}).get("may_delegate_to")
        if allowed is None:
            continue
        for to in allowed:
            to = _principal(to)
            if to in agents and to != frm:
                pairs.append((frm, to))
    return pairs


# ── deterministic: the round robin in a scratch fleet ─────────────────────────

@pytest.fixture
def fleet(monkeypatch):
    import actor_identity
    import ledger.policy
    from delegation_drill import DelegationDrill, _install_local_executor
    from ledger_chaos_drill import scratch_fleet
    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH", ledger.policy.DEFAULT_POLICY_PATH)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", actor_identity.PRINCIPALS)
    saved = dict(os.environ)
    try:
        os.environ["DATACORE_LEDGER_DRILL"] = "1"
        with scratch_fleet(prefix="agt13-") as root:
            _install_local_executor()
            drill = DelegationDrill(root)
            space = drill.delegation_space("round-robin")
            yield space, drill
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _fixture_agents_and_pairs():
    from delegation_drill import POLICY, ROSTER
    agents = {n for n, p in ROSTER.items() if p["kind"] == "agent"}
    allowed = _allowed_pairs(POLICY, agents)
    forbidden = [(a, b) for a in sorted(agents) for b in sorted(agents)
                 if a != b and (a, b) not in allowed]
    humans = [n for n, p in ROSTER.items() if p["kind"] == "human"]
    forbidden += [(a, h) for a in sorted(agents) for h in humans]
    return agents, allowed, forbidden


def _hand_over(drill, space, frm: str, to: str, n: int) -> str:
    """`frm` creates a self-validating task for `to`: count the lines of a committed file.

    Per-pair input and output, and a different line count per pair, so no other pair's
    answer and no stale file can satisfy this pair's check.
    """
    src = f"rr/input-{frm}-to-{to}.txt"
    out = f"rr/{frm}-to-{to}-linecount.txt"
    (space / "rr").mkdir(exist_ok=True)
    (space / src).write_text("".join(f"line {i}\n" for i in range(1, 10 + n)))
    drill.git(space, "add", "--", src)
    drill.git(space, "commit", "-qm", f"round input {frm}->{to}", "--", src)
    check = (f'test -f {out} && test "$(wc -l < {src} | tr -d " ")" '
             f'= "$(tr -d "[:space:]" < {out})"')
    return drill.delegate(space, by=frm, to=to, id=f"{MARK}-rr-{frm}-to-{to}",
                          title=f"count lines of {src} into {out}", check=check,
                          drill=MARK, requested_by=frm)


def test_every_allowed_pair_hands_work_over_and_the_taker_does_it(fleet):
    space, drill = fleet
    agents, allowed, _ = _fixture_agents_and_pairs()
    assert len(allowed) >= 3, f"the fixture policy allows too few pairs to make a round: {allowed}"

    outputs = {}
    for n, (frm, to) in enumerate(allowed):
        _hand_over(drill, space, frm, to, n)
        outputs[(frm, to)] = drill.dispatch(space, to)      # the taker's own loop, nobody else's

    missing = _missing_pairs([space], allowed)
    assert not missing, "these pairs handed work over and it was not done, with the check passing, " \
        "on record in the ledger:\n" + "\n".join(
            f"  {f} -> {t}: {why}\n    loop said: {outputs[(f, t)][:200]!r}" for f, t, why in missing)

    # The record is in the ledger, as the ledger's own reader prints it.
    import subprocess
    proc = subprocess.run([sys.executable, str(LIB / "ledger_cli.py"), "items", "--space", str(space)],
                          capture_output=True, text=True, timeout=120, env=dict(os.environ))
    listed = {json.loads(l)["id"]: json.loads(l) for l in proc.stdout.splitlines() if l.startswith("{")}
    for frm, to in allowed:
        row = listed.get(f"{MARK}-rr-{frm}-to-{to}")
        assert row and row["status"] in TERMINAL_DONE and _principal(row["owner"] or "") == to, (
            f"the ledger listing does not show {frm} -> {to} done by {to}: {row} ({proc.stderr[-300:]})")


def test_a_pair_the_policy_does_not_allow_is_refused_and_leaves_no_record(fleet):
    from ledger.policy import PolicyError
    space, drill = fleet
    _, _, forbidden = _fixture_agents_and_pairs()
    assert forbidden, "the fixture policy forbids nothing, so refusal cannot be shown"
    let_through = []
    for n, (frm, to) in enumerate(forbidden):
        try:
            _hand_over(drill, space, frm, to, n)
        except PolicyError:
            continue
        let_through.append(f"{frm} -> {to}")
    assert not let_through, f"the policy does not allow these hand-overs, yet they were accepted: {let_through}"
    on_record = [r["id"] for r in _rounds(space)]
    assert not on_record, f"refused hand-overs still left items in the ledger: {on_record}"


def test_the_round_is_red_when_the_taker_never_runs_its_loop(fleet):
    """The grader bites: the task is created, every loop EXCEPT the taker's runs, and the
    pair is still called missing -- nobody else's loop may do it, and the round says so."""
    space, drill = fleet
    agents, allowed, _ = _fixture_agents_and_pairs()
    counted = []
    for n, (frm, to) in enumerate(allowed):
        _hand_over(drill, space, frm, to, n)
        for other in sorted(agents - {to}):
            drill.dispatch(space, other)
        if not _missing_pairs([space], [(frm, to)]):
            counted.append(f"{frm} -> {to}")
    assert not counted, f"the round counted these pairs as done although their taker never ran: {counted}"


# ── production: the install's own pairs, fresh, on record ─────────────────────

@pytest.mark.production
def test_every_pair_of_agents_allowed_to_hand_work_over_has_a_fresh_completed_round(monkeypatch):
    import actor_identity
    from ledger import keys
    from ledger.policy import load_policy
    from spaces import discover_spaces

    # Read the live ledgers as the install's own reader does: the suite points the key
    # module at an empty root, and against that every hub's signed records look forged.
    # Read-only -- nothing below appends or signs.
    root = LIB.parents[1]
    monkeypatch.setattr(keys, "DATACORE_ROOT", root)
    monkeypatch.setattr(keys, "DEFAULT_KEYS_DIR", root / ".datacore" / "keys")
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", root / ".datacore" / "keys" / "registry.yaml")

    try:
        policy = load_policy()
        roster = actor_identity.principals(actor_identity.PRINCIPALS)
    except Exception as exc:  # noqa: BLE001 -- could not tell is red
        pytest.fail(f"could not tell: the install's policy or principal registry is unreadable ({exc})")
    agents = {n for n, p in (roster or {}).items() if (p or {}).get("kind") == "agent"}
    pairs = _allowed_pairs(policy.principals or {}, agents)
    assert pairs, (f"could not tell: the install's policy allows no agent to hand work to another "
                   f"(agents in the registry: {sorted(agents)})")

    spaces = [s.path for s in discover_spaces() if (s.path / ".datacore" / "events").is_dir()]
    assert spaces, "could not tell: no space with a ledger was found"

    since_ms = int((time.time() - FRESH_DAYS * 86400) * 1000)
    readable, unreadable = [], []
    for s in spaces:
        try:
            _rounds(s)
            readable.append(s)
        except Exception as exc:  # noqa: BLE001 -- could not tell is red, and named
            unreadable.append(f"  a space's ledger ({s.name}) could not be read: {type(exc).__name__}: {exc}")
    missing = _missing_pairs(readable, pairs, since_ms)

    problems = []
    if missing:
        problems.append(
            f"{len(missing)} of {len(pairs)} pairs of agents allowed to hand each other work have no "
            f"round in the last {FRESH_DAYS} days where the giver created the task and the taker did "
            f"it with its check passing:\n" + "\n".join(f"  {f} -> {t}: {why}" for f, t, why in missing))
    if unreadable:
        problems.append("could not tell for every space (a round there would not be seen):\n"
                        + "\n".join(unreadable))
    assert not problems, "\n".join(problems)
