#!/usr/bin/env python3
"""Discover GitHub repos to monitor from Datacore space git remotes.

Walks spaces at ~/Data/[N]-[name]/, extracts GitHub org/owner from
git remotes, fetches all non-archived repos per org via `gh` CLI.
Caches results to data/repos.json, refreshes if older than 7 days.

Usage:
    python3 repo_discovery.py [--data-dir ~/Data] [--force-refresh]

Output: JSON to stdout with discovered repos.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


def get_space_remotes(data_dir: Path) -> dict[str, str]:
    """Extract GitHub org/owner from each space's git remote.

    Returns dict mapping space name to GitHub org/owner.
    Skips spaces without GitHub remotes (e.g., self-hosted).
    """
    orgs = {}
    for space_dir in sorted(data_dir.iterdir()):
        if not re.match(r"^\d+-", space_dir.name) or not space_dir.is_dir():
            continue
        git_dir = space_dir / ".git"
        if not git_dir.exists():
            continue
        try:
            result = subprocess.run(
                ["git", "-C", str(space_dir), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                continue
            url = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

        match = re.search(r"github\.com[:/]([^/]+)/", url)
        if match:
            org = match.group(1)
            orgs[space_dir.name] = org

    # Also check root repo
    try:
        result = subprocess.run(
            ["git", "-C", str(data_dir), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            match = re.search(r"github\.com[:/]([^/]+)/", result.stdout.strip())
            if match:
                orgs["root"] = match.group(1)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return orgs


def fetch_org_repos(org: str, exclude: list[str] = None, include: list[str] = None) -> list[dict]:
    """Fetch all non-archived repos for a GitHub org/user via gh CLI.

    Returns list of dicts with keys: name, full_name, url, is_fork, default_branch.
    """
    exclude = exclude or []
    try:
        result = subprocess.run(
            [
                "gh", "repo", "list", org,
                "--no-archived",
                "--json", "name,url,isFork,defaultBranchRef",
                "--limit", "100",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"Warning: gh repo list {org} failed: {result.stderr.strip()}", file=sys.stderr)
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print(f"Warning: gh CLI not available or timed out for {org}", file=sys.stderr)
        return []

    repos = []
    for r in json.loads(result.stdout or "[]"):
        full_name = f"{org}/{r['name']}"
        if full_name in exclude:
            continue
        default_branch = ""
        if r.get("defaultBranchRef") and r["defaultBranchRef"].get("name"):
            default_branch = r["defaultBranchRef"]["name"]
        repos.append({
            "name": r["name"],
            "full_name": full_name,
            "url": r["url"],
            "is_fork": r.get("isFork", False),
            "default_branch": default_branch,
        })

    if include:
        existing = {r["full_name"] for r in repos}
        for inc in include:
            if inc.startswith(f"{org}/") and inc not in existing:
                repos.append({
                    "name": inc.split("/", 1)[1],
                    "full_name": inc,
                    "url": f"https://github.com/{inc}",
                    "is_fork": False,
                    "default_branch": "main",
                })

    return repos


def discover_repos(
    data_dir: Path,
    cache_path: Path,
    force_refresh: bool = False,
    max_age_days: int = 7,
    exclude_repos: list[str] = None,
    include_repos: list[str] = None,
) -> dict:
    """Discover all repos to monitor. Uses cache if fresh enough.

    Returns dict with keys: orgs, repos, org_to_space, updated_at.
    """
    exclude_repos = exclude_repos or []
    include_repos = include_repos or []

    if not force_refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            updated = datetime.fromisoformat(cached["updated_at"])
            if datetime.now() - updated < timedelta(days=max_age_days):
                return cached
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    space_orgs = get_space_remotes(data_dir)
    unique_orgs = sorted(set(space_orgs.values()))

    org_to_spaces = {}
    for space, org in space_orgs.items():
        org_to_spaces.setdefault(org, []).append(space)

    all_repos = {}
    for org in unique_orgs:
        repos = fetch_org_repos(org, exclude=exclude_repos, include=include_repos)
        all_repos[org] = repos

    result = {
        "orgs": unique_orgs,
        "repos": all_repos,
        "org_to_spaces": org_to_spaces,
        "space_to_org": space_orgs,
        "updated_at": datetime.now().isoformat(),
    }

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result, indent=2))

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Discover GitHub repos from Datacore spaces")
    parser.add_argument("--data-dir", default=str(Path.home() / "Data"), help="Datacore root")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cache")
    parser.add_argument("--exclude", nargs="*", default=[], help="Repos to exclude (owner/name)")
    parser.add_argument("--include", nargs="*", default=[], help="Extra repos to include (owner/name)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    cache_path = data_dir / ".datacore" / "modules" / "github" / "data" / "repos.json"

    result = discover_repos(
        data_dir, cache_path,
        force_refresh=args.force_refresh,
        exclude_repos=args.exclude,
        include_repos=args.include,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
