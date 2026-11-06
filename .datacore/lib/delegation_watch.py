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
import re
import subprocess
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


# ── Filing only where the agent listens (Winston path step 4, owner goal 2026-11-05) ──
# The smaller of the two fixes: rather than every agent watching every space,
# Winston files a delegation only in a space the assignee's dispatcher reads.
# Each agent's listening repositories are declared in principals.yaml
# (`listens_in`, by repository name: the pods name their folders differently).

def _repo_of(space_dir: Path) -> str | None:
    import subprocess
    try:
        url = subprocess.run(["git", "-C", str(space_dir), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return name[:-4] if name.endswith(".git") else (name or None)


def _entry_of(name):
    from actor_identity import principal_of
    try:
        return principal_of(str(name))[1]
    except Exception:  # noqa: BLE001 -- an unknown name is nobody's
        return None


def listening_problem(space_dir: Path, payload: dict, *, repo_of: Callable = _repo_of,
                      entry_of: Callable = _entry_of) -> str | None:
    """Why a delegation filed in `space_dir` would never be picked up, or None.

    Only a new delegation to an AGENT that declares where it listens is judged.
    The owner's own items, org mirrors, and a space whose repository cannot be
    told are left alone: this refuses only what is known to be unreachable."""
    if not isinstance(payload, dict) or payload.get("org"):
        return None
    who = payload.get("assignee") or payload.get("runner")
    entry = entry_of(who) if who else None
    if not entry or entry.get("kind") != "agent" or not entry.get("listens_in"):
        return None
    repo = repo_of(Path(space_dir))
    if not repo or repo in entry["listens_in"]:
        return None
    return (f"{who} does not listen in {Path(space_dir).name} ({repo}); its dispatcher reads "
            f"{', '.join(entry['listens_in'])}. File it there, with a pointer to where the work lives.")


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


def _send_owner(text: str) -> bool:
    """A report on the owner's OWN request goes to the owner's 1:1 chat with
    Winston, where they asked -- not the alert route, which posts only to The
    Firm group (2026-11-06: a verified request was recorded as told and never
    seen). Gaps stay alerts. False (retried next run) when it was not delivered."""
    import job_verify
    if not job_verify.WINSTON_SEND.exists():
        return False
    try:
        r = subprocess.run([sys.executable, str(job_verify.WINSTON_SEND)], input=text,
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


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


# ── Follow-through on the owner's requests (Winston path step 5, owner goal 2026-11-05) ──
# An owner request is an item Winston filed from the owner's message, marked
# `asked_by` (the person who asked) in its payload. Not `requested_by`: the claim
# gate reserves that for the filing principal. Each one that ends is reported to the owner
# ONCE per outcome, through the same alert route as the gaps: done and checked by
# someone other than the doer (verified), done but not yet checked (completed),
# or not done and why (dismissed: a drop after failures, a provider fault).
# Open requests that stall are already gaps above. Reads only.

DEFAULT_REQUESTS_STATE = STATE_DIR / "delegation-requests-reported.json"
ENDED = {"verified": "✅ Done and checked", "completed": "☑️ Done, not yet checked by someone else",
         "dismissed": "❌ Not done"}

_PATH = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.(?:md|txt|json|yaml|yml|py|csv|html))\b")


def _person(actor) -> str:
    """The principal behind a writer, as the owner calls it: nightshift -> Miles."""
    if not actor:
        return "nobody"
    try:
        from actor_identity import principal_of
        name = principal_of(str(actor))[0] or str(actor)
    except Exception:  # noqa: BLE001 -- a name is a courtesy, never a failure
        name = str(actor)
    return name[:1].upper() + name[1:]


def _checker(it):
    for line in reversed(it.history or []):
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "item.verify:" and line.endswith("applied"):
            return parts[1]
    return None


def _result(it) -> str:
    """Where the owner looks: the files the check names, or the task branch."""
    p = it.payload or {}
    files = sorted(set(_PATH.findall(str(p.get("check") or ""))))
    if files:
        return ", ".join(files[:3])
    repo = ((p.get("spec") or {}).get("properties") or {}).get("SURFACE")
    if p.get("runner") and repo and "/" in str(repo):
        return f"branch agent/{it.id} in {repo} (its pull request)"
    return ""


def _ended_head(it) -> str:
    """Owner, 2026-11-06: a finished request says who did it and who checked it."""
    doer = _person(getattr(it, "owner", None))
    if it.status == "verified":
        return f"✅ Done by {doer}, checked by {_person(_checker(it))}"
    if it.status == "completed":
        return f"☑️ Done by {doer}, not yet checked by someone else"
    return ENDED.get(it.status, it.status)


def _requests(folded: dict):
    for space in sorted(folded):
        for iid in sorted(folded[space].items):
            it = folded[space].items[iid]
            if (it.payload or {}).get("asked_by"):
                yield space, iid, it


def _outcome_line(space, iid, it) -> str:
    p = it.payload or {}
    who = p.get("assignee") or p.get("runner") or "nobody"
    why = f": {str(it.closed_reason)[:200]}" if it.status == "dismissed" and it.closed_reason else ""
    return f"{ENDED[it.status]}: {it.title[:100]} [{who}, {space} {iid}]{why}"


#: The bar (owner, 2026-11-05): within 15 minutes the owner hears who has it;
#: within 24 hours something other than the doer has checked it.
WHO_HAS_IT_MS = 15 * 60 * 1000
CHECKED_BY_MS = 24 * 3600 * 1000


def _stages_due(it, now_ms: int) -> list[tuple[str, str]]:
    """(stage, message tail) the request has reached by `now_ms`. Each stage is
    told once. Pattern: a durable workflow with timers, kept as level-triggered
    state (the ledger) plus a record of what was told; a missed timer is the
    next step (tell the owner), never a silent wait."""
    p = it.payload or {}
    who = p.get("assignee") or p.get("runner") or "nobody"
    created = _event_ms(it, "item.create")
    out = []
    if it.status in ENDED:
        out.append((it.status, ENDED[it.status]))
    elif it.status == "claimed":
        out.append(("has", f"🗂️ {who} has it"))
    elif created is not None and now_ms - created > WHO_HAS_IT_MS:
        out.append(("late_pickup", f"⏳ Not picked up {_hours(now_ms - created)} after you asked "
                                   f"(waiting for {who})"))
    if it.status not in ("verified", "dismissed"):
        # The bar counts from when the owner asked, not from when the doer finished.
        if created is not None and now_ms - created > CHECKED_BY_MS:
            out.append(("late_check", "⚠️ 24 h and not checked by anyone other than the doer"))
    return out


def report_requests(folded: dict, *, now_ms: int | None = None, state_path=DEFAULT_REQUESTS_STATE,
                    send: Callable[[str], bool] | None = None) -> dict:
    """Tell the owner each stage of each request once, in their own chat. {reported, unsent}."""
    send = send or _send_owner
    now = int(time.time() * 1000) if now_ms is None else now_ms
    st = _load(state_path)
    seen = st.get("told") or {}
    due = []
    for space, iid, it in _requests(folded):
        k = f"{space}/{iid}"
        for stage, head in _stages_due(it, now):
            if stage not in (seen.get(k) or []):
                due.append((k, stage, head, space, iid, it))
    if not due:
        return {"reported": [], "unsent": []}
    lines = []
    for k, stage, head, space, iid, it in due:
        p = it.payload or {}
        why = f": {str(it.closed_reason)[:200]}" if stage == "dismissed" and it.closed_reason else ""
        if stage in ("verified", "completed"):
            head = _ended_head(it)
            res = _result(it)
            why = f". Result: {res}" if res else ""
            lines.append(f"{head}: {it.title[:100]} [{space} {iid}]{why}")
            continue
        lines.append(f"{head}: {it.title[:100]} [{p.get('assignee') or p.get('runner') or 'nobody'}, "
                     f"{space} {iid}]{why}")
    keys = sorted({d[0] for d in due})
    if not send(("Your requests to Winston:\n" + "\n".join(lines))[:3400]):
        return {"reported": [], "unsent": keys}
    for k, stage, *_ in due:
        seen.setdefault(k, []).append(stage)
    _save(state_path, {"told": seen})
    return {"reported": keys, "unsent": []}


def requests_line(folded: dict) -> str:
    open_ = ended = 0
    for _, _, it in _requests(folded):
        if it.status in OPEN:
            open_ += 1
        elif it.status in ENDED:
            ended += 1
    return f"Your requests: {open_} open, {ended} finished (each finished one was reported to you)."


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
    folded, errors = states(a.root)
    now_ms = int(time.time() * 1000)
    found = [g for name, state in folded.items() for g in gaps_in(name, state, now_ms=now_ms)]
    if a.cmd == "briefing":
        print(briefing_line(found, errors) + " " + requests_line(folded))
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
                           "errors": errors, "gaps": found,
                           "line": briefing_line(found, errors) + " " + requests_line(folded)})
        result = alert(found, state_path=a.state)
        result.update({f"requests_{k}": v for k, v in report_requests(folded).items()})
    summary = {"gaps": len(found), "errors": errors, **{k: len(v) for k, v in result.items()}}
    print(json.dumps({**summary, **result, "list": found} if a.json else summary, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
