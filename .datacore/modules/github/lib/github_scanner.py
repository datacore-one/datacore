#!/usr/bin/env python3
"""Scan GitHub for activity relevant to a user.

Three scan types:
1. Mentions — issues/PRs mentioning @username
2. Authored — issues created by user with new comments from others
3. Org-wide — all new issues, closed issues, merged PRs per org

Uses `gh` CLI for API access. Caches results in scan_cache.json.

Usage:
    python3 github_scanner.py --username plur9 --orgs datacore-one,plur-ai [--hours 24]

Output: JSON to stdout with structured scan results.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


# A failed query and an empty result set both used to come back as [], which is
# how this scanner reported "created: 0, errors: 0" on the CoS box every morning
# from 2026-07-19 to 2026-08-10 — 23 days — while `gh` was not authenticated and
# EVERY search failed. "I found nothing" and "I could not look" must never be
# the same value. Failures are counted here and surfaced by the caller.
SEARCH_FAILURES: list[str] = []


def reset_search_failures() -> None:
    SEARCH_FAILURES.clear()


def _gh_search(query_args: list[str], timeout: int = 30) -> list[dict]:
    """Run a gh search command, return parsed JSON results.

    Returns [] on failure AND records the reason in SEARCH_FAILURES, so a caller
    can tell an empty result set from a query that never ran.
    """
    cmd = ["gh", "search", *query_args]

    def fail(reason: str) -> list[dict]:
        SEARCH_FAILURES.append(f"{' '.join(cmd[:4])}: {reason}")
        print(f"Warning: gh search failed: {reason}", file=sys.stderr)
        return []

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            if "rate limit" in err.lower():
                return fail(f"rate limited — {err[:160]}")
            return fail(err[:200] or f"exit {result.returncode} with no stderr")
        try:
            return json.loads(result.stdout or "[]")
        except json.JSONDecodeError as e:
            # rc=0 with unparseable output is a failure, not an empty result.
            return fail(f"unparseable JSON: {e}")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return fail(f"{type(e).__name__}: {e}")


def _gh_api(endpoint: str, timeout: int = 15) -> dict | list | None:
    """Call gh api for a single resource."""
    try:
        result = subprocess.run(
            ["gh", "api", endpoint, "--jq", "."],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout or "null")
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return None


def scan_mentions(username: str, since_date: str) -> list[dict]:
    """Find issues/PRs mentioning @username updated since date.

    Returns list of dicts with: repo, number, title, url, state, updated_at, type.
    """
    fields = "repository,number,title,url,state,updatedAt,isPullRequest"
    raw = _gh_search([
        "issues",
        f"--mentions={username}",
        f"--updated=>{since_date}",
        "--json", fields,
        "--limit", "50",
    ])

    items = []
    for r in raw:
        repo_name = r.get("repository", {}).get("nameWithOwner", "") if isinstance(r.get("repository"), dict) else ""
        items.append({
            "repo": repo_name,
            "number": r.get("number"),
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "state": r.get("state", ""),
            "updated_at": r.get("updatedAt", ""),
            "type": "pr" if r.get("isPullRequest") else "issue",
            "scan_type": "mention",
        })
    return items


def scan_authored(username: str, since_date: str) -> list[dict]:
    """Find issues authored by username with new comments from others.

    Returns list of dicts with: repo, number, title, url, state, updated_at,
    comment_count, latest_commenter.
    """
    fields = "repository,number,title,url,state,updatedAt,commentsCount"
    raw = _gh_search([
        "issues",
        f"--author={username}",
        f"--updated=>{since_date}",
        "--json", fields,
        "--limit", "50",
    ])

    items = []
    for r in raw:
        repo_name = r.get("repository", {}).get("nameWithOwner", "") if isinstance(r.get("repository"), dict) else ""
        item = {
            "repo": repo_name,
            "number": r.get("number"),
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "state": r.get("state", ""),
            "updated_at": r.get("updatedAt", ""),
            "comment_count": r.get("commentsCount", 0),
            "scan_type": "authored",
        }

        if repo_name and r.get("number"):
            comments = _gh_api(
                f"repos/{repo_name}/issues/{r['number']}/comments?per_page=1&sort=created&direction=desc"
            )
            if comments and isinstance(comments, list) and len(comments) > 0:
                commenter = comments[0].get("user", {}).get("login", "")
                if commenter.lower() != username.lower():
                    item["latest_commenter"] = commenter
                    item["latest_comment_body"] = comments[0].get("body", "")[:200]
                    items.append(item)

    return items


def scan_org_activity(org: str, since_date: str) -> dict:
    """Get org-wide activity counts: new issues, closed, PRs merged.

    Returns dict with: org, new_issues_count, closed_issues_count, merged_prs_count, lists.
    """
    new_issues = _gh_search([
        "issues",
        f"--owner={org}",
        f"--created=>{since_date}",
        "--json", "repository,number,title,url",
        "--limit", "100",
    ])

    closed_issues = _gh_search([
        "issues",
        f"--owner={org}",
        f"--closed=>{since_date}",
        "--json", "repository,number",
        "--limit", "100",
    ])

    merged_prs = _gh_search([
        "prs",
        f"--owner={org}",
        f"--merged-at=>{since_date}",
        "--json", "repository,number,title,url",
        "--limit", "100",
    ])

    return {
        "org": org,
        "new_issues_count": len(new_issues),
        "closed_issues_count": len(closed_issues),
        "merged_prs_count": len(merged_prs),
        "new_issues": [
            {
                "repo": i.get("repository", {}).get("nameWithOwner", "") if isinstance(i.get("repository"), dict) else "",
                "number": i.get("number"),
                "title": i.get("title", ""),
                "url": i.get("url", ""),
            }
            for i in new_issues
        ],
        "merged_prs": [
            {
                "repo": i.get("repository", {}).get("nameWithOwner", "") if isinstance(i.get("repository"), dict) else "",
                "number": i.get("number"),
                "title": i.get("title", ""),
                "url": i.get("url", ""),
            }
            for i in merged_prs
        ],
    }


def run_full_scan(
    username: str,
    orgs: list[str],
    scan_hours: int = 24,
    cache_path: Path | None = None,
) -> dict:
    """Run all three scan types. Returns structured results.

    Checks cache first — if scan was already done today, returns cached results.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("scan_date") == today:
                # A cached scan whose queries FAILED must not be replayed for
                # the rest of the day as though it were a clean empty result —
                # that would launder the very failure this scanner now reports.
                # Re-run instead; the condition that broke it may have been
                # fixed since (e.g. gh has just been authenticated).
                if cached.get("search_failures"):
                    print(
                        f"Cached scan for {today} recorded "
                        f"{len(cached['search_failures'])} failed search(es) — "
                        f"re-scanning rather than reusing it.",
                        file=sys.stderr,
                    )
                else:
                    return cached
        except (json.JSONDecodeError, KeyError):
            pass

    since = (datetime.now(timezone.utc) - timedelta(hours=scan_hours)).strftime("%Y-%m-%d")

    reset_search_failures()
    mentions = scan_mentions(username, since)
    authored = scan_authored(username, since)
    org_activity = {}
    for org in orgs:
        org_activity[org] = scan_org_activity(org, since)

    result = {
        "scan_date": today,
        "since": since,
        "username": username,
        "mentions": mentions,
        "authored": authored,
        "org_activity": org_activity,
        "scanned_at": datetime.now().isoformat(),
        # Without this a zero-result scan is indistinguishable from a scan whose
        # every query failed. That ambiguity hid an unauthenticated `gh` on the
        # CoS box for 23 days.
        "search_failures": list(SEARCH_FAILURES),
    }

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, indent=2))

    if SEARCH_FAILURES:
        print(
            f"ERROR: {len(SEARCH_FAILURES)} gh search(es) FAILED — this scan is "
            f"incomplete and any 'nothing found' below is unreliable. "
            f"First: {SEARCH_FAILURES[0]}",
            file=sys.stderr,
        )

    return result


