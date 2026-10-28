"""AGT-12: Winston delegates a task to an agent and the agent does it. The record
is in the ledger.

Evidence (2026-10-26/27): Winston created a ledger item for the communications
agent in the firm space -- post P1 of an approved X batch, with a check that the
post URL is under the order's `## Result`. The agent's claim loop did not watch
that space; once it did, the item was claimed with route "research" ("classifier
unavailable; default route"), the agent was framed to "find and summarise" and
reported instead of posting, and two attempts were released with "no post URL
yet" before a third completed it.

Kind: deterministic + production.

  * deterministic: a scratch fleet (delegation_drill's fixture, neutral names:
    agent-b delegates to agent-a). The real gate creates the item, the real claim
    loop (`ledger_claim.main`) claims, runs and checks it. The model is replaced
    by a LITERAL agent: it does what its framing tells it -- framed to
    investigate, find, assess or report, it reports and changes nothing (what the
    real agent did); framed to carry the task out, it does it. The host has no
    nightshift classifier, as on the agent host in the evidence. Two items: one
    written the way the chief of staff wrote the real one (title, assignee,
    check, no route) and one that declares route "do". Each must end completed by
    its assignee, with its check and result commit in the ledger and the work
    actually in the space.
  * production (read-only): in the firm and system spaces, as published on
    origin (a fresh shallow clone in a temp dir; the live working copies are not
    touched), every item the chief of staff created for another agent in the
    last 7 days is, 24 h after it could start (creation, or its not_before), done
    by that agent: completed or verified, owner = the assignee, its check
    recorded on the completion. Could not tell (no clone, no fold) is red.

Seeded failure: the real item b2126671e58124e1 (route "research", released twice
"no post URL"). Verified against the literal agent: the no-route item is red
today because the claim loop frames it as research.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

RESULT_LINE = "- P1: https://example.invalid/status/1"
ORDER = "order.md"
CHECK = f"grep -qxF -- '{RESULT_LINE}' {ORDER}"
# How the real item read: do one concrete thing, then record its result.
TITLE = (f"Post P1 of the order now: post the approved text, then add the line "
         f"'{RESULT_LINE}' under '## Result' in {ORDER} and push.")

#: A framing that tells the agent to look into something rather than do it.
INVESTIGATE = re.compile(r"\b(investigate|find and summari[sz]e|assess|report (position|what))", re.I)


def _literal_agent():
    """Register the literal agent and return its name (after the fleet is set up)."""
    from executors.base import Executor, register

    @register
    class LiteralAgent(Executor):
        name = "agt12-literal"

        def _invoke(self, prompt: str, timeout_s: int) -> tuple[str, int]:
            self._execution_env()                 # the real claim guard
            framing, _, rest = prompt.partition("\n\nTask:")
            cwd = Path(self._cwd) if self._cwd else Path.cwd()
            m = re.search(r"add the line '([^']+)' under '## Result' in (\S+?) and push", rest)
            if INVESTIGATE.search(framing) or not m:
                return ("Here is what I found: the order asks for P1 to be posted and its URL "
                        "recorded under ## Result. Nothing has been posted yet."), 0
            line, name = m.groups()
            path = cwd / name
            path.write_text(path.read_text() + line + "\n")
            for args in (("add", name), ("commit", "-qm", "P1 posted")):
                subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, timeout=60)
            return f"Posted P1; recorded {line}", 0

    return LiteralAgent.name


@pytest.fixture
def fleet(monkeypatch):
    import actor_identity
    import ledger.policy
    from delegation_drill import DelegationDrill
    from ledger_chaos_drill import scratch_fleet
    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH", ledger.policy.DEFAULT_POLICY_PATH)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", actor_identity.PRINCIPALS)
    # The agent host in the evidence has no nightshift classifier.
    monkeypatch.setitem(sys.modules, "classifier", None)
    saved = dict(os.environ)
    try:
        with scratch_fleet(prefix="agt12-") as root:
            drill = DelegationDrill(root)
            space = drill.delegation_space("firm")
            (space / ORDER).write_text("# Order\n\nPost P1 now.\n\n## Result\n")
            drill.git(space, "add", ORDER)
            drill.git(space, "commit", "-qm", "the order")
            os.environ["DATACORE_EXECUTOR"] = _literal_agent()
            yield space, drill
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _delegated_and_run(space, drill, iid, **extra):
    from ledger.log import read_events
    drill.delegate(space, by="agent-b", to="agent-a", id=iid, title=TITLE, check=CHECK, **extra)
    out = drill.dispatch(space, "agent-a")
    it = drill.item(space, iid)
    evs = [e for e in read_events(space) if (e.payload or {}).get("id") == iid]
    claims = [e for e in evs if e.type == "item.claim"]
    done = [e for e in evs if e.type == "item.complete"]
    route = (claims[-1].payload or {}).get("route") if claims else None
    head = subprocess.run(["git", "-C", str(space), "show", f"HEAD:{ORDER}"],
                          capture_output=True, text=True).stdout
    problems = []
    if it is None or it.status not in ("completed", "verified"):
        problems.append(f"the item is {getattr(it, 'status', 'missing')!r}, not done")
    if it is not None and it.owner != "agent-a":
        problems.append(f"it is owned by {it.owner!r}, not the agent it was given to")
    if not done or not (done[-1].payload or {}).get("check") or not (done[-1].payload or {}).get("artifact_commit"):
        problems.append("the ledger has no completion carrying its check and result commit")
    if RESULT_LINE not in head:
        problems.append("the work is not in the space (no result line committed)")
    assert not problems, (
        f"a task delegated to an agent was not done: {'; '.join(problems)}. The agent's loop "
        f"claimed it with route {route!r}" + (" -- it was framed to investigate and report, "
                                             "not to carry the task out" if route != "do" else "")
        + f". Loop output: {' '.join(out.split())[:400]}")


def test_a_task_delegated_as_the_chief_of_staff_writes_it_is_done(fleet):
    space, drill = fleet
    _delegated_and_run(space, drill, "p1-as-written")


def test_a_task_delegated_with_route_do_is_done(fleet):
    space, drill = fleet
    _delegated_and_run(space, drill, "p1-route-do", route="do")


# ── production: what the chief of staff delegated in the last week got done ────

WINDOW_DAYS = 7
GRACE_H = 24


def _published_copy(role: str, dest: Path) -> Path:
    import spaces
    folder = spaces.space_for(role, root=ROOT)
    assert folder, f"could not tell: no space holds the {role!r} role"
    url = subprocess.run(["git", "-C", str(ROOT / folder), "remote", "get-url", "origin"],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    assert url, f"could not tell: the {role} space has no origin to read its published ledger from"
    # The machine's own git configuration carries how it reaches origin; the suite's
    # hermetic git config is lifted for this one read-only clone, hooks off.
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")}
    r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "clone", "-q", "--depth", "1",
                        url, str(dest / folder)], capture_output=True, text=True, timeout=180, env=env)
    assert r.returncode == 0, f"could not tell: the {role} space's ledger could not be read ({r.stderr.strip()[-200:]})"
    return dest / folder


def _start_ms(create_ms: int, not_before) -> int | None:
    if not not_before:
        return create_ms
    from datetime import datetime
    try:
        nb = datetime.fromisoformat(str(not_before))
    except ValueError:
        return None
    if nb.tzinfo is None:
        return None
    return max(create_ms, int(nb.timestamp() * 1000))


@pytest.mark.production
def test_what_the_chief_of_staff_delegated_this_week_was_done_by_the_agent(tmp_path, monkeypatch):
    import actor_identity
    from ledger import keys
    from ledger.hub import authoritative_events, authoritative_state
    from ledger.log import read_events

    # Read as the install's own reader does: the suite points the key module at an
    # empty root, against which every hub's signed records look forged. Nothing
    # below appends or signs.
    monkeypatch.setattr(keys, "DATACORE_ROOT", ROOT)
    monkeypatch.setattr(keys, "DEFAULT_KEYS_DIR", ROOT / ".datacore" / "keys")
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", ROOT / ".datacore" / "keys" / "registry.yaml")

    ps = actor_identity.principals()
    chiefs = [n for n, p in ps.items() if str(p.get("role") or "").lower() == "chief of staff"]
    assert len(chiefs) == 1, f"could not tell: {len(chiefs)} principals hold the chief-of-staff role"
    chief = chiefs[0]
    now = int(time.time() * 1000)
    since = now - WINDOW_DAYS * 86_400_000
    bad, seen = [], 0
    for role in ("firm", "system"):
        space = _published_copy(role, tmp_path)
        events, _ = authoritative_events(space, read_events(space))
        state = authoritative_state(space, events)
        for ev in events:
            p = ev.payload or {}
            if ev.type != "item.create" or p.get("org") or not p.get("assignee"):
                continue
            if actor_identity.principal_of(ev.actor)[0] != chief:
                continue
            to, entry = actor_identity.principal_of(str(p["assignee"]))
            if to == chief or (entry or {}).get("kind") != "agent":
                continue
            created = int(str(ev.hlc).split(".")[0])
            if created < since:
                continue
            seen += 1
            it = state.items.get(p.get("id"))
            start = _start_ms(created, p.get("not_before"))
            age_h = (now - (start or created)) / 3_600_000
            label = (f"{role} space item {p.get('id')} for {to} "
                     f"({str(p.get('title') or '')[:60]!r})")
            if it is None:
                bad.append(f"{label}: created but not in the folded ledger (could not tell)")
                continue
            if start is None:
                bad.append(f"{label}: its start time {p.get('not_before')!r} cannot be read")
                continue
            if start > now or age_h <= GRACE_H:
                continue                          # not yet due, or still within its day
            done_by = actor_identity.principal_of(str(it.owner or ""))[0]
            completions = [e for e in events if e.type == "item.complete"
                           and (e.payload or {}).get("id") == it.id]
            checked = any((e.payload or {}).get("check") for e in completions)
            if it.status in ("completed", "verified") and done_by == to and checked:
                continue
            bad.append(f"{label}: {it.status}"
                       + (f" ({it.closed_kind})" if it.closed_kind else "")
                       + f", owner {it.owner or 'nobody'}, {age_h:.0f} h after it could start"
                       + ("" if not completions or checked else ", completed without its check"))
    assert not bad, (f"the chief of staff delegated work to agents that was not done within "
                     f"{GRACE_H} h ({len(bad)} of {seen} in the last {WINDOW_DAYS} days): " + " | ".join(bad))
