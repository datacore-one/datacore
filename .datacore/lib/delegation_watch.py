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
import html
import json
import re
import shlex
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


# ── An agent that paused itself (issue #296, owner 2026-11-06) ────────────────
# A login, credit or usage-limit failure pauses an agent's claiming on its own
# host (claim_backoff) for PROBE_AFTER; items stay queued. On 2026-11-06 Tris
# paused at 17:21 (OpenRouter 402), Winston filed the owner's request for her
# at 21:11 and said "Tris has it", and nobody told the owner. The block file is
# local to the agent's host, but the failure is in the ledger: an item.release
# by that agent whose error claim_backoff calls an access or usage failure,
# with no claim or completion by the agent after it. Reads only.

_PAUSE_ENDS = ("item.claim", "item.complete")


def _pause_why(error: str) -> str | None:
    import claim_backoff
    if claim_backoff.is_usage_limit(error):
        return "the model usage limit was reached"
    if any(m in error.lower() for m in claim_backoff.CREDIT_MARKERS):
        return "out of model credit"
    if claim_backoff.is_access_failure(error):
        return "its model login failed"
    return None


def pauses_from(events) -> dict:
    """{actor: {since_ms, retry_ms, why}} for each agent whose latest attempt
    ended in an access or usage failure."""
    import claim_backoff
    probe_ms = int(claim_backoff.PROBE_AFTER.total_seconds() * 1000)
    run: dict[str, dict | None] = {}
    for e in sorted(events, key=lambda e: _hlc_ms(e.hlc) or 0):
        if e.type in _PAUSE_ENDS:
            run[e.actor] = None
        elif e.type == "item.release":
            p = e.payload or {}
            why = ("the model usage limit was reached" if p.get("kind") == "usage_limit"
                   else _pause_why(str(p.get("error") or "")))
            at = _hlc_ms(e.hlc)
            if why is None or at is None:
                run[e.actor] = None       # an ordinary failure: the model ran
                continue
            cur = run.get(e.actor)
            run[e.actor] = {"since_ms": cur["since_ms"] if cur else at, "retry_ms": at + probe_ms, "why": why}
    return {a: p for a, p in run.items() if p}


def pauses(root, **_kw) -> dict:
    """pauses_from over every ledger space's task events; a space that cannot be read is skipped."""
    from ledger.log import read_task_events
    from ledger.union import ledger_spaces
    events = []
    for space in ledger_spaces(root):
        try:
            events += [e for e in read_task_events(space) if e.type in (*_PAUSE_ENDS, "item.release")]
        except Exception:  # noqa: BLE001 -- the gap check names unreadable spaces
            continue
    return pauses_from(events)


def _clock(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def pause_text(actor: str, p: dict) -> str:
    return (f"⏸ {_person(actor)} cannot take work: {p['why']} (since {_clock(p['since_ms'])}). "
            f"Their tasks stay queued; {_person(actor)} retries by itself about {_clock(p['retry_ms'])}, "
            f"or sooner once the cause is fixed and the pause is cleared on their host.")


def who_text(rows: list[dict], errors: dict, per_person: int = 8, pauses: dict | None = None) -> str:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["assignee"], []).append(r)
    lines = [pause_text(a, p) for a, p in sorted((pauses or {}).items())]
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
    if not rows:
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


def _agent_names() -> set[str]:
    """Every person's and agent's name in this install's registry (roster), plus
    the overnight module's writer name. Shipped code names nobody of ours (INS-3)."""
    try:
        import roster
        names = {str(n).lower() for n, p in roster.entries().items()
                 if isinstance(p, dict) and p.get("kind") in ("agent", "human")}
    except Exception:  # noqa: BLE001 -- an unreadable registry names nobody; the check still runs
        names = set()
    return {"nightshift"} | names


_COUNTED = re.compile(r"(grep\s+(-\w*\s+)*-\w*c|wc\s+-l)")
_COMPARED = re.compile(r"-(ge|gt|eq)\s+\"?\d")
_PR_SEARCH = ("api.github.com", "gh pr ")
_CHECK_EXAMPLE = ("For example: test -s <new file> && grep -qi '<a term done_when requires>' <file> "
                  "&& test $(grep -c '^- ' <file>) -ge 3")


def clean_check(check) -> str:
    """A check as a shell will read it. Winston's 2026-11-07 check arrived HTML-escaped
    (`&amp;&amp;`), which no shell runs, so it could never pass."""
    return html.unescape(str(check or "")).strip()


