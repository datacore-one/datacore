#!/usr/bin/env python3
"""org_workspace_adapter.py — CLI adapter wrapping org-workspace for GTD MCP tools.

Replaces org_parser.py as the backend for .datacore/modules/gtd/tools/index.js.
All commands output JSON to stdout.

Usage:
    python3 org_workspace_adapter.py <command> [options]

Commands:
    count       Count active tasks in an org file
    list        List tasks with optional filters
    add         Add a new task to a file
    complete    Mark a task DONE by title or ID
    agenda      List tasks scheduled within N days
    deadlines   List tasks with deadlines within N days
    archive-done Archive terminal-state tasks older than N days
    project-health Analyze project health (stuck, empty, etc.)
    effort-summary Aggregate effort estimates by focus area
    duplicates  Find near-duplicate task titles
    ensure-ids  Add :ID: properties to tasks that lack them
    write-clock Write a CLOCK entry to a task's LOGBOOK
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tag_utils import sanitize_org_tags  # noqa: E402


def _load_ws(*paths: str, state_config=None):
    """Load an OrgWorkspace from one or more file paths."""
    from org_workspace import OrgWorkspace
    ws = OrgWorkspace(state_config=state_config)
    for p in paths:
        path = Path(p).resolve()
        if path.exists():
            ws.load(path)
    return ws


def _node_to_dict(node) -> dict:
    """Convert a NodeView to a JSON-serializable dict."""
    from org_workspace.query import _to_date
    sched = _to_date(node.scheduled)
    deadline = _to_date(node.deadline)
    closed = _to_date(node.closed)
    return {
        "id": node.id(),
        "heading": node.heading,
        "state": node.todo,
        "priority": node.priority,
        "tags": sorted(node.tags),
        "level": node.level,
        "path": str(node.path),
        "scheduled": sched.isoformat() if sched else None,
        "deadline": deadline.isoformat() if deadline else None,
        "closed": closed.isoformat() if closed else None,
        "properties": dict(node.properties),
    }


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------

def cmd_count(args):
    """Count active (non-terminal) tasks in one or more files."""
    ws = _load_ws(*args.files)
    count = 0
    terminal = ws.state_config.terminal_states
    for node in ws.all_nodes():
        if node.todo and node.todo not in terminal:
            count += 1
    return {"count": count, "files": args.files}


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def cmd_list(args):
    """List tasks from a file with optional state/tag/limit filters."""
    ws = _load_ws(args.file)
    states = [s.strip() for s in args.states.split(",")] if args.states else None
    tags = [t.strip().strip(":") for t in args.tags.split(",")] if args.tags else None

    results = []
    for node in ws.all_nodes():
        if states and node.todo not in states:
            continue
        if tags:
            node_tags = {t.strip(":") for t in node.tags}
            if not any(t in node_tags for t in tags):
                continue
        results.append(_node_to_dict(node))
        if args.limit and len(results) >= args.limit:
            break

    return {"count": len(results), "tasks": results}


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

def cmd_add(args):
    """Add a new task to an org file."""
    ws = _load_ws(args.file)
    file_path = Path(args.file).resolve()

    # Auto-CREATED timestamp
    if args.created:
        created = args.created
    else:
        now = datetime.now()
        _days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        created = f"[{now.strftime('%Y-%m-%d')} {_days[now.weekday()]} {now.strftime('%H:%M')}]"

    # Parse tags. Sanitised before they reach the file: an org tag carrying a
    # hyphen voids the entire tag string on that heading, silently.
    tags = None
    if args.tags:
        raw = args.tags.strip(":")
        tags = sanitize_org_tags(raw.replace(",", ":").split(":")) or None

    # Priority belongs in the heading as [#A], not in PROPERTIES
    heading = f"[#{args.priority}] {args.heading}" if args.priority else args.heading

    # Find parent node
    parent_node = None
    if getattr(args, 'parent_id', None):
        parent_node = ws.find_by_id(args.parent_id)
        if parent_node is None:
            return {"error": f"Parent ID not found: {args.parent_id}"}
    elif getattr(args, 'parent', None):
        search = args.parent.lower()
        for n in ws.all_nodes():
            if n.heading.lower() == search:
                parent_node = n
                break
        if parent_node is None:
            return {"error": f"Parent heading not found: {args.parent}"}
    else:
        # Default: first level-1 heading
        for n in ws.all_nodes():
            if n.level == 1:
                parent_node = n
                break

    # Build extra properties
    extra_props = {}
    extra_props["CREATED"] = created
    if getattr(args, 'property', None):
        for prop in args.property:
            if "=" in prop:
                k, v = prop.split("=", 1)
                extra_props[k] = v

    # Body text
    body = getattr(args, 'body', None)

    node = ws.create_node(
        file_path,
        heading=heading,
        state=args.state or "TODO",
        tags=tags,
        parent=parent_node,
        body=body,
        **extra_props,
    )

    node_id = node.id()

    # SCHEDULED must be a planning keyword BEFORE :PROPERTIES:, not inside it.
    if args.scheduled:
        try:
            sched_dt = datetime.strptime(args.scheduled, "%Y-%m-%d")
        except ValueError:
            return {"error": f"Invalid scheduled date format: '{args.scheduled}'. Use YYYY-MM-DD."}
        _wdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        day_name = _wdays[sched_dt.weekday()]
        sched_line = f"SCHEDULED: <{args.scheduled} {day_name}>"

        ws.save(file_path)
        lines = file_path.read_text().split("\n")
        id_line_idx = next(
            (i for i, l in enumerate(lines) if f":ID: {node_id}" in l), None
        )
        if id_line_idx is None:
            return {"error": "SCHEDULED not inserted: could not locate node ID in file after creation", "id": node_id}
        for j in range(id_line_idx, -1, -1):
            if ":PROPERTIES:" in lines[j]:
                lines.insert(j, sched_line)
                break
        file_path.write_text("\n".join(lines))
        ws.reload(file_path)

    return {"added": True, "id": node_id, "heading": args.heading}


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------

def cmd_complete(args):
    """Mark a task DONE, matched by title substring or ID."""
    ws = _load_ws(args.file)

    if not getattr(args, 'id', None) and not getattr(args, 'title', None):
        return {"error": "Must provide --title or --id"}

    node = None
    if args.id:
        node = ws.find_by_id(args.id)
    else:
        # Title substring match (case-insensitive)
        search = args.title.lower()
        best = None
        for n in ws.all_nodes():
            if n.todo and search in n.heading.lower():
                best = n
                break
        node = best

    if node is None:
        return {"error": f"No matching task found for '{args.title or args.id}'"}

    if ws.state_config.is_terminal(node.todo or ""):
        return {"error": f"Task already in terminal state: {node.todo}", "heading": node.heading}

    file_path = Path(args.file).resolve()
    ws.transition(node, "DONE")
    ws.save(file_path)

    return {"completed": True, "heading": node.heading, "id": node.id()}


# ---------------------------------------------------------------------------
# agenda
# ---------------------------------------------------------------------------

def cmd_agenda(args):
    """List tasks SCHEDULED within the next N days."""
    from org_workspace import Query
    ws = _load_ws(args.file)
    q = Query(ws)
    nodes = q.agenda(days=args.days)
    return {"days": args.days, "count": len(nodes), "tasks": [_node_to_dict(n) for n in nodes]}


# ---------------------------------------------------------------------------
# deadlines
# ---------------------------------------------------------------------------

def cmd_deadlines(args):
    """List tasks with DEADLINE within the next N days."""
    from org_workspace import Query
    from org_workspace.query import _to_date
    ws = _load_ws(args.file)
    q = Query(ws)
    nodes = q.deadlines(days=args.days)
    today = date.today()

    results = []
    for n in nodes:
        d = _to_date(n.deadline)
        days_remaining = (d - today).days if d else None
        task = _node_to_dict(n)
        task["days_remaining"] = days_remaining
        task["overdue"] = (days_remaining is not None and days_remaining < 0)
        task["urgency"] = (
            "overdue" if days_remaining is not None and days_remaining < 0
            else "critical" if days_remaining is not None and days_remaining <= 3
            else "soon" if days_remaining is not None and days_remaining <= 7
            else "upcoming"
        )
        results.append(task)

    return {
        "days_window": args.days,
        "total": len(results),
        "overdue": sum(1 for r in results if r["overdue"]),
        "tasks": results,
    }


# ---------------------------------------------------------------------------
# archive-done
# ---------------------------------------------------------------------------

def cmd_archive_done(args):
    """Archive terminal-state tasks older than N days."""
    from org_workspace import archive_done, default_archive_path
    ws = _load_ws(args.file)
    file_path = Path(args.file).resolve()

    if args.dry_run:
        # Preview: find candidates without archiving
        from org_workspace.query import _to_date
        cutoff = date.today() - timedelta(days=args.min_age)
        candidates = []
        for node in ws.all_nodes():
            if not node.todo or not ws.state_config.is_terminal(node.todo):
                continue
            if node.level < 3:
                continue
            closed = _to_date(node.closed)
            if closed and closed < cutoff:
                age = (date.today() - closed).days
                candidates.append({
                    "heading": node.heading,
                    "id": node.id(),
                    "closed_date": closed.isoformat(),
                    "age_days": age,
                })
        return {"dry_run": True, "total_candidates": len(candidates), "candidates": candidates}

    # Ensure archive file loaded if it exists
    archive_path = default_archive_path(file_path)
    if archive_path.exists():
        ws.load(archive_path)

    archived = archive_done(ws, older_than_days=args.min_age)
    ws.save_all()

    return {"dry_run": False, "archived_count": len(archived), "archived": archived}


# ---------------------------------------------------------------------------
# project-health
# ---------------------------------------------------------------------------

def cmd_project_health(args):
    """Analyze project health: stuck, empty, no next actions."""
    ws = _load_ws(args.file)
    terminal = ws.state_config.terminal_states
    stale_days = getattr(args, 'stale_days', None)

    # Pre-build children index: project_node_id -> child state counts.
    # Single O(N) pass instead of O(N*M) nested iteration.
    all_nodes = list(ws.all_nodes())
    project_children: dict[str, dict] = {}  # heading -> counts

    for node in all_nodes:
        if node.level <= 2 or not node.todo:
            continue
        # Walk up to find the level-2 ancestor (project node)
        parent = node.parent
        while parent and parent.level > 2:
            parent = parent.parent
        if parent and parent.level == 2 and parent.heading not in (None, ""):
            key = parent.heading
            if key not in project_children:
                project_children[key] = {"total": 0, "todo": 0, "next": 0, "waiting": 0, "done": 0}
            cs = node.todo.lower()
            project_children[key]["total"] += 1
            if cs in project_children[key]:
                project_children[key][cs] += 1

    # Build stale threshold if requested
    stale_cutoff = None
    if stale_days is not None:
        from datetime import timedelta
        stale_cutoff = date.today() - timedelta(days=stale_days)

    projects = []
    for node in all_nodes:
        if node.level != 2:
            continue
        state = node.todo or ""
        if state in terminal:
            continue

        children = project_children.get(node.heading, {"total": 0, "todo": 0, "next": 0, "waiting": 0, "done": 0})

        issues = []
        if children["total"] > 0 and children["next"] == 0 and children["todo"] == 0:
            issues.append("no_active_tasks")
        if children["total"] == 0:
            issues.append("empty_project")
        if children["waiting"] > 0 and children["next"] == 0 and children["todo"] == 0:
            issues.append("all_waiting")
        if stale_cutoff and node.scheduled:
            from org_workspace.query import _to_date
            sched = _to_date(node.scheduled)
            if sched and sched < stale_cutoff:
                issues.append("stale")

        # Get parent (focus area) heading
        focus_area = node.parent.heading if node.parent else None

        projects.append({
            "name": node.heading,
            "id": node.id(),
            "state": state or None,
            "focus_area": focus_area,
            "tasks": children,
            "issues": issues,
            "healthy": len(issues) == 0,
        })

    healthy = [p for p in projects if p["healthy"]]
    stuck = [p for p in projects if not p["healthy"]]

    return {
        "total_projects": len(projects),
        "healthy": len(healthy),
        "stuck": len(stuck),
        "stuck_projects": stuck,
    }


# ---------------------------------------------------------------------------
# effort-summary
# ---------------------------------------------------------------------------

def cmd_effort_summary(args):
    """Aggregate effort estimates by focus area."""
    ws = _load_ws(args.file)
    states = [s.strip() for s in args.states.split(",")] if args.states else ["TODO", "NEXT"]

    by_area: dict[str, dict] = {}
    total_minutes = 0
    task_count = 0

    for node in ws.all_nodes():
        if node.todo not in states:
            continue

        effort_str = node.properties.get("EFFORT")
        if not effort_str:
            continue

        minutes = _parse_effort(effort_str)
        if minutes is None:
            continue

        # Walk up to level-1 heading for focus area
        focus_area = "Uncategorized"
        parent = node.parent
        while parent:
            if parent.level == 1:
                focus_area = parent.heading
                break
            parent = parent.parent

        if focus_area not in by_area:
            by_area[focus_area] = {"minutes": 0, "tasks": 0}
        by_area[focus_area]["minutes"] += minutes
        by_area[focus_area]["tasks"] += 1
        total_minutes += minutes
        task_count += 1

    areas = [
        {
            "focus_area": name,
            "hours": round(data["minutes"] / 60, 1),
            "tasks": data["tasks"],
        }
        for name, data in sorted(by_area.items(), key=lambda x: -x[1]["minutes"])
    ]

    return {
        "total_hours": round(total_minutes / 60, 1),
        "total_tasks_with_effort": task_count,
        "by_focus_area": areas,
    }


def _parse_effort(s: str) -> int | None:
    """Parse effort string to minutes. Handles H:MM and plain N formats."""
    import re
    s = s.strip()
    m = re.match(r"^(\d+):(\d{2})$", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.match(r"^(\d+)$", s)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# duplicates
# ---------------------------------------------------------------------------

def cmd_duplicates(args):
    """Find near-duplicate task titles using SequenceMatcher."""
    from difflib import SequenceMatcher
    ws = _load_ws(args.file)
    terminal = ws.state_config.terminal_states
    threshold = args.threshold

    query_lower = args.title.lower()
    duplicates = []

    for node in ws.all_nodes():
        if not node.todo or node.todo in terminal:
            continue
        existing_lower = node.heading.lower()
        ratio = SequenceMatcher(None, query_lower, existing_lower).ratio()
        if ratio >= threshold:
            d = _node_to_dict(node)
            d["similarity"] = round(ratio, 2)
            duplicates.append(d)

    duplicates.sort(key=lambda x: -x["similarity"])
    return {
        "query": args.title,
        "threshold": threshold,
        "has_duplicates": len(duplicates) > 0,
        "duplicates": duplicates[:10],
    }


# ---------------------------------------------------------------------------
# ensure-ids
# ---------------------------------------------------------------------------

def cmd_ensure_ids(args):
    """Add :ID: properties to tasks that lack them.

    Uses workspace.set_property() so mutations go through the official API
    (read-copy-merge-assign protocol, dirty tracking). After saving, reloads
    so the ID index is updated for any subsequent in-process lookups.

    Handles duplicate headings by using an incrementing disambiguator so that
    tasks with identical text get unique IDs.
    """
    from org_workspace.identifiers import generate_id
    ws = _load_ws(args.file)
    file_path = Path(args.file).resolve()

    # Seed seen_ids with existing IDs so we never collide with them.
    seen_ids: set[str] = set()
    for node in ws.all_nodes():
        if node.id():
            seen_ids.add(node.id())

    # Track how many times each heading has been seen (for disambiguation).
    heading_counts: dict[str, int] = {}

    pending = []
    for node in ws.all_nodes():
        if not node.todo:
            continue
        if node.id():
            continue
        # Count occurrences of this heading so far to build disambiguator.
        count = heading_counts.get(node.heading, 0)
        heading_counts[node.heading] = count + 1
        disambiguator = str(count) if count > 0 else None
        new_id = generate_id(node.heading, disambiguator=disambiguator)
        # Fallback: keep incrementing until unique (handles hash collisions).
        extra = count
        while new_id in seen_ids:
            extra += 1
            new_id = generate_id(node.heading, disambiguator=str(extra))
        seen_ids.add(new_id)
        pending.append((node, new_id))

    added = []
    for node, new_id in pending:
        ws.set_property(node, "ID", new_id)   # official mutation: marks dirty
        added.append({"heading": node.heading, "id": new_id})

    if added:
        ws.save(file_path)
        ws.reload(file_path)  # updates ID index so find_by_id() works immediately

    return {"added_count": len(added), "nodes": added}


# ---------------------------------------------------------------------------
# write-clock
# ---------------------------------------------------------------------------

def cmd_write_clock(args):
    """Write a CLOCK entry to a task's LOGBOOK, matched by title or ID."""
    from org_workspace.log import add_clock_entry
    ws = _load_ws(args.file)
    file_path = Path(args.file).resolve()

    if not getattr(args, 'id', None) and not getattr(args, 'title', None):
        return {"error": "Must provide --title or --id"}

    # Find node
    node_view = None
    if getattr(args, 'id', None):
        node_view = ws.find_by_id(args.id)
    elif getattr(args, 'title', None):
        search = args.title.lower()
        for n in ws.all_nodes():
            if n.todo and search in n.heading.lower():
                node_view = n
                break

    if node_view is None:
        return {"error": f"No matching task found for '{getattr(args, 'title', None) or getattr(args, 'id', None)}'"}

    # Parse times
    try:
        start_dt = datetime.fromisoformat(args.start)
        end_dt = datetime.fromisoformat(args.end)
    except ValueError as e:
        return {"error": f"Invalid time format: {e}"}

    add_clock_entry(node_view._node, start_dt, end_dt)
    # No public write_clock API; mark dirty then save.
    # If _mark_dirty is removed in a future library version, reload+save still works.
    try:
        ws._mark_dirty(file_path)
    except AttributeError:
        ws.reload(file_path)  # fallback: force re-parse so save() sees changes
    ws.save(file_path)

    duration = end_dt - start_dt
    hours, remainder = divmod(int(duration.total_seconds()), 3600)
    minutes = remainder // 60

    return {
        "logged": True,
        "heading": node_view.heading,
        "start": args.start,
        "end": args.end,
        "duration": f"{hours}:{minutes:02d}",
    }


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------

