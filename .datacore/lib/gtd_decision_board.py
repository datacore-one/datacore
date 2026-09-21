#!/usr/bin/env python3
"""
gtd_decision_board.py — a GTD weekly review, rendered as a decision board.

Follows .datacore/skills/decision-board: a local, PLUR-branded page with one
decision per row, a suggested answer on each, a "Note for Claude" field, and a
Save that downloads <slug>.decisions.json. The page is written to
$DATACORE_STATE/decision-boards/ (owner-only) and is never published.

Reads org files through org-workspace. Explicit application uses its recoverable adapter.
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
import base64
import hashlib
import json
import os
import stat
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from org_workspace_adapter import _load_ws, _node_to_dict  # noqa: E402
from org_transaction import serialized, watch_file, write_org_text, digest  # noqa: E402
from file_utils import atomic_write_json, atomic_write_text, file_lock  # noqa: E402

ASSETS = HERE / "decision_board"
MAX_DOCUMENT = 8 * 1024 * 1024
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
        "next": ("Do next", "Marked NEXT in its current file; its old scheduled date is cleared."),
        "defer": (f"Defer to {_fmt(when)}",
                  f"Becomes TODO, scheduled {_fmt(when)}. Write another date in the note to change it."),
        "delegate": ("Plan delegation", "Records a delegation request for review; no worker is started."),
        "someday": ("Someday", "Moved to someday.org as a passive TODO; all task content is preserved."),
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


def _private_path(raw, *, create_parent=False):
    path = Path(raw).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('decision artifacts must not use symbolic links')
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError('decision artifacts require an owner-only directory')
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('decision artifact must be an owned regular single-link file')
    return path


def _read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_DOCUMENT + 1)
    if len(raw) > MAX_DOCUMENT:
        raise ValueError('decision document exceeds size limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key in decision document')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _build_id(data):
    meta = {k: v for k, v in data['meta'].items() if k not in ('build', 'asOf')}
    return hashlib.sha256(json.dumps({'meta': meta, 'sections': data['sections']},
                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _inputs_current(inputs):
    from org_space import validate_org_write_path
    for name, expected in inputs.items():
        path = Path(name)
        if not path.is_absolute() or path != validate_org_write_path(path):
            raise ValueError('decision input path changed or is not canonical')
        if digest(watch_file(path)['before']) != expected:
            raise ValueError('reviewed input changed; rebuild the board before applying')


def _authority(inputs, *, require_projection=False):
    """Record each source's persistence model; generated input must be current."""
    from ledger_project_org import phase, rendered, ORG
    from ledger.log import read_events
    from ledger.fold import fold
    from ledger.projection_state import snapshot
    from org_space import ledger_space_for_file
    result = {}
    for name in inputs:
        path = Path(name)
        space = ledger_space_for_file(path)
        if path.parent.name == 'org' and phase(path.parent.parent) == 1 and space is None:
            raise ValueError('generated source has no available ledger')
        mode = phase(space) if space is not None else 0
        current = {'space': str(space) if space else None, 'phase': mode}
        if mode == 1 and path == (space / ORG).resolve():
            state = fold(read_events(space))
            if require_projection and any(item.edit_conflicts for item in state.items.values()):
                raise ValueError('unresolved ledger conflicts must be reconciled before review')
            if require_projection:
                # Compare against what the projector WRITES, not a bare replay
                # (see ledger_project_org.rendered). Two instants, because the
                # retention window rolls: a task that aged out since the file
                # was last rewritten is in the file and not in a render at
                # "now". The file's own mtime is when it last changed, and it
                # is current as of then. Anything else is a real difference.
                on_disk = snapshot(path.read_text(), space.name)
                instants = (time.time(), path.stat().st_mtime)
                if not any(snapshot(rendered(space, as_of=at, state=state), space.name) == on_disk
                           for at in instants):
                    raise ValueError('generated Org and ledger differ; reconcile before building a review')
            current['root'] = state.state_root()
        result[name] = current
    return result


