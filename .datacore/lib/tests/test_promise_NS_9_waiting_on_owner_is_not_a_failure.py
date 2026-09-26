"""NS-9 (follow-up): a repair waiting on the owner's merge is "waiting for you", never "gave up".

Promise NS-9: a job that keeps failing goes to Miles as a repair pull request
automatically; I am told only if he cannot take it or gives up after three tries.
Owner, 2026-09-25: an agent opens the pull request and stops; the owner merges.

Defect this pins (2026-09-26): the repair item's done-check (`jobs/fix_check.py
--stage merged`) exits 1 on an OPEN pull request -- "waiting for you" -- and
`ledger_claim` treats every check that does not pass as a failed attempt. So a
repair Miles finished correctly (PR open, waiting on the owner) is released,
re-dispatched to the agent, and dead-lettered after three "failed attempts",
then escalated as "miles gave up". Waiting on the owner needs its own exit and
its own state.

Kind: deterministic. Runs the real `jobs/fix_check.py` against a fake `gh` on
PATH, and the real `ledger_claim` dispatcher in a scratch fleet (the drill's
local executor stands in for the model) with the real `jobs/autofix.py`
reading the result.

Seeded failure: a repair whose check says "waiting on the owner" on every
tick. The promise holds when the check's exit says so distinctly, the item is
never dead-lettered nor re-run by the agent while it waits, the reports say
"waiting for you" and never "gave up", and the item completes by itself once
the owner has merged. Control: a check that genuinely fails still gives up
after three tries.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
for p in (LIB, LIB / "jobs"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import fix_check  # noqa: E402

FIX_CHECK = LIB / "jobs" / "fix_check.py"
AUTOFIX = LIB / "jobs" / "autofix.py"
ITEM = "autofix-box-x-20260926"


def _waiting_code():
    code = getattr(fix_check, "WAITING_ON_OWNER", None)
    assert isinstance(code, int) and code not in (0, 1, 2), (
        "fix_check has no distinct exit for 'waiting on the owner' (WAITING_ON_OWNER): an open "
        "pull request exits 1, the same code as a failed repair")
    return code


# ── the done-check says "waiting on the owner" with its own exit ──────────────

def _fake_gh(tmp_path, state):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "prs.json").write_text(json.dumps([{
        "number": 7, "title": f"fix box-x ({ITEM})", "body": "", "state": state,
        "mergedAt": "2026-09-26T10:00:00Z" if state == "MERGED" else None,
        "url": "https://github.com/o/r/pull/7"}]))
    (b / "files.json").write_text(json.dumps({"files": [{"path": "lib/producer.py"}]}))
    (b / "gh").write_text(f"#!/bin/bash\ncase \"$2\" in list) cat {b}/prs.json;; "
                          f"view) cat {b}/files.json;; *) exit 9;; esac\n")
    (b / "gh").chmod(0o755)
    return b


def _merged_stage(tmp_path, state):
    m = tmp_path / "manifest.yaml"
    m.write_text(yaml.safe_dump({"jobs": [{
        "name": "box-x", "machine": "box", "schedule": "0 * * * *", "cmd": "true",
        "exit_ok": [0], "on_fail": "log",
        "artifacts": [{"path": "~/x.log", "check": "regex", "arg": "^OK$"}]}]}))
    sha = fix_check.contract_sha("box-x", m)
    env = {**os.environ, "PATH": f"{_fake_gh(tmp_path, state)}:{os.environ['PATH']}"}
    return subprocess.run([sys.executable, str(FIX_CHECK), "--job", "box-x", "--machine", "box",
                           "--contract-sha", sha, "--manifest", str(m), "--stage", "merged",
                           "--item", ITEM, "--repo", "o/r"],
                          capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60)


def test_an_open_pull_request_exits_waiting_on_the_owner(tmp_path):
    code = _waiting_code()
    r = _merged_stage(tmp_path, "OPEN")
    assert r.returncode == code, (
        f"an open pull request exited {r.returncode}, not WAITING_ON_OWNER ({code}): "
        f"{(r.stdout + r.stderr).strip()[:200]}")
    assert "waiting for you" in (r.stdout + r.stderr)


def test_no_pull_request_yet_is_not_waiting_on_the_owner(tmp_path):
    """Without a PR the repair is not done by the agent: an ordinary not-yet."""
    code = _waiting_code()
    b = _fake_gh(tmp_path, "OPEN")
    (b / "prs.json").write_text("[]")
    m = tmp_path / "manifest.yaml"
    m.write_text(yaml.safe_dump({"jobs": [{
        "name": "box-x", "machine": "box", "schedule": "0 * * * *", "cmd": "true",
        "artifacts": [{"path": "~/x.log", "check": "regex", "arg": "^OK$"}]}]}))
    r = subprocess.run([sys.executable, str(FIX_CHECK), "--job", "box-x", "--machine", "box",
                        "--contract-sha", fix_check.contract_sha("box-x", m), "--manifest", str(m),
                        "--stage", "merged", "--item", ITEM, "--repo", "o/r"],
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "PATH": f"{b}:{os.environ['PATH']}"})
    assert r.returncode not in (0, code), r.stdout + r.stderr


# ── the dispatcher: waiting is not an attempt, not a re-run, not a drop ───────

@pytest.fixture
def fleet(monkeypatch):
    """A scratch fleet whose global side effects are undone after the test."""
    import actor_identity
    import ledger.policy
    from delegation_drill import DelegationDrill
    from ledger_chaos_drill import scratch_fleet
    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH", ledger.policy.DEFAULT_POLICY_PATH)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", actor_identity.PRINCIPALS)
    saved = dict(os.environ)
    try:
        with scratch_fleet(prefix="ns9w-") as root:
            drill = DelegationDrill(root)
            space = drill.delegation_space("2-datacore")     # autofix's system space
            yield root, space, drill
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _events(space, iid, kind):
    from ledger.log import read_events
    return [e for e in read_events(space) if e.type == kind and (e.payload or {}).get("id") == iid]


def _autofix(root, *args):
    r = subprocess.run([sys.executable, str(AUTOFIX), *args, "--root", str(root)],
                       capture_output=True, text=True, timeout=120, env=dict(os.environ))
    return r.stdout + r.stderr


def test_a_repair_waiting_on_the_owner_is_never_given_up(fleet, tmp_path, monkeypatch):
    from jobs import autofix
    from ledger_claim import MAX_ATTEMPTS
    root, space, drill = fleet
    # The fix's constant when it exists; 3 otherwise, so this shows the drop itself.
    code = getattr(fix_check, "WAITING_ON_OWNER", 3)
    monkeypatch.setattr(autofix, "_acked", lambda: set())
    merged = tmp_path / "owner-merged"
    # The agent's part is done (k.txt committed, standing in for the open PR); the
    # check then answers "waiting on the owner" until the owner has merged.
    iid = drill.delegate(space, by="winston", to="miles", id=ITEM,
                         title="write K into k.txt",
                         check=f"test -f k.txt && {{ test -f {merged} || exit {code}; }}",
                         autofix=True, job="box-x", machine="box")

    outs = [drill.dispatch(space, "miles") for _ in range(MAX_ATTEMPTS + 2)]
    it = drill.item(space, iid)
    assert it.status != "dismissed", (
        f"a repair waiting on the owner's merge was dropped ({it.closed_reason!r}) -- "
        f"dispatch said: {outs[-1][:300]}")
    assert not any("DEADLETTER" in o for o in outs), outs
    assert len(_events(space, iid, "item.claim")) == 1, (
        "the agent was sent back to a repair it had finished while it waited on the owner: "
        f"{len(_events(space, iid, 'item.claim'))} claims")
    assert "waiting for you" in outs[0].lower(), outs[0][:300]

    listed = _autofix(root, "--list")
    line = next((ln for ln in listed.splitlines() if iid in ln), "")
    assert "waiting" in line.lower(), f"--list does not show the repair as waiting on me: {line!r}"
    later = autofix.escalations(root, now_ms=__import__("time").time() * 1000 + 48 * 3600_000)
    assert not any("gave up" in r or "nobody is completing" in r for r in later), later

    # The owner merges: the next tick completes it, without the agent.
    merged.write_text("merged\n")
    out = drill.dispatch(space, "miles")
    it = drill.item(space, iid)
    assert it.status in ("done", "completed") or it.closed_kind == "done", (
        f"after the owner merged, the waiting repair did not complete: {it.status}; {out[:300]}")
    assert len(_events(space, iid, "item.claim")) <= 2


def test_a_repair_whose_check_fails_still_gives_up_after_three(fleet, monkeypatch):
    """Control: waiting is not a loophole -- a genuinely failing check still gives up."""
    from jobs import autofix
    from ledger_claim import MAX_ATTEMPTS
    root, space, drill = fleet
    monkeypatch.setattr(autofix, "_acked", lambda: set())
    iid = drill.delegate(space, by="winston", to="miles", id=ITEM,
                         title="write K into k.txt", check="test -f k.txt && exit 1",
                         autofix=True, job="box-x", machine="box")
    outs = [drill.dispatch(space, "miles") for _ in range(MAX_ATTEMPTS + 1)]
    assert drill.item(space, iid).status == "dismissed", outs[-1][:300]
    assert any("DEADLETTER" in o for o in outs)
    assert any("gave up" in r for r in autofix.escalations(root)), autofix.escalations(root)