def _covered(word: str, trivial: set[str]) -> bool:
    """A word that is a title word, an agent name, or a variant of one ("down" in
    "downtime", "prevent" in "prevention")."""
    return word in trivial or any(
        min(len(word), len(t)) >= 4 and (t.startswith(word) or word.startswith(t)) for t in trivial)


def _trivial_pattern(pat: str, trivial: set[str]) -> bool:
    """A grep pattern any note on the topic matches. An alternation is only as strong as
    its weakest branch: `(down|offline)` passes on "down" alone."""
    for branch in re.split(r"\|", pat):
        w = _words(branch)
        if not w or all(_covered(x, trivial) for x in w):
            return True
    return False


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(s).lower()))


def _grep_patterns(check: str) -> list[str]:
    """The pattern each grep/rg in a check looks for (its first non-option argument)."""
    try:
        toks = list(shlex.shlex(check, posix=True, punctuation_chars=True))
    except ValueError:
        return []
    out, i = [], 0
    while i < len(toks):
        if toks[i] in ("grep", "rg", "egrep"):
            j = i + 1
            while j < len(toks) and toks[j] not in ("&&", "||", "|", ";"):
                t = toks[j]
                if t in ("-e", "--regexp") and j + 1 < len(toks):
                    out.append(toks[j + 1])
                    break
                if not t.startswith("-"):
                    out.append(t)
                    break
                j += 1
            i = j
        i += 1
    return out


def check_problem(payload: dict) -> str | None:
    """Why the check on an owner's request would pass whatever the result says, or None.

    Issue #298 (2026-11-07): Winston filed `test -s F && grep -qi tris F && grep -qi
    data F` for a note comparing Tris and Data. Any note on the topic passes it.
    Only items asked for by the owner are judged; everything else is left alone."""
    if not isinstance(payload, dict) or not payload.get("asked_by"):
        return None
    check = clean_check(payload.get("check"))
    if not check:
        return ("an owner's request needs a `check`: a shell command, run from the space "
                "folder, that passes only when the result says what done_when asks. "
                + _CHECK_EXAMPLE)
    if any(s in check for s in _PR_SEARCH):
        return None
    trivial = _words(payload.get("title")) | _words(payload.get("assignee")) | _agent_names()
    content, weak = 0, []
    for pat in _grep_patterns(check):
        if not _trivial_pattern(pat, trivial):
            content += 1
        else:
            weak.append(pat)
    if _COUNTED.search(check) and _COMPARED.search(check):
        content += len(_COMPARED.findall(check))
    if content >= 2:
        return None
    said = f" It only looks for {', '.join(repr(p) for p in weak)}, words from the title or agent names." if weak else ""
    return ("the check would pass for any file on the topic, whatever it says." + said
            + " It needs at least two tests of what the result must contain: a grep for a "
            "specific term, heading or finding that done_when requires, or a count compared "
            "with a number (grep -c ... -ge N). " + _CHECK_EXAMPLE)


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


def _stages_due(it, now_ms: int, pauses: dict | None = None) -> list[tuple[str, str]]:
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
    elif who in (pauses or {}):
        # Issue #296: filed for an agent that cannot start -- said once, at once.
        out.append(("paused", f"⏸ Filed for {_person(who)}, who cannot take it yet. "
                              + pause_text(who, pauses[who])))
    elif created is not None and now_ms - created > WHO_HAS_IT_MS and _event_ms(it, "item.claim") is None:
        # A request back at `created` after a claim was RELEASED, not missed:
        # the request-releases subscriber tells that, with the doer's reason.
        out.append(("late_pickup", f"⏳ Not picked up {_hours(now_ms - created)} after you asked "
                                   f"(waiting for {who})"))
    if it.status not in ("verified", "dismissed"):
        # The bar counts from when the owner asked, not from when the doer finished.
        if created is not None and now_ms - created > CHECKED_BY_MS:
            out.append(("late_check", "⚠️ 24 h and not checked by anyone other than the doer"))
    return out


def _assistant() -> str:
    """What the owner calls the chief of staff who files their requests (roster)."""
    try:
        import roster
        cos = roster.by_role("chief of staff")
        return roster.display(cos) if cos else "your assistant"
    except Exception:  # noqa: BLE001 -- a heading is a courtesy, never a failure
        return "your assistant"


