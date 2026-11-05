#!/usr/bin/env python3
"""Winston catches delegation gaps: delegated work that should have happened and did not.

Why (owner, 2026-11-03): a delegated task whose agent never runs leaves no
failure and no record. If Data's machine is down, nothing is attempted, so
nothing fails. And only the dispatcher read a task's start time. Nothing asked
"should this have happened by now?", and nothing made Winston read results.

Across every ledger space (`ledger.union.ledger_spaces`, each folded through
`authoritative_state`), a delegation (an item that is not an org mirror) is a
gap when it is:

  not_picked_up  addressed to an agent (or to a runner) and still `created`
                 GRACE after its start time (`not_before`, else its creation);
  stuck          `claimed` and not finished STUCK after the claim;
  gave_up        dismissed by the dispatcher after three failed attempts;
  no_assignee    addressed to nobody, so no dispatcher will ever take it.

A task addressed to a person (principals.yaml kind: human) is the owner's
list, not a gap.

BOUNDARY: it reads; it never claims, releases, dismisses or writes any event.
The judge is not the worker: it reads the ledger, not the agents' own reports.

    delegation_watch.py check    [--root ~/Data] [--dry-run] [--json]
    delegation_watch.py briefing [--root ~/Data]          # one line for /today
    delegation_watch.py who      [--root ~/Data] [--json] # who has what, across spaces

`check` sends through this host's alert route (job_verify._send_command, as
agent_presence_check does), one message per cycle for the NEW gaps and one for
gaps that cleared. State: ~/.datacore/state/delegation-watch-alerts.json. It
writes the full list to ~/.datacore/state/delegation-watch.json for the job
checker and the briefing. Exit 0 unless the read failed for a space (1).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

STATE_DIR = Path.home() / ".datacore" / "state"
DEFAULT_STATE = STATE_DIR / "delegation-watch-alerts.json"
DEFAULT_ARTIFACT = STATE_DIR / "delegation-watch.json"
GRACE_MS = 2 * 3600 * 1000
STUCK_MS = 24 * 3600 * 1000
GAVE_UP_MS = 7 * 24 * 3600 * 1000
GAVE_UP = "gave up after"
LABEL = {"not_picked_up": "not picked up", "stuck": "claimed, not finished",
         "gave_up": "given up after 3 failures", "no_assignee": "addressed to nobody"}


def _hlc_ms(text) -> int | None:
    try:
        return int(str(text).split(".", 1)[0])
    except (TypeError, ValueError):
        return None


def _event_ms(item, verb: str) -> int | None:
    """When the LAST applied `verb` (item.create, item.claim) happened, from the item's history."""
    for line in reversed(getattr(item, "history", None) or []):
        if f" {verb}: applied" in line:
            return _hlc_ms(line.split(" ", 1)[0])
    return None


def _start_ms(item) -> int | None:
    nb = (item.payload or {}).get("not_before")
    if nb:
        try:
            t = datetime.fromisoformat(str(nb))
        except ValueError:
            return None
        if t.tzinfo is None:
            return None
        return int(t.timestamp() * 1000)
    return _event_ms(item, "item.create")


def _default_kind_of(name):
    from actor_identity import principal_of
    try:
        return (principal_of(str(name))[1] or {}).get("kind")
    except Exception:  # noqa: BLE001 -- an unknown name is not an agent
        return None


def _hours(ms: int) -> str:
    h = ms / 3_600_000
    return f"{h:.0f} h" if h < 48 else f"{h / 24:.0f} days"


