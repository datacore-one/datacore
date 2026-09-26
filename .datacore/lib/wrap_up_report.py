#!/usr/bin/env python3
"""Render the /wrap-up consolidated report from data, so its shape never drifts.

WHY THIS EXISTS. The report was composed freehand by the model from a template
in commands/wrap-up.md §10, and every model reinterpreted it. Across 168
archived reports: Opus 5 used the banner-and-rules form 62 times and dropped the
section rules 77 times; Opus 5.5 switched to markdown headings; Fable kept the
template every time. A template the model re-types is a suggestion. This file
makes it a program: the model supplies judgement as JSON, the numbers come from
what preflight / meta / finalize / audit already computed, and the layout is
code — identical in Claude Code, Codex, Cursor or any harness that can run
Python.

    python3 .datacore/lib/wrap_up_mechanics.py report --input narrative.json
    python3 .datacore/lib/wrap_up_mechanics.py report --input narrative.json --journal

A missing required field is a refusal (exit 2, fields named), never a thinner
report. A missing mechanics input (e.g. `meta` without a session id outside
Claude Code) renders as "unavailable (<reason>)", never as a blank or a zero.

The narrative JSON (all keys optional unless marked REQUIRED):

  goal (REQUIRED)            str
  done (REQUIRED)            [str]
  decisions                  [str]
  rejected                   str
  next (REQUIRED)            str
  continuation               [{heading, id, scheduled, signals}]
  tasks_completed            {auto: [str], suggested: [str], retroactive: [str]}
  learnings                  [str]
  engrams                    [str]            engram ids written in-session
  gtd_proposals              [str]
  delegations                [str]
  coverage                   str              the §10c one-line coverage note
  meta (REQUIRED arc, observation)
                             {arc, corrections: [{error, category}], user_role,
                              energy, observation}
  files                      {location: {created: [str], modified: [str]}}
  artifacts                  [{type, path, description}]
  social (REQUIRED)          {personal_x, project_x, linkedin}
  pulse                      str              score/notes, or omitted = not answered
  checklist (REQUIRED)       [{step, title, status}] — the 12 §12 rows
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

HEAVY = "═" * 51
LIGHT = "─" * 51
MARKER = "<!-- wrap-up-report:{sid} -->"

SECTIONS = [
    "1. SESSION NARRATIVE",
    "2. CONTINUATION TASKS",
    "3. TASKS COMPLETED",
    "4. LEARNING & JOURNALS",
    "5. GTD TASKS EXTRACTED",
    "6. INSIGHT VERIFICATION",
    "7. SESSION META-ANALYSIS",
    "8. FILES CREATED/MODIFIED",
    "9. KNOWLEDGE ARTIFACTS",
    "STATS",
    "10. SOCIAL POSTS (draft for immediate posting)",
    "TOKEN COST",
]

REQUIRED = ("goal", "done", "next", "social", "checklist")
REQUIRED_META = ("arc", "observation")
REQUIRED_SOCIAL = ("personal_x", "project_x", "linkedin")
CHECKLIST_STEPS = 12
DONE_STATUSES = ("run ✓", "inferred-and-reported", "applied-from-feedback",
                 "not-applicable", "skipped-by-user", "skipped-by-mode-fast",
                 "not-answered")


class ReportInputError(ValueError):
    """The narrative JSON is missing something the report cannot render without."""


def validate(n: dict) -> list[str]:
    missing = [k for k in REQUIRED if not n.get(k)]
    meta = n.get("meta") or {}
    missing += [f"meta.{k}" for k in REQUIRED_META if not meta.get(k)]
    social = n.get("social") or {}
    if n.get("social"):
        missing += [f"social.{k}" for k in REQUIRED_SOCIAL if not social.get(k)]
    rows = n.get("checklist") or []
    if rows and len(rows) != CHECKLIST_STEPS:
        missing.append(f"checklist: {len(rows)} rows, need exactly {CHECKLIST_STEPS} (one per step)")
    return missing


def _section(title: str) -> list[str]:
    return ["", LIGHT, title, LIGHT, ""]


def _bullets(items, indent: str = "  ", empty: str = "None") -> list[str]:
    items = [i for i in (items or []) if str(i).strip()]
    return [f"{indent}- {i}" for i in items] if items else [f"{indent}{empty}"]


def _local(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except (AttributeError, ValueError):
        return None


def _span(start: str, end: str) -> str:
    """`HH:MM — HH:MM`, with dates when the session crossed midnight."""
    a, b = _local(start), _local(end)
    if not a or not b:
        return "timing unavailable"
    if a.date() == b.date():
        return f"{a:%H:%M} — {b:%H:%M}"
    return f"{a:%Y-%m-%d %H:%M} — {b:%Y-%m-%d %H:%M}"


def _duration(start: str | None, end: str | None) -> str | None:
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    mins = int((b - a).total_seconds() // 60)
    return f"{mins // 60}h {mins % 60:02d}m"


def _fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else "unavailable"


def _unavailable(step: dict | None, name: str) -> str | None:
    """Reason a mechanics input is missing, or None when it is present."""
    if not step:
        return f"`{name}` did not run this session"
    if step.get("error"):
        return f"`{name}`: {step['error']}"
    return None


def checklist_verified(rows: list[dict]) -> int:
    return sum(1 for r in rows if str(r.get("status", "")).startswith(DONE_STATUSES))


def render(n: dict, mech: dict) -> str:
    """The consolidated report, laid out exactly as commands/wrap-up.md §10."""
    problems = validate(n)
    if problems:
        raise ReportInputError("; ".join(problems))

    meta_step, audit, finalize = mech.get("meta"), mech.get("audit"), mech.get("finalize")
    archive = mech.get("archive_meta") or {}
    rows = n["checklist"]
    start, end = archive.get("started_at"), archive.get("ended_at")
    if start and end:
        session_line = f"Session: {_span(start, end)} ({_duration(start, end)})"
    else:
        session_line = "Session: timing unavailable (session not archived)"

    out = [HEAVY, "SESSION COMPLETE — CONSOLIDATED REPORT", HEAVY, "",
           session_line,
           f"Checklist: {checklist_verified(rows)}/{CHECKLIST_STEPS} items verified"]
    if n.get("pulse"):
        out.append(f"Pulse: {n['pulse']}")
    else:
        out.append("Pulse: not answered")

    # 1
    out += _section(SECTIONS[0])
    out += [f"Goal: {n['goal']}", "", "Done:"] + _bullets(n["done"])
    if n.get("decisions"):
        out += ["", "Decisions:"] + _bullets(n["decisions"])
    if n.get("rejected"):
        out += ["", f"Rejected: {n['rejected']}"]
    out += ["", f"Next: {n['next']}"]

    # 2
    out += _section(SECTIONS[1])
    conts = n.get("continuation") or []
    if conts:
        for c in conts:
            out.append(f"  - {c.get('heading')} [{c.get('id', '?')}] scheduled {c.get('scheduled', '—')}")
            if c.get("signals"):
                out.append(f"    signals: {c['signals']}")
    else:
        out.append("None needed (work appears complete)")

    # 3
    out += _section(SECTIONS[2])
    tc = n.get("tasks_completed") or {}
    out += ["Auto-marked DONE (high confidence):"] + _bullets(tc.get("auto"))
    out += ["", "Suggested DONE (medium confidence — confirm):"] + _bullets(tc.get("suggested"))
    if tc.get("retroactive"):
        out += ["", "Retroactive DONE (no existing task):"] + _bullets(tc["retroactive"])

    # 4
    out += _section(SECTIONS[3])
    out.append("Journals updated:")
    journals = _journals(audit)
    out += [f"  - {j} ✓" for j in journals] if journals else ["  unavailable (audit did not run)"]
    out += ["", "Learnings captured:"] + _bullets(n.get("learnings"),
                                                  empty="Deferred to the nightly learning sweep")
    engrams = n.get("engrams") or []
    out += ["", f"Engrams registered: {len(engrams)}" + (f" ({', '.join(engrams)})" if engrams else "")]

    # 5
    out += _section(SECTIONS[4])
    out += ["Proposed (say \"add 1,3\" or \"add all\"):"]
    props = n.get("gtd_proposals") or []
    out += [f"  {i}. {p}" for i, p in enumerate(props, 1)] or ["  None"]
    dels = n.get("delegations") or []
    out += ["", "Delegation opportunities (opt in: \"delegate 1\"):"]
    out += [f"  {i}. {d}" for i, d in enumerate(dels, 1)] or ["  None"]

    # 6
    out += _section(SECTIONS[5])
    out.append(n.get("coverage") or "Coverage: not reported")

    # 7
    out += _section(SECTIONS[6])
    m = n["meta"]
    out += [f"Session Arc: {m['arc']}", ""]
    corr = m.get("corrections") or []
    out.append(f"Corrections: {len(corr)} total")
    if corr:
        out += ["  | # | Error                    | Category        |",
                "  |---|--------------------------|-----------------|"]
        out += [f"  | {i} | {str(c.get('error', '')).ljust(24)} | {str(c.get('category', '')).ljust(15)} |"
                for i, c in enumerate(corr, 1)]
    out += ["", "Session Shape (from `wrap_up_mechanics.py meta`):"]
    why = _unavailable(meta_step, "meta")
    if why:
        out.append(f"  - unavailable ({why})")
    else:
        out += [f"  - Turns: {_fmt(meta_step.get('turns'))} (user: {_fmt(meta_step.get('user_turns'))})"
                f"   Tool calls: {_fmt(meta_step.get('tool_calls'))}"
                f"   Agents spawned: {sum((meta_step.get('agents_spawned') or {}).values())}",
                f"  - Output tokens: {_fmt(meta_step.get('output_tokens'))}"
                f"     Subagent output: {_fmt(meta_step.get('subagent_output_tokens'))}"
                f"     Billable: {_fmt(meta_step.get('billable_tokens'))}",
                f"  - Spaces touched: {', '.join(meta_step.get('spaces_touched') or []) or 'none'}"]
    if m.get("user_role"):
        out += ["", f"User Role: {m['user_role']}"]
    if m.get("energy"):
        out += ["", f"Session Energy Pattern: {m['energy']}"]
    out += ["", f"Key Observation: {m['observation']}"]

    # 8
    out += _section(SECTIONS[7])
    files = n.get("files") or {}
    if not files:
        out.append("  None")
    for loc, groups in files.items():
        out.append(f"  {loc}:")
        for label, key, suffix in (("Created", "created", " (NEW)"), ("Modified", "modified", "")):
            if groups.get(key):
                out.append(f"    {label}:")
                out += [f"      - {f}{suffix}" for f in groups[key]]
        out.append("")
    if out[-1] == "":
        out.pop()

    # 9
    out += _section(SECTIONS[8])
    arts = n.get("artifacts") or []
    if arts:
        out += ["  | TYPE | PATH | DESCRIPTION |", "  |------|------|-------------|"]
        out += [f"  | {a.get('type', '')} | {a.get('path', '')} | {a.get('description', '')} |" for a in arts]
    else:
        out.append("  None")

    # STATS
    out += _section(SECTIONS[9])
    pushed = _all_pushed(audit, finalize)
    out += [
        f"- Tasks completed: {len(tc.get('auto') or []) + len(tc.get('retroactive') or [])}",
        f"- Continuation tasks: {len(conts)}" + (" (with bootstrap context)" if conts else ""),
        f"- Knowledge artifacts: {len(arts)}" + (" (with paths in journal)" if arts else ""),
        f"- Learnings captured: {len(n.get('learnings') or [])}",
        f"- Engrams: {len(engrams)} registered",
        f"- Journals updated: {len(journals) if journals else 'unavailable'}",
        f"- All repos pushed: {pushed}",
    ]
    if conts:
        out += ["", "Next session can run: /continue", "Or search for :continuation: tagged tasks."]

    # 10
    out += _section(SECTIONS[10])
    s = n["social"]
    out += ["Personal X (@jssr):", f"  {s['personal_x']}", "",
            "Project X:", f"  {s['project_x']}", "",
            "LinkedIn:"] + [f"  {line}" if line else "" for line in str(s["linkedin"]).splitlines()]

    # TOKEN COST
    out += _section(SECTIONS[11])
    out += _token_table(meta_step, archive)
    out += ["", "Ready to close terminal.", HEAVY]
    return "\n".join(out) + "\n"


def _journals(audit: dict | None) -> list[str]:
    if not audit:
        return []
    found = []
    for c in audit.get("checks") or []:
        name, detail = c.get("check") or c.get("name") or "", c.get("detail")
        if name == "personal journal written" and c.get("pass") and detail:
            found.append(_rel(str(detail)))
        elif name == "space journals" and isinstance(detail, str) and "[" in detail:
            try:
                found += json.loads(detail[detail.index("["):].replace("'", '"'))
            except ValueError:
                pass
    return found


def _rel(p: str) -> str:
    home_data = str(Path.home() / "Data") + "/"
    return p[len(home_data):] if p.startswith(home_data) else p


def _all_pushed(audit: dict | None, finalize: dict | None) -> str:
    if audit:
        for c in audit.get("checks") or []:
            if (c.get("check") or c.get("name") or "").startswith(("all repos pushed", "session work")):
                return "Yes" if c.get("pass") else f"No — {c.get('detail')}"
    if finalize:
        bad = [p["repo"] for p in finalize.get("pushes") or [] if p.get("ok") is False]
        return "No — push failed: " + ", ".join(bad) if bad else "Yes (per finalize)"
    return "unavailable (neither audit nor finalize ran)"


def _token_table(meta_step: dict | None, archive: dict) -> list[str]:
    why = _unavailable(meta_step, "meta")
    if why:
        return [f"Token cost unavailable ({why}).",
                "No estimate is given: a point estimate without the transcript was once off by 5000x."]
    agents = meta_step.get("agents_spawned") or {}
    sub = meta_step.get("subagent_output_tokens") or 0
    main = meta_step.get("output_tokens") or 0
    label = ", ".join(f"{k} ×{v}" for k, v in agents.items()) or "none"
    rows = [("Subagents (" + label + ")", _fmt(sub)),
            ("**Subagent total**", f"**{_fmt(sub)}**"),
            ("Main conversation", _fmt(main)),
            ("**Session total**", f"**{_fmt(main + sub)}**")]
    w = max(len("Component"), *(len(r[0]) for r in rows))
    v = max(len("Tokens"), *(len(r[1]) for r in rows))
    table = [f"| {'Component'.ljust(w)} | {'Tokens'.ljust(v)} |",
             f"|{'-' * (w + 2)}|{'-' * (v + 2)}|"]
    table += [f"| {a.ljust(w)} | {b.ljust(v)} |" for a, b in rows]
    t = archive.get("tokens") or {}
    table += ["", "Main conversation breakdown (output tokens; billable = fresh + cache write + output):",
              f"  Turns:       {_fmt(meta_step.get('turns'))}",
              f"  Input fresh: {_fmt(t.get('input_tokens'))}",
              f"  Cache write: {_fmt(t.get('cache_creation_input_tokens'))}",
              f"  Cache read:  {_fmt(t.get('cache_read_input_tokens'))}",
              f"  Output:      {_fmt(t.get('output_tokens'))}",
              f"  Billable:    {_fmt(meta_step.get('billable_tokens'))}"]
    return table


def render_journal(n: dict, mech: dict, sid: str) -> str:
    """The journal form: the same report verbatim, plus the sections the
    PreToolUse checklist hook requires, all from the same data."""
    report = render(n, mech)
    title = n.get("title") or n["goal"]
    rows = n["checklist"]
    meta_lines = report.split(LIGHT + "\n" + SECTIONS[6] + "\n" + LIGHT + "\n", 1)[1]
    meta_lines = meta_lines.split("\n\n" + LIGHT, 1)[0].strip("\n")
    token = report.split(LIGHT + "\n" + SECTIONS[11] + "\n" + LIGHT + "\n", 1)[1]
    token = token.split("\n\nReady to close terminal.", 1)[0].strip("\n")
    parts = [
        f"## Session report: {title}",
        MARKER.format(sid=sid),
        "",
        "### Consolidated Report",
        "",
        "```text",
        report.rstrip("\n"),
        "```",
        "",
        "### Session Meta-Analysis",
        "",
        "```text",
        meta_lines,
        "```",
        "",
        "### Token Cost",
        "",
        token,
        "",
        "## Wrap-up Checklist Audit",
        "",
        "| Step | Title | Status |",
        "|------|-------|--------|",
    ]
    parts += [f"| {r.get('step', i)} | {r.get('title', '')} | {r.get('status', '')} |"
              for i, r in enumerate(rows, 1)]
    return "\n".join(parts) + "\n"