def cmd_move(args):
    """Move a task from one file to another, preserving all properties and body."""
    from_path = Path(args.source).resolve()
    to_path = Path(args.target).resolve()

    ws = _load_ws(args.source, args.target)

    # Find node in source
    node = None
    if getattr(args, 'id', None):
        node = ws.find_by_id(args.id)
    elif getattr(args, 'title', None):
        search = args.title.lower()
        for n in ws.all_nodes():
            if n.path == from_path and n.todo and search in n.heading.lower():
                node = n
                break

    if node is None:
        return {"error": f"Task not found in {args.source}: '{args.id or args.title}'"}

    if node.path != from_path:
        return {"error": f"Task found but not in source file {args.source}"}

    # Find target parent
    target_parent = None
    if getattr(args, 'parent_id', None):
        target_parent = ws.find_by_id(args.parent_id)
        if target_parent is None:
            return {"error": f"Target parent ID not found: {args.parent_id}"}
    elif getattr(args, 'parent', None):
        search = args.parent.lower()
        for n in ws.all_nodes():
            if n.path == to_path and n.heading.lower() == search:
                target_parent = n
                break
        if target_parent is None:
            return {"error": f"Target parent not found in {args.target}: '{args.parent}'"}

    # Capture parent heading before refile (refile reloads, making NodeViews stale)
    parent_heading = target_parent.heading if target_parent else None

    # Refile
    refiled = ws.refile(node, to_path, target_parent)
    ws.save_all()

    return {
        "moved": True,
        "id": refiled.id(),
        "heading": refiled.heading,
        "from": str(from_path),
        "to": str(to_path),
        "parent": parent_heading,
    }


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

