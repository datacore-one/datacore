#!/usr/bin/env python3
"""Create org-mode tasks from GitHub scan results.

Reads scan output from github_scanner, determines which items are actionable,
creates tasks in the appropriate space's next_actions.org with :AI:github: tags.

Usage:
    python3 task_creator.py --scan-file data/scan_cache.json --data-dir ~/Data --repos-file data/repos.json

Output: JSON summary of created tasks.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

# Add shared lib to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from triage_utils import create_triage_task


def _space_for_repo(repo_full_name: str, org_to_spaces: dict[str, list[str]]) -> str | None:
    """Determine which space a repo belongs to based on org mapping."""
    org = repo_full_name.split("/")[0] if "/" in repo_full_name else ""
    spaces = org_to_spaces.get(org, [])
    for s in spaces:
        if s != "root":
            return s
    return spaces[0] if spaces else None


def _org_file_for_space(data_dir: Path, space: str) -> Path:
    """Get the next_actions.org path for a space."""
    if space == "root":
        return data_dir / "0-personal" / "org" / "next_actions.org"
    return data_dir / space / "org" / "next_actions.org"


def _make_task_id(repo: str, number: int, today: str) -> str:
    """Generate idempotent task ID: gh-{repo_short}-{number}-{date}."""
    repo_short = repo.split("/")[-1] if "/" in repo else repo
    return f"gh-{repo_short}-{number}-{today}"


def create_tasks_from_scan(
    scan: dict,
    data_dir: Path,
    org_to_spaces: dict[str, list[str]],
    auto_create: bool = True,
) -> dict:
    """Process scan results and create org-mode tasks for actionable items.

    Returns summary dict with: created, skipped, errors.
    """
    if not auto_create:
        return {"created": 0, "skipped": 0, "errors": 0, "tasks": []}

    today = date.today().isoformat()
    created = []
    skipped = 0
    errors = []

    # Process mentions — these are always actionable
    for item in scan.get("mentions", []):
        repo = item.get("repo", "")
        number = item.get("number", 0)
        title = item.get("title", "")
        url = item.get("url", "")
        space = _space_for_repo(repo, org_to_spaces)

        if not space or not number:
            continue

        task_id = _make_task_id(repo, number, today)
        org_file = _org_file_for_space(data_dir, space)

        if not org_file.exists():
            errors.append(f"Org file not found: {org_file}")
            continue

        heading = f"Respond to {repo}#{number} — {title[:60]}"
        properties = {
            "TRIAGE_ID": task_id,
            "GITHUB_URL": url,
            "GITHUB_TYPE": "issue_mention",
            "SPACE": space,
            "COMPLEXITY": "unknown",
            "CONFIDENCE": "0",
        }
        context = f"Mentioned in {repo}#{number}: {title}\nURL: {url}"

        result = create_triage_task(
            org_file=org_file,
            heading=heading,
            tags=["AI", "github"],
            properties=properties,
            context_body=context,
            scheduled_date=date.today(),
        )

        if result.get("skipped"):
            skipped += 1
        elif result.get("success"):
            created.append({"id": task_id, "heading": heading, "space": space, "type": "mention"})
        else:
            errors.append(f"Failed to create task for {repo}#{number}: {result.get('error')}")

    # Process authored issues with new comments
    for item in scan.get("authored", []):
        repo = item.get("repo", "")
        number = item.get("number", 0)
        title = item.get("title", "")
        url = item.get("url", "")
        commenter = item.get("latest_commenter", "")
        comment_body = item.get("latest_comment_body", "")
        space = _space_for_repo(repo, org_to_spaces)

        if not space or not number:
            continue

        task_id = _make_task_id(repo, number, today)
        org_file = _org_file_for_space(data_dir, space)

        if not org_file.exists():
            errors.append(f"Org file not found: {org_file}")
            continue

        heading = f"Review comment on {repo}#{number} — {title[:60]}"
        properties = {
            "TRIAGE_ID": task_id,
            "GITHUB_URL": url,
            "GITHUB_TYPE": "authored_comment",
            "SPACE": space,
            "COMPLEXITY": "unknown",
            "CONFIDENCE": "0",
        }
        context = f"New comment by @{commenter} on your issue {repo}#{number}: {title}\n"
        context += f"Comment: {comment_body}\nURL: {url}"

        result = create_triage_task(
            org_file=org_file,
            heading=heading,
            tags=["AI", "github"],
            properties=properties,
            context_body=context,
            scheduled_date=date.today(),
        )

        if result.get("skipped"):
            skipped += 1
        elif result.get("success"):
            created.append({"id": task_id, "heading": heading, "space": space, "type": "authored"})
        else:
            errors.append(f"Failed to create task for {repo}#{number}: {result.get('error')}")

    return {
        "created": len(created),
        "skipped": skipped,
        "errors": len(errors),
        "error_details": errors,
        "tasks": created,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Create org-mode tasks from GitHub scan")
    parser.add_argument("--scan-file", required=True, help="Path to scan_cache.json")
    parser.add_argument("--data-dir", default=str(Path.home() / "Data"))
    parser.add_argument("--repos-file", required=True, help="Path to repos.json")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created")
    args = parser.parse_args()

    scan = json.loads(Path(args.scan_file).read_text())
    repos = json.loads(Path(args.repos_file).read_text())
    org_to_spaces = repos.get("org_to_spaces", {})
    data_dir = Path(args.data_dir)

    if args.dry_run:
        print("DRY RUN — would create tasks for:")
        for item in scan.get("mentions", []):
            print(f"  [mention] {item['repo']}#{item['number']}: {item['title']}")
        for item in scan.get("authored", []):
            print(f"  [authored] {item['repo']}#{item['number']}: {item['title']}")
        return

    result = create_tasks_from_scan(scan, data_dir, org_to_spaces)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