def report_requests(folded: dict, *, now_ms: int | None = None, state_path=DEFAULT_REQUESTS_STATE,
                    send: Callable[[str], bool] | None = None, pauses: dict | None = None) -> dict:
    """Tell the owner each stage of each request once, in their own chat. {reported, unsent}."""
    send = send or _send_owner
    now = int(time.time() * 1000) if now_ms is None else now_ms
    st = _load(state_path)
    seen = st.get("told") or {}
    due = []
    for space, iid, it in _requests(folded):
        k = f"{space}/{iid}"
        for stage, head in _stages_due(it, now, pauses):
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
    if not send((f"Your requests to {_assistant()}:\n" + "\n".join(lines))[:3400]):
        return {"reported": [], "unsent": keys}
    for k, stage, *_ in due:
        seen.setdefault(k, []).append(stage)
    _save(state_path, {"told": seen})
    return {"reported": keys, "unsent": []}


def _state_for(space: str, root: Path | None = None):
    from ledger.hub import authoritative_state
    return authoritative_state(Path(root or Path.home() / "Data") / space)


def on_release(event: dict, *, state_for: Callable | None = None, send: Callable[[str], bool] | None = None,
               state_path=DEFAULT_REQUESTS_STATE) -> None:
    """The request-releases subscriber (registry/hooks.yaml ledger_events).

    A doer released one of the owner's requests without finishing it: tell the
    owner once per request, with the doer's own words where the claim run
    recorded them (`doer_said`), else the check's error. Raises when the
    message was not delivered, so the subscriber runner retries it."""
    p = event.get("payload") or {}
    iid, space = p.get("id"), event.get("space")
    if not iid or not space:
        return
    if state_for is None:
        space_dir = event.get("space_dir")
        state_for = (lambda s: _state_for(Path(space_dir).name, Path(space_dir).parent)) if space_dir else _state_for
    it = state_for(space).items.get(iid)
    if it is None or not (it.payload or {}).get("asked_by"):
        return
    st = _load(state_path)
    seen = st.get("told") or {}
    k = f"{space}/{iid}"
    if "released" in (seen.get(k) or []):
        return
    why = str(p.get("doer_said") or p.get("error") or p.get("reason") or "no reason recorded")[:400]
    text = (f"Your requests to {_assistant()}:\n↩️ {_person(event.get('actor'))} released it without finishing: "
            f"{it.title[:100]} [{space} {iid}]. Reason: {why}")
    if not (send or _send_owner)(text):
        raise RuntimeError("the release message was not delivered")
    seen.setdefault(k, []).append("released")
    st["told"] = seen
    _save(state_path, st)


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


def _converge(path) -> bool:
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "ledger_transport.py"),
                            "converge", "--space", str(path)], capture_output=True, text=True, timeout=180)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def refresh_open_requests(root, folded: dict) -> list[str]:
    """Bring in the latest copy of every space holding an owner request still open.

    The watcher read its own copy, synced on the quarter hour it runs at, so a
    claim made minutes earlier was not seen and "not picked up" went out for
    work in progress (2026-11-08). Returns the spaces refreshed."""
    names = [name for name, st in folded.items()
             if any((it.payload or {}).get("asked_by") and it.status not in ENDED
                    and it.status not in ("verified", "dismissed")
                    for it in getattr(st, "items", {}).values())]
    for name in names:
        _converge(Path(root) / name)
    return names


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
        paused = pauses(a.root)
        print(json.dumps({"errors": errors, "open": rows, "paused": paused}, sort_keys=True) if a.json
              else who_text(rows, errors, pauses=paused))
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
        if refresh_open_requests(a.root, folded):
            folded, errors = states(a.root)
            found = [g for name, state in folded.items() for g in gaps_in(name, state, now_ms=now_ms)]
        _save(a.artifact, {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "errors": errors, "gaps": found,
                           "line": briefing_line(found, errors) + " " + requests_line(folded)})
        result = alert(found, state_path=a.state)
        result.update({f"requests_{k}": v for k, v in report_requests(folded, pauses=pauses(a.root)).items()})
        # The ledger-event subscribers (registry/hooks.yaml ledger_events) run on
        # this same 15-minute cycle; a failed delivery is kept and retried there.
        try:
            import ledger_subscribers
            subs = ledger_subscribers.run(root=a.root)
            result["subscriber_failures"] = [n for n, r in subs.items() if r["failed"]]
        except Exception as exc:  # noqa: BLE001 -- reported, never stops the gap check
            result["subscriber_failures"] = [f"runner: {exc}"[:200]]
    summary = {"gaps": len(found), "errors": errors, **{k: len(v) for k, v in result.items()}}
    print(json.dumps({**summary, **result, "list": found} if a.json else summary, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
