#!/usr/bin/env python3
"""Standup <-> org-mode task sync.

Bidirectional sync between markdown standup checkboxes and org tasks
tagged :standup: in next_actions.org.

Usage:
    python3 .datacore/lib/standup_sync.py carryover --space [path] --contributor [name]
    python3 .datacore/lib/standup_sync.py check-off --space [path] --id [task-id]
    python3 .datacore/lib/standup_sync.py create --space [path] --contributor [name] --text [item]
"""

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from org_workspace import OrgWorkspace, Query


def get_standup_tasks(space_path: str, contributor: str) -> list[dict]:
    """Get all :standup: tagged tasks for a contributor from next_actions.org."""
    org_file = Path(space_path) / "org" / "next_actions.org"
    if not org_file.exists():
        return []

    ws = OrgWorkspace()
    ws.load(str(org_file))
    q = Query(ws)

    tasks = []
    for node in q.by_tag("standup"):
        assignee = node.get_property("ASSIGNEE") or ""
        if assignee.lower() == contributor.lower() or not assignee:
            tasks.append({
                "id": node.id(),
                "heading": node.heading,
                "state": node.todo,
                "assignee": assignee,
                "source": node.get_property("SOURCE") or "",
            })
    return tasks


def get_yesterday_standup(space_path: str, contributor: str) -> list[dict]:
    """Parse yesterday's journal standup section for a contributor."""
    journal_dir = Path(space_path) / "journal"

    # Look back up to 3 days for the last journal
    for days_back in range(1, 4):
        check_date = date.today() - timedelta(days=days_back)
        journal_file = journal_dir / f"{check_date.isoformat()}.md"
        if journal_file.exists():
            return parse_standup_items(journal_file, contributor)
    return []


def parse_standup_items(journal_file: Path, contributor: str) -> list[dict]:
    """Extract standup checkbox items for a contributor from a journal file."""
    content = journal_file.read_text()
    items = []

    # Find the Standup section
    standup_match = re.search(r"## Standup\s*\n(.*?)(?=\n## [^#]|\Z)", content, re.DOTALL)
    if not standup_match:
        return []

    standup_text = standup_match.group(1)

    # Find the contributor's subsection within Standup
    pattern = rf"### @{re.escape(contributor)}\s*\n((?:- \[[ x]\] .+\n?)*)"
    contrib_match = re.search(pattern, standup_text, re.IGNORECASE)
    if not contrib_match:
        return []

    for line in contrib_match.group(1).strip().split("\n"):
        line = line.strip()
        if not line.startswith("- ["):
            continue
        checked = line.startswith("- [x]")
        # Extract ID from comment
        id_match = re.search(r"<!-- :ID: (.+?) -->", line)
        task_id = id_match.group(1) if id_match else None
        # Extract text (between checkbox and comment)
        text = re.sub(r"\s*<!-- :ID: .+? -->", "", line[6:]).strip()
        items.append({
            "text": text,
            "checked": checked,
            "id": task_id,
        })
    return items


def create_standup_task(space_path: str, contributor: str, text: str) -> dict:
    """Create an org task tagged :standup: for a standup item."""
    org_file = Path(space_path) / "org" / "next_actions.org"
    if not org_file.exists():
        return {"error": f"File not found: {org_file}"}

    ws = OrgWorkspace()
    ws.load(str(org_file))

    today = date.today().isoformat()
    task_id = f"{Path(space_path).name[:2]}-{today.replace('-', '')}-{hash(text) % 1000:03d}"

    # Find or create a Standup section
    standup_parent = None
    for node in ws.root.children:
        if "standup" in node.heading.lower():
            standup_parent = node
            break

    if standup_parent is None:
        standup_parent = ws.root.add_child(heading="Standup Items", todo="")
        standup_parent.tags = []

    new_task = standup_parent.add_child(heading=text, todo="TODO")
    new_task.tags = ["standup"]
    new_task.set_property("ID", task_id)
    new_task.set_property("ASSIGNEE", contributor)
    new_task.set_property("SOURCE", f"journal/{today}")
    new_task.set_property("CREATED", today)

    ws.save()

    return {"id": task_id, "heading": text, "state": "TODO"}


def carryover(space_path: str, contributor: str) -> dict:
    """Get carryover data: yesterday's unchecked items + completed items."""
    yesterday_items = get_yesterday_standup(space_path, contributor)
    org_tasks = get_standup_tasks(space_path, contributor)

    # Build ID -> org state map
    org_state = {t["id"]: t["state"] for t in org_tasks}

    carried = []
    completed = []

    for item in yesterday_items:
        if item["checked"]:
            completed.append(item)
        elif item["id"] and org_state.get(item["id"]) == "DONE":
            completed.append({**item, "checked": True})
        else:
            carried.append(item)

    return {
        "carried_over": carried,
        "completed": completed,
        "org_tasks_total": len(org_tasks),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Standup <-> org sync")
    parser.add_argument("command", choices=["carryover", "check-off", "create"])
    parser.add_argument("--space", required=True, help="Space path")
    parser.add_argument("--contributor", help="Contributor name")
    parser.add_argument("--id", help="Task ID (for check-off)")
    parser.add_argument("--text", help="Item text (for create)")
    args = parser.parse_args()

    if args.command == "carryover":
        result = carryover(args.space, args.contributor)
    elif args.command == "create":
        result = create_standup_task(args.space, args.contributor, args.text)
    elif args.command == "check-off":
        org_file = Path(args.space) / "org" / "next_actions.org"
        ws = OrgWorkspace()
        ws.load(str(org_file))
        node = ws.get_by_id(args.id)
        if node:
            node.todo = "DONE"
            ws.save()
            result = {"id": args.id, "state": "DONE"}
        else:
            result = {"error": f"Task {args.id} not found"}

    print(json.dumps(result, indent=2))