def format_summary(scan: dict, org_to_spaces: dict[str, list[str]] = None) -> str:
    """Format scan results into markdown summary."""
    org_to_spaces = org_to_spaces or {}
    lines = []

    mentions = scan.get("mentions", [])
    authored = scan.get("authored", [])

    if mentions or authored:
        lines.append("### GitHub: Your Items")
        lines.append("")

        if mentions:
            lines.append(f"**@{scan['username']} mentions:**")
            for m in mentions:
                lines.append(f"- {m['repo']}#{m['number']} — \"{m['title']}\"")
                lines.append(f"  State: {m['state']} | [View]({m['url']})")
            lines.append("")

        if authored:
            lines.append("**Your issues with new activity:**")
            for a in authored:
                commenter = a.get("latest_commenter", "unknown")
                snippet = a.get("latest_comment_body", "")[:100]
                lines.append(f"- {a['repo']}#{a['number']} — \"{a['title']}\"")
                lines.append(f"  Latest by @{commenter}: {snippet}")
                lines.append(f"  [View]({a['url']})")
            lines.append("")
    else:
        lines.append("### GitHub: Your Items")
        lines.append("")
        lines.append("No mentions or comments on your issues in the last 24 hours.")
        lines.append("")

    org_activity = scan.get("org_activity", {})
    if org_activity:
        lines.append("### GitHub: Org Activity")
        lines.append("")
        lines.append("| Org | New Issues | Closed | PRs Merged |")
        lines.append("|-----|-----------|--------|------------|")
        for org, data in org_activity.items():
            spaces = ", ".join(org_to_spaces.get(org, [org]))
            lines.append(
                f"| {org} ({spaces}) | {data['new_issues_count']} "
                f"| {data['closed_issues_count']} | {data['merged_prs_count']} |"
            )
        lines.append("")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scan GitHub for activity")
    parser.add_argument("--username", default="plur9")
    parser.add_argument("--orgs", required=True, help="Comma-separated org names")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--cache", help="Path to scan_cache.json")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    orgs = [o.strip() for o in args.orgs.split(",")]
    cache_path = Path(args.cache) if args.cache else None

    scan = run_full_scan(args.username, orgs, args.hours, cache_path)

    if args.format == "markdown":
        print(format_summary(scan))
    else:
        print(json.dumps(scan, indent=2))

    # Exit non-zero when any query failed, so cron/systemd records a FAILURE
    # instead of a clean run. Previously an entirely unauthenticated `gh`
    # produced exit 0 and "Completed" for 23 consecutive mornings.
    if scan.get("search_failures"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
