#!/usr/bin/env python3
"""
gtd_decision_board.py — a GTD weekly review, rendered as a decision board.

Follows .datacore/skills/decision-board: a local, PLUR-branded page with one
decision per row, a suggested answer on each, a "Note for Claude" field, and a
Save that downloads <slug>.decisions.json. The page is written to
.datacore/state/decision-boards/ (gitignored) and is never published.

Reads org files through org-workspace (never raw text) and never writes one.
Applying the saved choices is a separate, explicit step.

What needs a call:
  REVIEW                                 -> waiting for review
  WAITING                                -> waiting on others
  scheduled or deadline on/before today  -> dates that slipped
  NEXT, no date, created > 14 days ago   -> stale next actions
  TODO, no future date, created > 30 d   -> old backlog
  open items in a --projects file        -> projects
  open top-level captures in inbox.org   -> inbox
Fresh items and items with a future date are counted and noted, not asked.

Personal inputs never live in this file: --week (sections that sit above the
task list), --overrides (per-id suggestions) and --redact (terms replaced with
"the customer") are passed at run time.

Usage:
  python3 .datacore/lib/gtd_decision_board.py build \\
    --files 0-personal/org/inbox.org 0-personal/org/next_actions.org \\
    --projects 0-personal/org/projects.org --today 2026-09-11 \\
    --slug w37-weekly-review --title "W37 Weekly Review" \\
    [--week week.json] [--overrides overrides.json] [--redact WORD] \\
    [--prefill w37-weekly-review.decisions.json]
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from org_workspace_adapter import _load_ws, _node_to_dict  # noqa: E402

ASSETS = HERE / "decision_board"
BOARDS = HERE.parent / "state" / "decision-boards"
FONT_HREF = ("https://fonts.googleapis.com/css2?family=Outfit:wght@200;300;400;500"
             "&family=JetBrains+Mono:wght@400;500&display=swap")
OPEN = ("TODO", "NEXT", "WAITING", "REVIEW")
PRIVATE_TAGS = {"health", "medical", "family", "finance", "personal-finance", "private"}

# key, prefix for short ids, heading, one-line hint
SECTIONS = [
    ("inbox", "I", "Inbox", "Unprocessed captures. Decide what each one is."),
    ("slipped", "D", "Dates that slipped",
     "Scheduled or due on or before today and still open. Give each one a new home."),
    ("stale", "N", "Stale next actions",
     "Marked NEXT more than two weeks ago, with no date. A next action that waits this long is usually a someday."),
    ("waiting", "T", "Waiting on others", "What you are waiting on, and for how long."),
    ("review", "R", "Waiting for your review",
     "Marked REVIEW: agent output or work waiting for your sign-off."),
    ("projects", "P", "Projects", "Open items in your projects file."),
    ("backlog", "B", "Old backlog", "TODO items open more than 30 days and never started."),
]


def _ymd(y, m, d):
    # Hex ids such as org-12345678abcd match the date pattern too; reject them.
    try:
        v = date(int(y), int(m), int(d))
    except ValueError:
        return None
    return v if 2000 <= v.year <= 2100 else None


def _date(s):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    return _ymd(*m.groups()) if m else None


def _created(props, oid):
    d = _date(props.get("CREATED"))
    if d:
        return d
    m = re.match(r"org-(\d{4})(\d{2})(\d{2})", oid or "")
    return _ymd(*m.groups()) if m else None


def _float(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _fmt(d):
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _area(node, fallback):
    top, cur = None, node.parent
    while cur is not None and getattr(cur, "level", 0) and cur.level >= 1:
        top, cur = cur, cur.parent
    return top.heading if top is not None else fallback


def _snippet(node, tags):
    if PRIVATE_TAGS & {t.lower() for t in tags}:
        return ""
    body = getattr(node, "body", "") or ""
    body = re.sub(r":PROPERTIES:.*?:END:", " ", body, flags=re.S)
    body = re.sub(r":LOGBOOK:.*?:END:", " ", body, flags=re.S)
    body = re.sub(r"\[\[[^\]]*\]\[([^\]]*)\]\]", r"\1", body)
    body = re.sub(r"\[\[([^\]]*)\]\]", r"\1", body)
    body = re.sub(r"^\s*(SCHEDULED|DEADLINE|CLOSED):.*$", " ", body, flags=re.M)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:240] + ("…" if len(body) > 240 else "")


def _redactor(terms):
    pats = [re.compile(re.escape(t), re.I) for t in terms if t]

    def red(s):
        s = s or ""
        for p in pats:
            s = p.sub("the customer", s)
        return s
    return red


def _option(section, key, nxt, when):
    """Label and one-line consequence for a standard GTD choice."""
    if section == "waiting" and key == "next":
        return "Follow up now", "Becomes NEXT on your list: you chase it."
    if section == "waiting" and key == "defer":
        return f"Check again on {_fmt(when)}", f"Stays WAITING, with a check date of {_fmt(when)}."
    if section == "waiting" and key == "done":
        return "Got it", "Marked DONE: what you waited for arrived."
    if section == "review" and key == "next":
        return "Rework it", "Back to NEXT on your own list."
    table = {
        "next": ("Do next", "Stays on your next-actions list, with no date; an old date is cleared."),
        "defer": (f"Defer to {_fmt(when)}",
                  f"Becomes TODO, scheduled {_fmt(when)}. Write another date in the note to change it."),
        "delegate": ("Delegate", "Tagged :AI: for nightshift, or handed to whoever you name in the note."),
        "someday": ("Someday", "Set aside as someday (DEFERRED): out of the weekly view, nothing deleted."),
        "drop": ("Drop", "Marked CANCELLED; it stays in the file's history."),
        "accept": ("Accept", "Marked DONE."),
        "done": ("Already done", "Marked DONE: it already happened."),
    }
    return table[key]


def classify(it, today, nxt, later, is_project, is_inbox):
    """Return (section, context, option keys, suggested, defer date) or None."""
    st, age, pri = it["state"], it["age"], it["pri"]
    sched, dl, props = it["sched"], it["dl"], it["props"]
    ai = "AI" in it["tags"]
    age_txt = f"{age} days" if age is not None else "an unknown time (no creation date)"

    if is_inbox:
        return ("inbox", "Unprocessed capture.", ["next", "someday", "drop"], "next", later)

    if st == "REVIEW":
        score = _float(props.get("NIGHTSHIFT_SCORE"))
        has_out = bool((props.get("NIGHTSHIFT_OUTPUT") or "").strip())
        ran = _date(props.get("NIGHTSHIFT_COMPLETED"))
        bits = ["Agent output" if (ai or ran) else "Marked for review"]
        if ran:
            bits.append(f"from {_fmt(ran)}")
        if score is not None:
            bits.append(f"scored {score:.2f}")
        ctx = " ".join(bits) + f", open {age_txt}."
        if score is not None and score >= 0.75 and has_out:
            rec = "accept"
        elif age is not None and age > 60:
            rec = "drop"
        elif not ai and not ran and (age is None or age <= 30):
            rec = "next"
        else:
            rec = "someday"
        return ("review", ctx, ["accept", "next", "someday", "drop"], rec, later)

    if st == "WAITING":
        on = (props.get("WAITING_ON") or "").strip()
        ctx = f"Waiting {age_txt}" + (f", on {on}" if on else "") + "."
        future = sched if sched and sched > today else None
        rec = "defer" if future else ("someday" if age is not None and age > 60 else "next")
        return ("waiting", ctx, ["next", "defer", "someday", "drop"], rec, future or nxt + timedelta(days=7))

    slipped = (sched and sched <= today) or (dl and dl <= today)
    if is_project:
        if not slipped and age is not None and age <= 14:
            return None
        ctx = f"Open {age_txt}" + (f"; was scheduled {_fmt(sched)}" if sched and sched <= today else "") + "."
        rec = "next" if st == "NEXT" and (age is None or age <= 30) else "someday"
        return ("projects", ctx, ["next", "defer", "someday", "drop"], rec, later)

    if slipped:
        when = dl if dl and dl <= today else sched
        kind = "Deadline" if dl and dl <= today else "Scheduled"
        rel = "today" if when == today else f"{(today - when).days} days ago"
        ctx = f"{kind} {_fmt(when)} ({rel}), still {st}."
        rec = "next" if pri == "A" else ("someday" if age is not None and age > 45 else "defer")
        return ("slipped", ctx, ["next", "defer", "someday", "drop"], rec, later)

    if st == "NEXT" and not sched and (age is None or age > 14):
        ctx = f"NEXT for {age_txt}, with no date."
        rec = "next" if pri == "A" else ("someday" if age is not None and age > 60 else "defer")
        opts = ["next", "defer", "delegate" if ai else "someday", "drop"]
        if rec not in opts:
            opts[2] = rec
        return ("stale", ctx, opts, rec, later)

    if st == "TODO" and not (sched and sched > today) and (age is None or age > 30):
        ctx = f"Open {age_txt}, never started."
        rec = "next" if pri == "A" else "someday"
        opts = ["next", "delegate", "someday", "drop"] if ai else ["next", "defer", "someday", "drop"]
        return ("backlog", ctx, opts, rec, later)

    return None


def _json_block(name, obj):
    s = json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")
    s = s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f'<script type="application/json" id="{name}">{s}</script>'


def build(args):
    today = date.fromisoformat(args.today)
    nxt = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    later = nxt + timedelta(days=14)
    red = _redactor(args.redact or [])
    overrides = json.loads(Path(args.overrides).read_text()) if args.overrides else {}
    week = json.loads(Path(args.week).read_text()) if args.week else {}
    prefill = {}
    if args.prefill and Path(args.prefill).exists():
        prefill = json.loads(Path(args.prefill).read_text()).get("decisions", {})

    raw = {k: [] for k, *_ in SECTIONS}
    sources, open_count, quiet = [], 0, 0
    plan = [(p, False) for p in args.files] + [(p, True) for p in (args.projects or [])]
    multi_space = len({Path(f).resolve().parent.parent.name for f, _ in plan}) > 1
    for f, is_project in plan:
        path = Path(f)
        if not path.exists():
            continue
        space = path.resolve().parent.parent.name
        sources.append(f"{space}/{path.name}" if multi_space else path.name)
        is_inbox = path.name == "inbox.org"
        ws = _load_ws(str(path))
        for node in ws.all_nodes():
            if node.todo not in OPEN or (is_inbox and node.level != 1):
                continue
            d = _node_to_dict(node)
            props = d["properties"] or {}
            created = _created(props, d["id"])
            it = {"state": d["state"], "pri": d["priority"], "tags": d["tags"], "props": props,
                  "sched": _date(d["scheduled"]), "dl": _date(d["deadline"]),
                  "age": (today - created).days if created else None}
            if args.states and d["state"] not in args.states:
                continue
            if args.min_age and (it["age"] is None or it["age"] < args.min_age):
                continue
            open_count += 1
            # A decision made on a board in the last week is not asked again.
            reviewed = _date(props.get("LAST_REVIEWED"))
            if reviewed and (today - reviewed).days < 7:
                quiet += 1
                continue
            c = classify(it, today, nxt, later, is_project, is_inbox)
            if not c:
                quiet += 1
                continue
            section, ctx, keys, rec, when = c
            oid = d["id"]
            ov = overrides.get(oid or "", {})
            keys = list(ov.get("options") or keys)
            rec = ov.get("rec", rec)
            if rec not in keys:
                keys = keys[:3] + [rec]
            # Work often gets done without the task being closed; every task
            # row can say so. (REVIEW rows already have Accept.)
            if section != "review" and "done" not in keys:
                keys.append("done")
            when = _date(ov.get("defer")) or when
            opts = []
            for k in keys:
                lab, cons = _option(section, k, nxt, when)
                lab = (ov.get("labels") or {}).get(k, lab)
                cons = (ov.get("consequences") or {}).get(k, cons)
                opts.append({"value": k, "label": red(lab), "consequence": red(cons)})
            meta = ([space] if multi_space else []) + [d["state"]] + ([f"priority {d['priority']}"] if d["priority"] else [])
            meta += [f"{it['age']} days old" if it["age"] is not None else "age unknown"]
            if oid:
                meta.append(oid)
            raw[section].append({
                "area": red(_area(node, path.stem)),
                "title": red(d["heading"]),
                "context": red(ov["why"] + " " + ctx if ov.get("why") else ctx),
                "preview": red(_snippet(node, d["tags"])),
                "meta": meta, "options": opts, "suggested": rec,
                "apply": {"file": str(path), "orgId": oid, "state": d["state"],
                          "hk": None if oid else hashlib.sha1(d["heading"].encode()).hexdigest()[:12],
                          "defer": when.isoformat() if when else None, "next": nxt.isoformat()},
            })

    sections = []
    # Sections that sit above the task list come from --week, already shaped.
    for s in week.get("sections", []):
        rows = []
        for i, r in enumerate(s.get("rows", []), 1):
            rows.append(dict(r, id=f"{s['prefix']}{i}"))
        sections.append({"key": s["key"], "label": s["label"], "hint": s.get("hint", ""),
                         "rows": rows, "noted": s.get("noted", [])})
    for key, prefix, label, hint in SECTIONS:
        items = sorted(raw[key], key=lambda r: (r["area"].lower(), r["title"].lower()))
        if not items:
            continue
        for i, r in enumerate(items, 1):
            r["id"] = f"{prefix}{i}"
        sections.append({"key": key, "label": label, "hint": hint, "rows": items, "noted": []})
    notes = []
    if quiet:
        notes.append(f"{quiet} open items are fresh or dated in the future, so they are not asked about.")
    if any(s.endswith("inbox.org") for s in sources) and not raw["inbox"]:
        notes.append("The inbox is at zero.")
    if sections and notes:
        sections[-1]["noted"] = sections[-1]["noted"] + [{"text": " ".join(notes)}]

    total = sum(len(s["rows"]) for s in sections)
    meta = {
        "slug": args.slug, "title": args.title,
        "eyebrow": args.eyebrow or f"Weekly review · {today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}",
        "h1": args.h1 or args.title,
        "lede": args.lede or ("Every open item in your GTD that needs a call, each with a suggestion. "
                              "Choose, add a note where it helps, and save; your org files change only "
                              "after you say “apply the decisions”."),
        "asOf": args.as_of or datetime.now().strftime("%Y-%m-%d %H:%M"),
        # Row ids are positional, so a rebuild can shift them. The build stamp
        # keys the page's saved choices and is echoed into the decisions file,
        # and `apply` refuses a file saved from a different build.
        "build": datetime.now().isoformat(timespec="seconds"),
        "sources": sources, "open": open_count, "total": total,
        "path": [["Read", f"{open_count} open items · {len(sources)} files"],
                 ["Picked", f"{total} need a call"],
                 ["Your calls", "decided on this page"],
                 ["Applied", "after you say “apply the decisions”"]],
    }
    data = {"meta": meta, "sections": sections, "prefill": prefill}

    css = (ASSETS / "board.css").read_text()
    js = (ASSETS / "board.js").read_text()
    if "</script" in js.lower():
        raise SystemExit("board.js contains a literal </script")
    title = args.title.replace("&", "&amp;").replace("<", "&lt;")
    page = ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{title}</title>"
            '<link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link rel="stylesheet" href="{FONT_HREF}">'
            f"<style>{css}</style></head><body><div id=\"app\"></div>"
            f"{_json_block('data', data)}<script>{js}</script></body></html>\n")

    out = Path(args.out) if args.out else BOARDS / f"{today.isoformat()}-{args.slug}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    mapping = {r["id"]: dict(r.get("apply") or {}, title=r["title"]) for s in sections for r in s["rows"]}
    out.with_suffix(".map.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=1))

    recs = {}
    for s in sections:
        for r in s["rows"]:
            recs[r.get("suggested")] = recs.get(r.get("suggested"), 0) + 1
    print(json.dumps({"out": str(out), "bytes": len(page.encode()), "open": open_count,
                      "asked": total, "quiet": quiet,
                      "sections": {s["label"]: len(s["rows"]) for s in sections},
                      "suggestions": recs}, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------------------
# apply: saved choices -> org changes, through the adapter (locks + ledger)
# ---------------------------------------------------------------------------

def _adapter(argv):
    import subprocess
    r = subprocess.run([sys.executable, str(HERE / "org_workspace_adapter.py")] + argv,
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"error": (r.stdout or r.stderr or "no output").strip()[:300]}


def _board_rows(board):
    t = Path(board).read_text()
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', t, re.S)
    data = json.loads(m.group(1))
    return data, {r["id"]: r for s in data["sections"] for r in s.get("rows", [])}


def _ops(row, choice, note, today, someday_parent):
    """Adapter calls for one choice. Every touched task is stamped LAST_REVIEWED."""
    a = row.get("apply") or {}
    oid, f, st = a.get("orgId"), a.get("file"), a.get("state")
    if not oid or not f:
        return None
    tag = f"decision board {today}"
    stamp = [f"LAST_REVIEWED={today}"] + ([f"DECISION_NOTE={note}"] if note else [])

    def props(extra=()):
        out = []
        for p in list(extra) + stamp:
            out += ["--property", p]
        return out
    base = ["update", "--file", f, "--id", oid]
    if choice in ("done", "accept"):
        return [base + ["--state", "DONE"] + props([f"CLOSED_REASON={tag}"])]
    if choice == "drop":
        return [base + ["--state", "CANCELLED"] + props([f"CANCELLED_REASON={tag}"])]
    if choice == "next":
        return [base + ["--state", "NEXT", "--scheduled", "none"] + props()]
    if choice == "defer":
        when = _date(note) or _date(a.get("defer"))
        if not when:
            return None
        return [base + ["--state", "WAITING" if st == "WAITING" else "TODO",
                        "--scheduled", when.isoformat()] + props()]
    if choice == "someday":
        # Phase 1 spaces (DIP-0046) regenerate next_actions.org from the ledger,
        # and a move is not a ledger event: a task moved to someday.org comes
        # back on the next projection (seen 2026-09-11). There, someday is a
        # state the ledger records — DEFERRED, in place.
        space = Path(f).resolve().parent.parent
        try:
            phase1 = (space / ".datacore" / "ledger-phase").read_text().strip() == "1"
        except OSError:
            phase1 = False
        if phase1 and Path(f).name == "next_actions.org":
            return [base + ["--state", "DEFERRED", "--scheduled", "none"]
                    + props([f"SOMEDAY={tag}"])]
        target = str(Path(f).parent / "someday.org")
        move = ["move", "--from", f, "--to", target, "--id", oid]
        if someday_parent and "0-personal" in target:
            move += ["--parent-id", someday_parent]
        return [move, ["update", "--file", target, "--id", oid, "--state", "TODO",
                       "--scheduled", "none"] + props()]
    if choice == "delegate":
        return [base + props([f"DELEGATE_PENDING={tag}: needs a sprint entry with a definition of done"])]
    return None


def apply_cmd(args):
    today = args.today or date.today().isoformat()
    data, rows = _board_rows(args.board)
    saved = json.loads(Path(args.decisions).read_text())
    built, saved_build = (data.get("meta") or {}).get("build"), saved.get("build")
    if built and saved_build and built != saved_build:
        raise SystemExit(f"Refusing: these decisions were saved from build {saved_build}, "
                         f"but {args.board} is build {built}. Row ids may point at different tasks.")
    decisions = saved.get("decisions", {})
    log = Path(args.board).with_suffix(".applied.json")
    applied = json.loads(log.read_text()) if log.exists() else {}
    plan, for_claude, skipped = [], [], []
    for rid, d in decisions.items():
        ch, note = d.get("choice"), (d.get("note") or "").strip()
        row = rows.get(rid)
        if row is None:
            skipped.append((rid, "not on this board"))
            continue
        prev = applied.get(rid)
        if prev and prev.get("choice") == ch and prev.get("note", "") == note:
            continue
        has_task = bool((row.get("apply") or {}).get("orgId"))
        # A note that contradicts the choice ("it's done" on a Drop) is read by
        # Claude rather than applied blindly.
        contradicts = ch not in ("done", "accept") and re.search(r"\b(done|finished|already did)\b", note, re.I)
        if not has_task or (not ch and note) or contradicts:
            for_claude.append({"id": rid, "choice": ch, "note": note, "title": row.get("title")})
            continue
        if not ch:
            continue
        ops = _ops(row, ch, note, today, args.someday_parent)
        if ops is None:
            skipped.append((rid, f"no rule for '{ch}'"))
            continue
        plan.append((rid, ch, note, row, ops))

    counts = {}
    for _, ch, *_ in plan:
        counts[ch] = counts.get(ch, 0) + 1
    print(json.dumps({"board": args.board, "to_apply": len(plan), "by_choice": counts,
                      "for_claude": for_claude, "skipped": skipped,
                      "dry_run": bool(args.dry_run)}, ensure_ascii=False, indent=1))
    if args.dry_run:
        for rid, ch, note, row, ops in plan[:args.show]:
            print(f"  {rid:6} {ch:8} {row['title'][:70]}")
        return
    ok = failed = 0
    for rid, ch, note, row, ops in plan:
        err = None
        for op in ops:
            res = _adapter(op)
            if res.get("error"):
                err = res["error"]
                break
        if err:
            failed += 1
            print(f"  FAILED {rid} {ch}: {err}")
            continue
        ok += 1
        applied[rid] = {"choice": ch, "note": note, "at": datetime.now().isoformat(timespec="seconds")}
    log.write_text(json.dumps(applied, ensure_ascii=False, indent=1))
    print(json.dumps({"applied": ok, "failed": failed, "log": str(log)}))


def main():
    ap = argparse.ArgumentParser(description="Render a GTD review as a decision board page")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap_apply = sub.add_parser("apply", help="apply a saved <slug>.decisions.json to the org files")
    ap_apply.add_argument("--board", required=True, help="the board's .html file")
    ap_apply.add_argument("--decisions", required=True, help="the saved <slug>.decisions.json")
    ap_apply.add_argument("--today", help="YYYY-MM-DD stamped as LAST_REVIEWED (default: today)")
    ap_apply.add_argument("--someday-parent", dest="someday_parent",
                          help="heading id in 0-personal/org/someday.org to file someday items under")
    ap_apply.add_argument("--dry-run", action="store_true")
    ap_apply.add_argument("--show", type=int, default=25, help="dry run: how many planned rows to list")
    b = sub.add_parser("build")
    b.add_argument("--files", nargs="+", required=True)
    b.add_argument("--projects", nargs="*")
    b.add_argument("--today", required=True, help="YYYY-MM-DD")
    b.add_argument("--slug", required=True)
    b.add_argument("--title", required=True)
    b.add_argument("--week", help="JSON with sections that sit above the task list")
    b.add_argument("--overrides", help="JSON {org-id: {rec, options, why, labels, consequences, defer}}")
    b.add_argument("--redact", action="append", help="term replaced with 'the customer' (repeatable)")
    b.add_argument("--prefill", help="a saved <slug>.decisions.json, to revise a board")
    b.add_argument("--states", nargs="+", help="only these org states (e.g. WAITING)")
    b.add_argument("--min-age", dest="min_age", type=int, help="only items at least this many days old")
    b.add_argument("--h1")
    b.add_argument("--eyebrow")
    b.add_argument("--lede")
    b.add_argument("--as-of", dest="as_of")
    b.add_argument("--out", help="default: .datacore/state/decision-boards/<today>-<slug>.html")
    args = ap.parse_args()
    if args.cmd == "build":
        build(args)
    elif args.cmd == "apply":
        apply_cmd(args)


if __name__ == "__main__":
    main()