@serialized
def build(args):
    today = date.fromisoformat(args.today)
    nxt = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    later = nxt + timedelta(days=14)
    red = _redactor(args.redact or [])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', args.slug):
        raise ValueError('invalid board slug')
    overrides = _read_json(args.overrides) if args.overrides else {}
    week = _read_json(args.week) if args.week else {}
    if not isinstance(overrides, dict) or not isinstance(week, dict):
        raise ValueError('week and overrides must be objects')
    prefill = _read_json(args.prefill) if args.prefill else None
    inputs = {}

    raw = {k: [] for k, *_ in SECTIONS}
    sources, open_count, quiet = [], 0, 0
    plan = [(p, False) for p in args.files] + [(p, True) for p in (args.projects or [])]
    multi_space = len({Path(f).resolve().parent.parent.name for f, _ in plan}) > 1
    for f, is_project in plan:
        from org_space import validate_org_write_path
        path = validate_org_write_path(f)
        if str(path) in inputs:
            raise ValueError('duplicate source file on decision board')
        inputs[str(path)] = digest(watch_file(path)['before'])
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
            from ledger_project_org import phase, ORG
            from org_space import ledger_space_for_file
            space_root = ledger_space_for_file(path)
            generated = space_root is not None and phase(space_root) == 1 and path == (space_root / ORG).resolve()
            opts = []
            for k in keys:
                lab, cons = _option(section, k, nxt, when)
                if k == 'someday' and generated:
                    lab, cons = f'Bench until {_fmt(when)}', f'DEFERRED in place, with a wake date of {_fmt(when)}.'
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
            if r.get('apply'):
                raise ValueError('week rows cannot supply task mutation targets')
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
        # Content identity binds positional rows, choices and exact input bytes.
        "schema": 2, "inputs": inputs, "authority": _authority(inputs, require_projection=True),
        "sources": sources, "open": open_count, "total": total,
        "path": [["Read", f"{open_count} open items · {len(sources)} files"],
                 ["Picked", f"{total} need a call"],
                 ["Your calls", "decided on this page"],
                 ["Applied", "after you say “apply the decisions”"]],
    }
    data = {"meta": meta, "sections": sections, "prefill": {}}
    _validate_rows(data)
    meta['build'] = _build_id(data)
    if prefill is not None:
        _validate_saved(data, prefill)
        data['prefill'] = prefill['decisions']
    # Parsing and classification must describe exactly these watched bytes.
    for name, expected in inputs.items():
        if digest(Path(name).read_bytes().decode('utf-8')) != expected:
            raise ValueError('input changed while building the board')

    css = (ASSETS / "board.css").read_text()
    js = (ASSETS / "board.js").read_text()
    if "</script" in js.lower():
        raise SystemExit("board.js contains a literal </script")
    title = args.title.replace("&", "&amp;").replace("<", "&lt;")
    script_hash = base64.b64encode(hashlib.sha256(js.encode()).digest()).decode()
    csp = f"default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    page = ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f'<meta http-equiv="Content-Security-Policy" content="{csp}"><title>{title}</title>'
            f"<style>{css}</style></head><body><div id=\"app\"></div>"
            f"{_json_block('data', data)}<script>{js}</script></body></html>\n")

    from file_utils import private_state_directory
    selected = Path(args.out) if args.out else private_state_directory('decision-boards') / f"{today.isoformat()}-{args.slug}.html"
    out = _private_path(selected, create_parent=True)
    if len(page.encode()) > MAX_DOCUMENT:
        raise ValueError('board exceeds document size limit')
    mapping = {r["id"]: dict(r.get("apply") or {}, title=r["title"]) for s in sections for r in s["rows"]}
    atomic_write_json(_private_path(out.with_suffix('.map.json')), mapping)
    atomic_write_text(out, page)

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
    # In-process calls retain the canonical transaction across a move+update.
    from org_workspace_adapter import build_parser, COMMAND_MAP
    args = build_parser().parse_args(argv)
    return COMMAND_MAP[args.command](args)


