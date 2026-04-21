#!/usr/bin/env python3
"""
space_manager — Manage datacore team spaces.

Team spaces are independent git repos cloned under ~/Data/. Each space
has its own upstream (shared template) and origin (personal fork).

Usage
-----
  space_manager.py list
  space_manager.py add <repo-url> [local-name] [--upstream <url>]
  space_manager.py remove <local-name> [--force]
  space_manager.py init <name> [--template <url>]

Examples
--------
  # Clone a team space with upstream tracking
  space_manager.py add https://github.com/tris-on-hermes/datacore-space.git \\
      2-datacore --upstream https://github.com/datacore-one/datacore-space.git

  # List all spaces with branch and dirty status
  space_manager.py list

  # Remove a space (fails if uncommitted changes)
  space_manager.py remove 2-datacore

  # Create a new local space from template
  space_manager.py init 1-acme --template https://github.com/datacore-one/datacore-org.git
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

DATA_ROOT = Path.home() / "Data"


def run(cmd: list[str], cwd: Path | str | None = None, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def list_spaces() -> list[dict]:
    spaces: list[dict] = []
    for entry in sorted(DATA_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        git_dir = entry / ".git"
        if not git_dir.exists():
            continue
        remotes: dict[str, str] = {}
        r = run(["git", "remote", "-v"], cwd=entry)
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                remotes[parts[0]] = parts[1]
        branch = run(["git", "branch", "--show-current"], cwd=entry).stdout.strip()
        status = run(["git", "status", "--short"], cwd=entry).stdout.strip()
        spaces.append({
            "name": entry.name,
            "path": entry,
            "branch": branch,
            "remotes": remotes,
            "dirty": bool(status),
        })
    return spaces


def add_space(repo_url: str, local_name: str | None = None, upstream_url: str | None = None) -> Path:
    if local_name is None:
        local_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")

    target = DATA_ROOT / local_name
    if target.exists():
        print(f"Error: {target} already exists", file=sys.stderr)
        sys.exit(1)

    print(f"Cloning {repo_url} into {target}...")
    run(["git", "clone", repo_url, str(target)], check=True)

    # Configure upstream if provided
    if upstream_url:
        run(["git", "remote", "rename", "origin", "upstream"], cwd=target, check=True)
        run(["git", "remote", "add", "origin", repo_url], cwd=target, check=True)
        run(["git", "remote", "set-url", "upstream", upstream_url], cwd=target, check=True)
        print(f"Remotes: origin={repo_url}, upstream={upstream_url}")
    else:
        # Infer upstream for datacore-* repos
        remotes = _get_remotes(target)
        if "origin" in remotes:
            origin_url = remotes["origin"]
            if "github.com" in origin_url:
                repo_name = origin_url.split("/")[-1].replace(".git", "")
                if repo_name.startswith("datacore-"):
                    upstream_candidate = f"https://github.com/datacore-one/{repo_name}.git"
                    run(["git", "remote", "add", "upstream", upstream_candidate], cwd=target)
                    print(f"Inferred upstream: {upstream_candidate}")

    print(f"Space '{local_name}' added.")
    return target


def remove_space(local_name: str, force: bool = False) -> None:
    target = DATA_ROOT / local_name
    if not target.exists():
        print(f"Error: {target} does not exist", file=sys.stderr)
        sys.exit(1)

    if target == DATA_ROOT:
        print("Error: cannot remove root ~/Data", file=sys.stderr)
        sys.exit(1)

    if (target / ".git").exists():
        r = run(["git", "status", "--short"], cwd=target)
        if r.stdout.strip() and not force:
            print(
                f"Error: {local_name} has uncommitted changes. Use --force.",
                file=sys.stderr,
            )
            sys.exit(1)

    shutil.rmtree(target)
    print(f"Space '{local_name}' removed.")


def init_space(name: str, template_url: str | None = None) -> Path:
    target = DATA_ROOT / name
    if target.exists():
        print(f"Error: {target} already exists", file=sys.stderr)
        sys.exit(1)

    target.mkdir(parents=True)

    if template_url:
        print(f"Cloning template from {template_url}...")
        run(["git", "clone", template_url, str(target)], check=True)
    else:
        run(["git", "init"], cwd=target, check=True)
        for sub in ("org", "journal", "0-inbox", "1-tracks", "3-knowledge", "4-archive"):
            (target / sub).mkdir(exist_ok=True)

    print(f"Space '{name}' initialized at {target}")
    return target


def _get_remotes(space_dir: Path) -> dict[str, str]:
    r = run(["git", "remote", "-v"], cwd=space_dir)
    remotes: dict[str, str] = {}
    for line in r.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            remotes[parts[0]] = parts[1]
    return remotes


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage datacore team spaces")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all spaces")

    p_add = sub.add_parser("add", help="Add a space from a git repo")
    p_add.add_argument("repo_url", help="Git repository URL")
    p_add.add_argument("local_name", nargs="?", help="Local directory name")
    p_add.add_argument("--upstream", help="Upstream repository URL")

    p_rm = sub.add_parser("remove", help="Remove a space")
    p_rm.add_argument("local_name", help="Local directory name")
    p_rm.add_argument("--force", action="store_true", help="Remove even with uncommitted changes")

    p_init = sub.add_parser("init", help="Initialize a new local space")
    p_init.add_argument("name", help="Space name (e.g., 1-acme)")
    p_init.add_argument("--template", help="Template repo URL to clone")

    args = parser.parse_args()

    if args.command == "list":
        spaces = list_spaces()
        if not spaces:
            print("No git-managed spaces found.")
            return
        print(f"{'Space':<20} {'Branch':<12} {'Remotes':<30} {'Status'}")
        print("-" * 70)
        for s in spaces:
            remote_list = ", ".join(s["remotes"].keys())
            status = "dirty" if s["dirty"] else "clean"
            print(f"{s['name']:<20} {s['branch']:<12} {remote_list:<30} {status}")

    elif args.command == "add":
        add_space(args.repo_url, args.local_name, args.upstream)

    elif args.command == "remove":
        remove_space(args.local_name, args.force)

    elif args.command == "init":
        init_space(args.name, args.template)


if __name__ == "__main__":
    main()
