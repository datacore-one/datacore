"""
GitHub Issues sync adapter.

DIP-0010: Task Sync Architecture

Uses `gh` CLI for GitHub API access (avoids PyGithub dependency).
Requires: GitHub CLI installed and authenticated (`gh auth login`)
"""

import json
import os
import subprocess
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .base import (
    TaskSyncAdapter,
    OrgTask,
    ExternalTask,
    ExternalTaskRef,
    TaskChange,
    SyncResult,
    TaskState,
    Priority,
    ChangeType,
)


def load_label_mapping_from_registry(data_dir=None) -> Dict[str, str]:
    """Load label mapping from tags.yaml registry."""
    data_dir = Path(data_dir or os.environ.get("DATA_DIR") or os.environ.get("DATACORE_ROOT") or Path.home() / "Data")
    registry_path = data_dir / ".datacore" / "tags.yaml"

    if not registry_path.exists():
        return {}

    with open(registry_path, encoding="utf-8") as f:
        registry = yaml.safe_load(f)
    if not isinstance(registry, dict):
        raise ValueError("tag registry must be a mapping")
    mapping = registry.get("sync_label_mapping", {})
    if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
        raise ValueError("sync label mapping must map strings to strings")
    return mapping


class GitHubAdapter(TaskSyncAdapter):
    """
    GitHub Issues sync adapter using gh CLI.

    Configuration (from settings.yaml):
        sync:
          adapters:
            github:
              enabled: true
              repos:
                - owner: datacore-one
                  repo: datacore
              # Label mappings loaded from tags.yaml registry
              # Override specific mappings here if needed:
              # label_mapping:
              #   ":custom:": "custom-label"
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize GitHub adapter.

        Args:
            config: Adapter configuration from settings.yaml
        """
        self.config = config
        self.repos = config.get("repos", [])

        # Load label mapping: config overrides > tags.yaml registry > defaults
        default_mapping = {
            ":AI:": "ai-task",
            ":AI:research:": "ai-research",
            ":AI:content:": "ai-content",
            ":AI:data:": "ai-data",
            ":AI:pm:": "ai-pm",
            ":AI:technical:": "ai-technical",
            "[#A]": "priority-high",
            "[#B]": "priority-medium",
            "[#C]": "priority-low",
        }

        # Try loading from registry first
        registry_mapping = load_label_mapping_from_registry(config.get("data_dir"))

        # Merge: defaults < registry < config overrides
        self.label_mapping = {**default_mapping, **registry_mapping}
        if "label_mapping" in config:
            self.label_mapping.update(config["label_mapping"])

        self.state_mapping = config.get("state_mapping", {
            "TODO": "open",
            "NEXT": "open",
            "WAITING": "open",
            "DONE": "closed",
            "CANCELLED": "closed",
        })

    @property
    def name(self) -> str:
        return "github"

    def is_configured(self) -> bool:
        """Check if adapter is properly configured."""
        return bool(self.repos)

    def test_connection(self) -> tuple[bool, str]:
        """Test connection to GitHub via gh CLI."""
        try:
            result = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return True, "GitHub CLI authenticated"
            return False, f"GitHub CLI not authenticated: {result.stderr}"
        except FileNotFoundError:
            return False, "GitHub CLI (gh) not installed"
        except subprocess.TimeoutExpired:
            return False, "GitHub CLI timeout"
        except Exception as e:
            return False, f"GitHub CLI error: {str(e)}"

    def _run_gh(self, args: List[str], timeout: int = 30) -> tuple[bool, str, str]:
        """
        Run gh CLI command.

        Returns:
            Tuple of (success, stdout, stderr)
        """
        try:
            result = subprocess.run(
                ["gh"] + args,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode == 0, result.stdout, result.stderr
        except Exception as e:
            return False, "", str(e)

    def _parse_issue(self, issue_data: Dict[str, Any], owner: str, repo: str) -> ExternalTask:
        """Parse gh CLI JSON output to ExternalTask."""
        # Parse dates
        created_at = datetime.fromisoformat(issue_data["createdAt"].replace("Z", "+00:00"))
        updated_at = datetime.fromisoformat(issue_data["updatedAt"].replace("Z", "+00:00"))

        # Extract labels
        labels = [label["name"] for label in issue_data.get("labels", [])]

        # Extract assignee
        assignees = issue_data.get("assignees", [])
        assignee = assignees[0]["login"] if assignees else None

        return ExternalTask(
            id=str(issue_data["number"]),
            title=issue_data["title"],
            state=issue_data["state"].lower(),
            url=issue_data["url"],
            created_at=created_at,
            updated_at=updated_at,
            body=issue_data.get("body", "") or "",
            labels=labels,
            assignee=assignee,
            raw={"owner": owner, "repo": repo, **issue_data}
        )

    def _make_external_id(self, owner: str, repo: str, number: int) -> str:
        """Create external ID in format 'github:owner/repo#number'."""
        return f"github:{owner}/{repo}#{number}"

    def _parse_external_id(self, external_id: str) -> tuple[str, str, int] | None:
        """Parse external ID to (owner, repo, number)."""
        match = re.fullmatch(r"github:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#([1-9]\d*)", external_id)
        if match:
            return match.group(1), match.group(2), int(match.group(3))
        return None

    def _authorized_ref(self, ref):
        parsed = self._parse_external_id(ref.external_id)
        if ref.adapter != "github" or parsed is None:
            return None
        owner, repo, _ = parsed
        if not any(str(r.get("owner", "")).lower() == owner.lower()
                   and str(r.get("repo", "")).lower() == repo.lower() for r in self.repos):
            return None
        return parsed

    def pull_changes(self, since: Optional[datetime] = None) -> List[TaskChange]:
        """Fetch issues from configured repos."""
        changes = []

        for repo_config in self.repos:
            owner = repo_config["owner"]
            repo = repo_config["repo"]

            # Build query - get open and recently closed issues
            args = [
                "issue", "list",
                "-R", f"{owner}/{repo}",
                "--json", "number,title,state,url,createdAt,updatedAt,body,labels,assignees",
                # gh paginates internally up to this bound. Reaching it is
                # an incomplete snapshot, never a successful truncated pull.
                "--limit", "10000"
            ]

            # Get open issues
            success, stdout, stderr = self._run_gh(args + ["--state", "open"])
            if not success or not stdout:
                raise RuntimeError("GitHub open-issue snapshot failed")
            if success and stdout:
                issues = json.loads(stdout)
                if not isinstance(issues, list) or len(issues) >= 10000:
                    raise ValueError("GitHub snapshot is malformed or exceeds the 10000-issue limit")
                for issue_data in issues:
                    task = self._parse_issue(issue_data, owner, repo)
                    if since is None or task.updated_at > since:
                        changes.append(TaskChange(
                            change_type=ChangeType.UPDATED,
                            external_task=task,
                            timestamp=task.updated_at
                        ))

            # Get recently closed issues
            success, stdout, stderr = self._run_gh(args + ["--state", "closed"])
            if not success or not stdout:
                raise RuntimeError("GitHub closed-issue snapshot failed")
            if success and stdout:
                issues = json.loads(stdout)
                if not isinstance(issues, list) or len(issues) >= 10000:
                    raise ValueError("GitHub snapshot is malformed or exceeds the 10000-issue limit")
                for issue_data in issues:
                    task = self._parse_issue(issue_data, owner, repo)
                    if since is None or task.updated_at > since:
                        changes.append(TaskChange(
                            change_type=ChangeType.CLOSED,
                            external_task=task,
                            timestamp=task.updated_at,
                            new_state="closed"
                        ))

        return changes

    def push_changes(self, changes: List[TaskChange]) -> SyncResult:
        """Push org-mode changes to GitHub."""
        result = SyncResult(success=True)

        for change in changes:
            if change.org_task is None:
                result.add_error("change has no org task")
                continue

            task = change.org_task

            if change.change_type == ChangeType.CREATED:
                # Create new issue
                ref = self.create_task(task)
                if ref:
                    result.items_created += 1
                else:
                    result.add_error(f"Failed to create issue for: {task.title}")

            elif change.change_type == ChangeType.STATE_CHANGED:
                if task.external_id:
                    ref = ExternalTaskRef.from_external_id(task.external_id, task.external_url or "")
                    if task.state in (TaskState.DONE, TaskState.CANCELLED):
                        if self.close_task(ref):
                            result.items_updated += 1
                        else:
                            result.add_error(f"Failed to close: {task.title}")
                    else:
                        # Reopen if needed
                        if self._reopen_task(ref):
                            result.items_updated += 1
                        else:
                            result.add_error("failed to reopen issue")
                else:
                    result.add_error("state change has no external ID")

            elif change.change_type == ChangeType.UPDATED:
                if task.external_id:
                    ref = ExternalTaskRef.from_external_id(task.external_id, task.external_url or "")
                    if self.update_task(ref, task):
                        result.items_updated += 1
                    else:
                        result.add_error(f"Failed to update: {task.title}")
                else:
                    result.add_error("update has no external ID")
            else:
                result.add_error("unsupported GitHub change type")

            result.items_processed += 1

        return result

    def create_task(self, task: OrgTask) -> Optional[ExternalTaskRef]:
        """Create new GitHub issue from org task."""
        if not self.repos:
            return None

        # Use first configured repo as default
        repo_config = self.repos[0]
        owner = repo_config["owner"]
        repo = repo_config["repo"]

        # Build labels from tags and priority
        labels = []
        for tag in task.tags:
            if tag in self.label_mapping:
                labels.append(self.label_mapping[tag])

        if task.priority:
            priority_label = self.map_priority_to_external(task.priority)
            if priority_label and priority_label not in labels:
                labels.append(priority_label)

        # Build body with deadline if present
        body = task.body
        if task.deadline:
            body = f"**Deadline:** {task.deadline.strftime('%Y-%m-%d')}\n\n{body}"

        # Create issue via gh CLI
        args = [
            "issue", "create",
            "-R", f"{owner}/{repo}",
            "--title", task.title,
            "--body", body or "Created from org-mode"
        ]

        if labels:
            args.extend(["--label", ",".join(labels)])

        success, stdout, stderr = self._run_gh(args)

        if success and stdout:
            # Parse issue URL from output
            url = stdout.strip()
            # Extract issue number from URL
            match = re.search(r"/issues/(\d+)", url)
            if match:
                number = int(match.group(1))
                external_id = self._make_external_id(owner, repo, number)
                return ExternalTaskRef(
                    adapter="github",
                    external_id=external_id,
                    url=url
                )

        return None

    def update_task(self, ref: ExternalTaskRef, task: OrgTask) -> bool:
        """Update existing GitHub issue."""
        parsed = self._authorized_ref(ref)
        if not parsed:
            return False

        owner, repo, number = parsed

        # Build labels
        labels = []
        for tag in task.tags:
            if tag in self.label_mapping:
                labels.append(self.label_mapping[tag])

        if task.priority:
            priority_label = self.map_priority_to_external(task.priority)
            if priority_label:
                labels.append(priority_label)

        # Update issue
        args = [
            "issue", "edit", str(number),
            "-R", f"{owner}/{repo}",
            "--title", task.title,
        ]

        if task.body:
            body = task.body
            if task.deadline:
                body = f"**Deadline:** {task.deadline.strftime('%Y-%m-%d')}\n\n{body}"
            args.extend(["--body", body])

        success, _, _ = self._run_gh(args)

        # Update labels separately (gh edit doesn't replace labels well)
        if success and labels:
            labels_ok, _, _ = self._run_gh([
                "issue", "edit", str(number),
                "-R", f"{owner}/{repo}",
                "--add-label", ",".join(labels)
            ])
            success = success and labels_ok

        return success

    def close_task(self, ref: ExternalTaskRef) -> bool:
        """Close GitHub issue."""
        parsed = self._authorized_ref(ref)
        if not parsed:
            return False

        owner, repo, number = parsed

        success, _, _ = self._run_gh([
            "issue", "close", str(number),
            "-R", f"{owner}/{repo}"
        ])

        return success

    def _reopen_task(self, ref: ExternalTaskRef) -> bool:
        """Reopen GitHub issue."""
        parsed = self._authorized_ref(ref)
        if not parsed:
            return False

        owner, repo, number = parsed

        success, _, _ = self._run_gh([
            "issue", "reopen", str(number),
            "-R", f"{owner}/{repo}"
        ])

        return success

    def find_matching_task(self, task: OrgTask) -> Optional[ExternalTaskRef]:
        """Search for existing issue matching org task."""
        if not self.repos:
            return None

        # Search by title in each repo
        search_title = task.title.replace('"', '\\"')

        for repo_config in self.repos:
            owner = repo_config["owner"]
            repo = repo_config["repo"]

            # Search issues
            success, stdout, _ = self._run_gh([
                "issue", "list",
                "-R", f"{owner}/{repo}",
                "--search", f'"{search_title}" in:title',
                "--json", "number,title,url",
                "--limit", "5"
            ])

            if success and stdout:
                issues = json.loads(stdout)
                for issue in issues:
                    # Check for exact or close title match
                    if issue["title"].lower() == task.title.lower():
                        external_id = self._make_external_id(owner, repo, issue["number"])
                        return ExternalTaskRef(
                            adapter="github",
                            external_id=external_id,
                            url=issue["url"]
                        )

        return None

    def get_issue(self, owner: str, repo: str, number: int) -> Optional[ExternalTask]:
        """Get a single issue by number."""
        success, stdout, _ = self._run_gh([
            "issue", "view", str(number),
            "-R", f"{owner}/{repo}",
            "--json", "number,title,state,url,createdAt,updatedAt,body,labels,assignees"
        ])

        if success and stdout:
            issue_data = json.loads(stdout)
            return self._parse_issue(issue_data, owner, repo)

        return None

    def get_open_comments_count(self) -> int:
        """Get count of issues with unresolved comments (for diagnostic)."""
        # This is a simplified check - could be expanded
        total = 0
        for repo_config in self.repos:
            owner = repo_config["owner"]
            repo = repo_config["repo"]

            success, stdout, _ = self._run_gh([
                "issue", "list",
                "-R", f"{owner}/{repo}",
                "--json", "number,comments",
                "--state", "open"
            ])

            if success and stdout:
                issues = json.loads(stdout)
                for issue in issues:
                    if issue.get("comments", 0) > 0:
                        total += 1

        return total