def _validate_rows(data):
    if not isinstance(data, dict) or not isinstance(data.get('meta'), dict) or not isinstance(data.get('sections'), list):
        raise ValueError('invalid board document')
    rows, sections = {}, set()
    for section in data['sections']:
        if not isinstance(section, dict) or not isinstance(section.get('key'), str) or section['key'] in sections:
            raise ValueError('duplicate or invalid section identity')
        sections.add(section['key'])
        for row in section.get('rows', []):
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] or row['id'] in rows:
                raise ValueError('duplicate or invalid row identity')
            options = row.get('options')
            if not isinstance(options, list) or not options or any(not isinstance(o, dict) or not isinstance(o.get('value'), str) for o in options):
                raise ValueError('invalid row options')
            keys = [o['value'] for o in options]
            if len(keys) != len(set(keys)) or (row.get('suggested') is not None and row['suggested'] not in keys):
                raise ValueError('ambiguous row options')
            rows[row['id']] = row
    return rows


def _board_rows(board):
    path = _private_path(board)
    if path.stat().st_size > MAX_DOCUMENT:
        raise ValueError('board exceeds document size limit')
    match = re.search(r'<script type="application/json" id="data">(.*?)</script>', path.read_text(), re.S)
    if not match:
        raise ValueError('board has no embedded data')
    data = json.loads(match.group(1))
    return data, _validate_rows(data)


def _validate_saved(data, saved):
    meta, rows = data['meta'], _validate_rows(data)
    if meta.get('schema') != 2 or not isinstance(meta.get('inputs'), dict):
        raise ValueError('rebuild this legacy board before applying')
    if not isinstance(saved, dict) or not meta.get('build') or saved.get('build') != meta['build'] or saved.get('board') != meta.get('slug'):
        raise ValueError('saved decisions do not belong to this board build')
    if meta['build'] != _build_id(data):
        raise ValueError('board content does not match its build identity')
    decisions = saved.get('decisions')
    if not isinstance(decisions, dict):
        raise ValueError('decisions must be an object')
    for rid, decision in decisions.items():
        if rid not in rows or not isinstance(decision, dict):
            raise ValueError('unknown row or malformed decision')
        choice, note = decision.get('choice'), decision.get('note', '')
        if not isinstance(note, str) or len(note) > 8000 or '\x00' in note:
            raise ValueError('invalid decision note')
        if choice is not None and choice not in [o['value'] for o in rows[rid]['options']]:
            raise ValueError('choice was not offered on this board')
    return decisions


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
        from ledger_project_org import phase, ORG
        from org_space import ledger_space_for_file
        space = ledger_space_for_file(f)
        if space is not None and phase(space) == 1 and Path(f).resolve() == (space / ORG).resolve():
            when = _date(note) or _date(a.get('defer'))
            if not when or when <= date.fromisoformat(today):
                raise ValueError('benching a generated task requires a future wake date')
            return [base + ['--state', 'DEFERRED', '--scheduled', when.isoformat()]
                    + props([f'SOMEDAY={tag}'])]
        target = str(Path(f).parent / "someday.org")
        move = ["move", "--from", f, "--to", target, "--id", oid]
        if someday_parent and Path(target).parent.parent.name == "0-personal":
            move += ["--parent-id", someday_parent]
        return [move, ["update", "--file", target, "--id", oid, "--state", "TODO",
                       "--scheduled", "none"] + props()]
    if choice == "delegate":
        return [base + props([f"DELEGATE_PENDING={tag}: needs a sprint entry with a definition of done"])]
    return None