def gaps_in(space: str, state, *, now_ms: int, kind_of: Callable = _default_kind_of,
            grace_ms: int = GRACE_MS, stuck_ms: int = STUCK_MS, gave_up_ms: int = GAVE_UP_MS) -> list[dict]:
    out = []
    for iid in sorted(state.items):
        it = state.items[iid]
        p = it.payload or {}
        if p.get("org"):
            continue                      # an org mirror, not a delegation
        assignee, runner = p.get("assignee"), p.get("runner")
        if assignee and kind_of(assignee) == "human" and not runner:
            continue                      # the owner's own list
        base = {"space": space, "id": iid, "title": it.title, "assignee": assignee or runner}
        if it.status == "created":
            if not assignee and not runner:
                out.append({**base, "kind": "no_assignee", "since_ms": _event_ms(it, "item.create"),
                            "detail": "no assignee: no dispatcher will take it"})
                continue
            start = _start_ms(it)
            if start is not None and now_ms - start > grace_ms:
                out.append({**base, "kind": "not_picked_up", "since_ms": start,
                            "detail": f"due {_hours(now_ms - start)} ago, never picked up"})
        elif it.status == "claimed":
            claimed = _event_ms(it, "item.claim")
            if claimed is not None and now_ms - claimed > stuck_ms:
                out.append({**base, "kind": "stuck", "since_ms": claimed,
                            "detail": f"claimed {_hours(now_ms - claimed)} ago, not finished"})
        elif it.status == "dismissed" and str(it.closed_reason or "").startswith(GAVE_UP):
            closed = _hlc_ms(it.closed_at)
            if closed is None or now_ms - closed <= gave_up_ms:
                out.append({**base, "kind": "gave_up", "since_ms": closed,
                            "detail": str(it.closed_reason)[:300]})
    return out


def states(root, *, fold=None) -> tuple[dict, dict]:
    """({space name: folded state}, {space name: why its read failed}) for every ledger space."""
    from ledger.union import ledger_spaces
    if fold is None:
        from ledger.hub import authoritative_state as fold
    out, errors = {}, {}
    for space in ledger_spaces(root):
        try:
            out[space.name] = fold(space)
        except Exception as exc:  # noqa: BLE001 -- named, never skipped silently
            errors[space.name] = f"{type(exc).__name__}: {exc}"
    return out, errors


