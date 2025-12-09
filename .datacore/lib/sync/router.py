"""
Task Router - Routes external tasks to appropriate org-mode locations.

DIP-0010: Task Sync Architecture
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .adapters import ExternalTask, OrgTask, TaskState, Priority


@dataclass
class RoutingRule:
    """A routing rule for external tasks."""
    source: str  # Adapter name or "*" for all
    condition: str  # Condition expression
    destination: str  # Target org file (inbox.org, next_actions.org)
    tags: List[str] = None  # Tags to add
    category: Optional[str] = None  # Category/heading to place under
    state: Optional[str] = None  # Force specific state

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class TaskRouter:
    """
    Routes external tasks to appropriate org-mode locations.

    Configuration (from settings.yaml):
        sync:
          routing:
            - source: github
              condition: "labels contains 'ai-task'"
              destination: next_actions.org
              tags: [":AI:"]

            - source: github
              condition: "assignee is null"
              destination: inbox.org
    """

    def __init__(self, config: Dict[str, Any], data_dir: Optional[str] = None):
        """
        Initialize router.

        Args:
            config: Routing configuration from settings.yaml
            data_dir: Path to ~/Data
        """
        self.data_dir = Path(data_dir or os.environ.get("DATA_DIR", os.path.expanduser("~/Data")))
        self.rules = self._parse_rules(config.get("routing", []))

        # Default rules if none configured
        if not self.rules:
            self.rules = [
                # AI tasks go to next_actions with :AI: tag
                RoutingRule(
                    source="*",
                    condition="labels contains 'ai-task'",
                    destination="next_actions.org",
                    tags=[":AI:"]
                ),
                # High priority
                RoutingRule(
                    source="*",
                    condition="labels contains 'priority-high'",
                    destination="next_actions.org",
                    state="NEXT"
                ),
                # Default: everything else to inbox
                RoutingRule(
                    source="*",
                    condition="true",
                    destination="inbox.org"
                ),
            ]

    def _parse_rules(self, rules_config: List[Dict]) -> List[RoutingRule]:
        """Parse routing rules from config."""
        rules = []
        for rule_config in rules_config:
            rules.append(RoutingRule(
                source=rule_config.get("source", "*"),
                condition=rule_config.get("condition", "true"),
                destination=rule_config.get("destination", "inbox.org"),
                tags=rule_config.get("tags", []),
                category=rule_config.get("category"),
                state=rule_config.get("state"),
            ))
        return rules

    def _evaluate_condition(self, condition: str, task: ExternalTask) -> bool:
        """
        Evaluate a routing condition against a task.

        Supported conditions:
            - "true" - Always matches
            - "labels contains 'X'" - Task has label X
            - "assignee == 'X'" - Task assigned to X
            - "assignee is null" - Task unassigned
        """
        condition = condition.strip().lower()

        if condition == "true":
            return True

        # Labels contains
        match = re.match(r"labels contains ['\"](.+)['\"]", condition)
        if match:
            label = match.group(1)
            return any(l.lower() == label.lower() for l in task.labels)

        # Assignee equals
        match = re.match(r"assignee == ['\"](.+)['\"]", condition)
        if match:
            expected = match.group(1)
            return task.assignee and task.assignee.lower() == expected.lower()

        # Assignee is null
        if condition == "assignee is null":
            return task.assignee is None

        # No external ID (new task)
        if condition == "no external_id":
            return True  # This is checked at caller level

        return False

    def route(self, task: ExternalTask, adapter_name: str) -> Dict[str, Any]:
        """
        Determine routing for an external task.

        Args:
            task: External task to route
            adapter_name: Name of source adapter

        Returns:
            Dict with routing info:
                - destination: org file name
                - tags: tags to add
                - category: heading to place under
                - state: task state
        """
        for rule in self.rules:
            # Check source matches
            if rule.source != "*" and rule.source != adapter_name:
                continue

            # Check condition
            if self._evaluate_condition(rule.condition, task):
                return {
                    "destination": rule.destination,
                    "tags": rule.tags,
                    "category": rule.category,
                    "state": rule.state or "TODO",
                }

        # Default: inbox
        return {
            "destination": "inbox.org",
            "tags": [],
            "category": None,
            "state": "TODO",
        }

    def external_to_org(self, task: ExternalTask, adapter_name: str, external_id: str) -> OrgTask:
        """
        Convert external task to org task with routing.

        Args:
            task: External task
            adapter_name: Source adapter name
            external_id: Full external ID (e.g., "github:owner/repo#42")

        Returns:
            OrgTask ready for insertion
        """
        routing = self.route(task, adapter_name)

        # Determine state
        state_str = routing["state"]
        if task.state in ("closed", "done", "completed"):
            state = TaskState.DONE
        else:
            state = TaskState(state_str) if state_str in TaskState.__members__ else TaskState.TODO

        # Determine priority from labels
        priority = None
        for label in task.labels:
            label_lower = label.lower()
            if "high" in label_lower or label == "priority-high":
                priority = Priority.A
                break
            elif "medium" in label_lower or label == "priority-medium":
                priority = Priority.B
                break
            elif "low" in label_lower or label == "priority-low":
                priority = Priority.C
                break

        # Build tags
        tags = list(routing["tags"])
        for label in task.labels:
            if label.startswith("ai-"):
                # Map ai-* labels to :AI:* tags
                tag = f":AI:{label[3:]}:" if label != "ai-task" else ":AI:"
                if tag not in tags:
                    tags.append(tag)

        return OrgTask(
            id=f"{adapter_name}:{task.id}",
            title=task.title,
            state=state,
            priority=priority,
            deadline=task.due_date,
            tags=tags,
            body=task.body,
            external_id=external_id,
            external_url=task.url,
            sync_status="synced",
            sync_updated=datetime.now(),
            properties={
                "CREATED": datetime.now().strftime("[%Y-%m-%d %a]"),
                "EXTERNAL_ID": external_id,
                "EXTERNAL_URL": f"[[{task.url}][{external_id.split(':')[1]}]]",
                "SYNC_STATUS": "synced",
                "SYNC_UPDATED": datetime.now().strftime("[%Y-%m-%d %a %H:%M]"),
            }
        )

    def get_org_file_path(self, destination: str, space: str = "0-personal") -> Path:
        """
        Get full path to org file.

        Args:
            destination: Org file name (inbox.org, next_actions.org)
            space: Space name (0-personal, 1-datafund, etc.)

        Returns:
            Full path to org file
        """
        return self.data_dir / space / "org" / destination