@serialized
def _prepare(data, rows, saved, receipt, today, someday_parent):
    decisions = _validate_saved(data, saved)
    meta = data['meta']
    if receipt:
        if receipt.get('build') != meta['build'] or receipt.get('version') != 2:
            raise ValueError('receipt belongs to another board; preserve it and build a new board')
        if receipt.get('pending'):
            raise ValueError('an interrupted decision needs reconciliation; preserve its receipt and inspect task and ledger state before making a new board')
    expected = receipt.get('inputs', meta['inputs'])
    _inputs_current(expected)
    if _authority(expected) != receipt.get('authority', meta.get('authority')):
        raise ValueError('reviewed ledger or persistence model changed; rebuild the board')
    plan, for_claude = [], []
    for rid, decision in decisions.items():
        choice, note = decision.get('choice'), decision.get('note', '').strip()
        previous = receipt.get('completed', {}).get(rid)
        if previous:
            if previous['choice'] != choice or previous['note'] != note:
                raise ValueError('this decision was already applied; rebuild from the current task before revising it')
            continue
        row = rows[rid]
        target = row.get('apply') or {}
        if not target.get('orgId') or (not choice and note) or (choice not in ('done', 'accept') and re.search(r'\b(done|finished|already did)\b', note, re.I)):
            for_claude.append({'id': rid, 'choice': choice, 'note': note, 'title': row.get('title')})
            continue
        if not choice:
            continue
        if target.get('file') not in meta['inputs']:
            raise ValueError('task target is outside the reviewed input set')
        ops = _ops(row, choice, note, today, someday_parent)
        if not ops:
            raise ValueError('choice has no supported application rule')
        plan.append((rid, choice, note, ops))
    return plan, for_claude, expected


@serialized
def _apply_row(log, receipt, row):
    rid, choice, note, ops = row
    _inputs_current(receipt['inputs'])
    # The pending intent was durably written before entering this transaction.
    # Org changes and completion receipt recover together. Append-only ledger
    # effects are never falsely claimed to be rolled back: pending blocks replay.
    if _authority(receipt['inputs']) != receipt['authority']:
        raise ValueError('reviewed ledger or persistence model changed; rebuild the board')
    watch_file(log)
    from ledger.projection_state import reviewed_state
    roots = {entry['space']: entry['root'] for entry in receipt['authority'].values() if 'root' in entry}
    with reviewed_state(roots):
        for op in ops:
            result = _adapter(op)
            success_key = 'moved' if op[0] == 'move' else 'updated'
            if not isinstance(result, dict) or result.get('error') or result.get(success_key) is not True:
                raise ValueError('adapter did not confirm the requested mutation; reconciliation required')
    from org_transaction import changed_files
    inputs = dict(receipt['inputs'])
    for name, sha in changed_files().items():
        if name.endswith('.org'):
            inputs[name] = sha
    completed = dict(receipt['completed'])
    completed[rid] = {'choice': choice, 'note': note, 'at': datetime.now().isoformat()}
    updated = dict(receipt, pending=None, completed=completed, inputs=inputs, authority=_authority(inputs))
    write_org_text(log, json.dumps(updated, ensure_ascii=False, indent=1) + '\n')
    return updated


def apply_cmd(args):
    today = date.fromisoformat(args.today or date.today().isoformat()).isoformat()
    data, rows = _board_rows(args.board)
    saved = _read_json(args.decisions)
    log = _private_path(Path(args.board).with_suffix('.applied.json'))
    # A private lock outside the output paths avoids following board-side links.
    from hook_state import state_path
    lock = state_path('decision-board', str(log))
    with file_lock(lock, timeout=30):
        receipt = _read_json(log) if log.exists() else {}
        if not isinstance(receipt, dict):
            raise ValueError('invalid application receipt')
        plan, for_claude, expected = _prepare(data, rows, saved, receipt, today, args.someday_parent)
        print(json.dumps({'board': str(args.board), 'to_apply': len(plan),
                          'for_claude': for_claude, 'dry_run': bool(args.dry_run)}, ensure_ascii=False))
        if args.dry_run:
            return
        if not receipt:
            receipt = {'version': 2, 'build': data['meta']['build'], 'inputs': expected, 'authority': data['meta']['authority'], 'completed': {}, 'pending': None}
        for row in plan:
            rid, choice, note, ops = row
            receipt = dict(receipt, pending={'id': rid, 'choice': choice, 'note': note, 'operations': ops})
            atomic_write_json(log, receipt)
            receipt = _apply_row(log, receipt, row)
        print(json.dumps({'applied': len(plan), 'failed': 0, 'log': str(log)}))


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
    b.add_argument("--out", help="default: $DATACORE_STATE/decision-boards/<today>-<slug>.html (owner-only)")
    args = ap.parse_args()
    if args.cmd == "build":
        build(args)
    elif args.cmd == "apply":
        apply_cmd(args)


if __name__ == "__main__":
    main()