def scan(root, *, now_ms: int | None = None, kind_of: Callable = _default_kind_of, fold=None,
         **limits) -> tuple[list[dict], dict]:
    """(gaps across every ledger space, {space: why its read failed})."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    folded, errors = states(root, fold=fold)
    found = []
    for name, state in folded.items():
        found += gaps_in(name, state, now_ms=now, kind_of=kind_of, **limits)
    return found, errors


# ── Who has what (Winston path step 3, owner goal 2026-11-05) ─────────────────
# Winston's read tool: every open delegation across spaces, by assignee and
# status. Same read and the same exclusions as the gap check above (org mirrors
# are not delegations); it reads and never writes.

OPEN = ("created", "claimed")


def who_has_what(folded: dict, *, now_ms: int) -> list[dict]:
    rows = []
    for space in sorted(folded):
        for iid in sorted(folded[space].items):
            it = folded[space].items[iid]
            p = it.payload or {}
            if p.get("org") or it.status not in OPEN:
                continue
            since = _event_ms(it, "item.claim") if it.status == "claimed" else _start_ms(it)
            rows.append({"assignee": p.get("assignee") or p.get("runner") or "(nobody)",
                         "status": it.status, "space": space, "id": iid, "title": it.title,
                         "since_ms": since,
                         "age": ("?" if since is None else _hours(now_ms - since) if since <= now_ms
                                 else f"due in {_hours(since - now_ms)}")})
    return rows


def who_text(rows: list[dict], errors: dict, per_person: int = 8) -> str:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["assignee"], []).append(r)
    lines = []
    for who in sorted(by, key=lambda w: (-len(by[w]), w)):
        mine = sorted(by[who], key=lambda r: (r["status"] != "claimed", -(r["since_ms"] or 0)))
        claimed = sum(r["status"] == "claimed" for r in mine)
        lines.append(f"{who}: {len(mine)} open ({claimed} claimed, {len(mine) - claimed} waiting)")
        for r in mine[:per_person]:
            state = "working" if r["status"] == "claimed" else "waiting"
            age = r["age"] if r["age"].startswith("due in") else f"{state} {r['age']}"
            lines.append(f"  - {age}: {r['title'][:90]} [{r['space']} {r['id']}]")
        if len(mine) > per_person:
            lines.append(f"  … and {len(mine) - per_person} more")
    if not lines:
        lines.append("Nobody has open delegated work.")
    for space, why in sorted(errors.items()):
        lines.append(f"{space} could not be read ({why[:120]}): its work is not in this list.")
    return "\n".join(lines)


def _load(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text())
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save(path: Path, data) -> None:
    from file_utils import atomic_write_text
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(Path(path), json.dumps(data, indent=2, sort_keys=True) + "\n")


def _key(g) -> str:
    return f"{g['space']}/{g['id']}"


def _message(new: list[dict], limit: int = 12) -> str:
    by = {}
    for g in new:
        by.setdefault(g["kind"], []).append(g)
    head = ", ".join(f"{len(v)} {LABEL[k]}" for k, v in sorted(by.items()))
    lines = [f"⚠️ Delegation gaps: {len(new)} new ({head})."]
    for g in new[:limit]:
        lines.append(f"- {g['title'][:80]} [{g['space']}, {g['assignee'] or 'nobody'}]: {g['detail'][:160]}")
    if len(new) > limit:
        lines.append(f"…and {len(new) - limit} more in {DEFAULT_ARTIFACT}")
    return "\n".join(lines)[:3400]


def _send_default(text: str) -> bool:
    import job_verify
    return job_verify._send_command(text)


def alert(found: list[dict], *, state_path=DEFAULT_STATE, send: Callable[[str], bool] | None = None) -> dict:
    """One message for the new gaps, one for those that cleared. {alerted, cleared, unsent}."""
    send = send or _send_default
    st = _load(state_path)
    open_ = st.get("open") or {}
    now = {_key(g): g for g in found}
    new = [g for k, g in now.items() if k not in open_]
    gone = [k for k in open_ if k not in now]
    alerted, cleared, unsent = [], [], []
    if new:
        if send(_message(new)):
            alerted = [_key(g) for g in new]
            for g in new:
                open_[_key(g)] = {"kind": g["kind"], "title": g["title"]}
        else:
            unsent += [_key(g) for g in new]
    if gone:
        names = "; ".join(open_[k]["title"][:60] for k in gone[:10])
        if send(f"✅ Delegation gaps cleared: {len(gone)} ({names}{'…' if len(gone) > 10 else ''})."):
            cleared = gone
            for k in gone:
                del open_[k]
        else:
            unsent += gone
    _save(state_path, {"open": open_})
    return {"alerted": alerted, "cleared": cleared, "unsent": unsent}


def briefing_line(found: list[dict], errors: dict) -> str:
    if errors:
        return f"Delegation gaps: could not read {', '.join(sorted(errors))}, so the count is incomplete."
    if not found:
        return "Delegation gaps: none: every delegated task is done, in progress within a day, or not yet due."
    by = {}
    for g in found:
        by[g["kind"]] = by.get(g["kind"], 0) + 1
    return "Delegation gaps: " + ", ".join(f"{n} {LABEL[k]}" for k, n in sorted(by.items())) + "."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("check", "briefing", "who"))
    ap.add_argument("--root", type=Path, default=Path.home() / "Data")
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    ap.add_argument("--dry-run", action="store_true", help="print what it would send; no file is written")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "who":
        folded, errors = states(a.root)
        rows = who_has_what(folded, now_ms=int(time.time() * 1000))
        print(json.dumps({"errors": errors, "open": rows}, sort_keys=True) if a.json
              else who_text(rows, errors))
        return 1 if errors else 0
    found, errors = scan(a.root)
    if a.cmd == "briefing":
        print(briefing_line(found, errors))
        return 1 if errors else 0
    if a.dry_run:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            st = Path(tmp) / "s.json"
            if a.state.exists():
                st.write_text(a.state.read_text())
            result = alert(found, state_path=st, send=lambda t: print("WOULD SEND:\n" + t) or True)
    else:
        _save(a.artifact, {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "errors": errors, "gaps": found, "line": briefing_line(found, errors)})
        result = alert(found, state_path=a.state)
    summary = {"gaps": len(found), "errors": errors, **{k: len(v) for k, v in result.items()}}
    print(json.dumps({**summary, **result, "list": found} if a.json else summary, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