def cmd_show(args):
    """Show full details of a task including body and all properties."""
    ws = _load_ws(args.file)

    node = None
    if getattr(args, 'id', None):
        node = ws.find_by_id(args.id)
    elif getattr(args, 'title', None):
        search = args.title.lower()
        for n in ws.all_nodes():
            if n.todo and search in n.heading.lower():
                node = n
                break

    if node is None:
        return {"error": f"Task not found: '{args.id or args.title}'"}

    result = _node_to_dict(node)
    result["body"] = node.body.strip() if node.body else ""
    result["children"] = [
        {"heading": c.heading, "state": c.todo, "id": c.id()}
        for c in node.children
    ]
    return result


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def cmd_update(args):
    """Update fields on an existing task."""
    ws = _load_ws(args.file)
    file_path = Path(args.file).resolve()

    # Find node
    node = None
    if getattr(args, 'id', None):
        node = ws.find_by_id(args.id)
    elif getattr(args, 'title', None):
        search = args.title.lower()
        for n in ws.all_nodes():
            if n.todo and search in n.heading.lower():
                node = n
                break

    if node is None:
        return {"error": f"Task not found: '{args.id or args.title}'"}

    changes = []

    # State transition
    if args.state:
        ws.transition(node, args.state)
        changes.append(f"state→{args.state}")

    # Tags
    if args.tags:
        raw = args.tags.strip(":")
        tag_list = [t for t in raw.replace(",", ":").split(":") if t]
        ws.set_tags(node, tag_list)
        changes.append(f"tags→{tag_list}")

    # Properties
    if getattr(args, 'property', None):
        for prop in args.property:
            if "=" in prop:
                k, v = prop.split("=", 1)
                ws.set_property(node, k, v)
                changes.append(f"{k}={v}")

    # Scheduled
    if args.scheduled:
        try:
            sched_dt = datetime.strptime(args.scheduled, "%Y-%m-%d").date()
        except ValueError:
            return {"error": f"Invalid date: '{args.scheduled}'. Use YYYY-MM-DD."}
        ws.set_scheduled(node, sched_dt)
        changes.append(f"scheduled→{args.scheduled}")

    # Heading
    if getattr(args, 'new_heading', None):
        ws.set_heading(node, args.new_heading)
        changes.append(f"heading→{args.new_heading}")

    ws.save(file_path)

    return {
        "updated": True,
        "id": node.id(),
        "heading": node.heading,
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="org_workspace_adapter — CLI wrapper for GTD MCP tools"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # count
    p = sub.add_parser("count", help="Count active tasks")
    p.add_argument("--files", nargs="+", required=True, metavar="FILE")

    # list
    p = sub.add_parser("list", help="List tasks")
    p.add_argument("--file", required=True)
    p.add_argument("--states", help="Comma-separated states (e.g. TODO,NEXT)")
    p.add_argument("--tags", help="Comma-separated tags to filter")
    p.add_argument("--limit", type=int)

    # add
    p = sub.add_parser("add", help="Add a new task")
    p.add_argument("--file", required=True)
    p.add_argument("--heading", required=True)
    p.add_argument("--state", default="TODO")
    p.add_argument("--tags", help="Tags in :tag1:tag2: or tag1,tag2 format")
    p.add_argument("--scheduled", help="YYYY-MM-DD")
    p.add_argument("--priority", choices=["A", "B", "C"])
    p.add_argument("--created", help="Override CREATED timestamp")
    p.add_argument("--body", help="Task body text (use \\n for newlines)")
    p.add_argument("--property", action="append", metavar="KEY=VALUE",
                   help="Extra property (repeatable)")
    p.add_argument("--parent", help="Parent heading to nest under")
    p.add_argument("--parent-id", dest="parent_id", help="Parent node ID to nest under")

    # complete
    p = sub.add_parser("complete", help="Mark task DONE")
    p.add_argument("--file", required=True)
    p.add_argument("--title", help="Title substring to match")
    p.add_argument("--id", help="Task :ID: to match")

    # agenda
    p = sub.add_parser("agenda", help="Scheduled tasks within N days")
    p.add_argument("--file", required=True)
    p.add_argument("--days", type=int, default=7)

    # deadlines
    p = sub.add_parser("deadlines", help="Deadline tasks within N days")
    p.add_argument("--file", required=True)
    p.add_argument("--days", type=int, default=14)

    # archive-done
    p = sub.add_parser("archive-done", help="Archive old DONE tasks")
    p.add_argument("--file", required=True)
    p.add_argument("--min-age", type=int, default=30, dest="min_age")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")

    # project-health
    p = sub.add_parser("project-health", help="Analyze project health")
    p.add_argument("--file", required=True)
    p.add_argument("--stale-days", type=int, default=None, dest="stale_days",
                   help="Flag projects with no activity in N days as stale")

    # effort-summary
    p = sub.add_parser("effort-summary", help="Aggregate effort estimates")
    p.add_argument("--file", required=True)
    p.add_argument("--states", help="Comma-separated states (default: TODO,NEXT)")

    # duplicates
    p = sub.add_parser("duplicates", help="Find near-duplicate tasks")
    p.add_argument("--file", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--threshold", type=float, default=0.7)

    # ensure-ids
    p = sub.add_parser("ensure-ids", help="Add IDs to tasks missing them")
    p.add_argument("--file", required=True)

    # write-clock
    p = sub.add_parser("write-clock", help="Write CLOCK entry to LOGBOOK")
    p.add_argument("--file", required=True)
    p.add_argument("--title", help="Title substring to match")
    p.add_argument("--id", help="Task :ID: to match")
    p.add_argument("--start", required=True, help="ISO datetime: YYYY-MM-DDTHH:MM")
    p.add_argument("--end", required=True, help="ISO datetime: YYYY-MM-DDTHH:MM")

    # move
    p = sub.add_parser("move", help="Move task between files")
    p.add_argument("--from", required=True, dest="source", help="Source org file")
    p.add_argument("--to", required=True, dest="target", help="Target org file")
    p.add_argument("--title", help="Title substring to match in source")
    p.add_argument("--id", help="Task :ID: to match in source")
    p.add_argument("--parent", help="Target parent heading")
    p.add_argument("--parent-id", dest="parent_id", help="Target parent node ID")

    # show
    p = sub.add_parser("show", help="Show full task details")
    p.add_argument("--file", required=True)
    p.add_argument("--title", help="Title substring to match")
    p.add_argument("--id", help="Task :ID: to match")

    # update
    p = sub.add_parser("update", help="Update fields on existing task")
    p.add_argument("--file", required=True)
    p.add_argument("--title", help="Title substring to match")
    p.add_argument("--id", help="Task :ID: to match")
    p.add_argument("--state", help="New state (TODO, NEXT, WAITING, DONE)")
    p.add_argument("--tags", help="Replace tags (:tag1:tag2: format)")
    p.add_argument("--scheduled", help="YYYY-MM-DD")
    p.add_argument("--heading", dest="new_heading", help="New heading text")
    p.add_argument("--property", action="append", metavar="KEY=VALUE",
                   help="Set property (repeatable)")

    return parser


COMMAND_MAP = {
    "count": cmd_count,
    "list": cmd_list,
    "add": cmd_add,
    "complete": cmd_complete,
    "agenda": cmd_agenda,
    "deadlines": cmd_deadlines,
    "archive-done": cmd_archive_done,
    "project-health": cmd_project_health,
    "effort-summary": cmd_effort_summary,
    "duplicates": cmd_duplicates,
    "ensure-ids": cmd_ensure_ids,
    "write-clock": cmd_write_clock,
    "move": cmd_move,
    "show": cmd_show,
    "update": cmd_update,
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    cmd_fn = COMMAND_MAP.get(args.command)
    if cmd_fn is None:
        print(json.dumps({"error": f"Unknown command: {args.command}"}))
        sys.exit(1)

    try:
        result = cmd_fn(args)
    except Exception as e:
        import traceback
        result = {
            "error": str(e),
            "detail": traceback.format_exc()[-500:],
        }

    print(json.dumps(result))


if __name__ == "__main__":
    main()
