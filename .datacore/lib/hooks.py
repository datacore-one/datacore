#!/usr/bin/env python3
"""
Agent Lifecycle Hooks for DIP-0016 §16-18

This module implements the hook system for agent lifecycle management:
- Pre-execution hooks: context injection, validation
- Post-execution hooks: learning extraction, metrics logging
- Validation hooks: output quality checks
- Error hooks: classification, retry scheduling, escalation

Usage:
    from hooks import HookExecutor

    executor = HookExecutor()

    # Pre-execution
    should_continue, context = executor.execute_pre_hooks("gtd-inbox-processor", "Process inbox")
    if not should_continue:
        print(f"Aborted: {context}")
        return

    # ... agent execution with injected context ...

    # Post-execution
    executor.execute_post_hooks("gtd-inbox-processor", result)

    # On error
    instructions = executor.execute_error_hooks("gtd-inbox-processor", error)
"""

import os
import re
import subprocess
import sys
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from state_store import YamlStateStore

# Status constants for learning candidates and embed queue
STATUS_PENDING = "pending"
STATUS_EMBEDDED = "embedded"

# Default paths
DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
REGISTRY_PATH = DATACORE_ROOT / ".datacore" / "registry" / "agents.yaml"
HOOK_STATE_PATH = DATACORE_ROOT / ".datacore" / "state" / "hook_state.yaml"
DIPS_PATH = DATACORE_ROOT / ".datacore" / "dips"


@dataclass
class HookResult:
    """Result from executing a hook."""
    success: bool = True
    context: str = ""
    message: str = ""
    abort: bool = False
    data: Dict[str, Any] = field(default_factory=dict)


class HookExecutor:
    """
    Executes lifecycle hooks for agents based on registry configuration.

    Implements DIP-0016 §16-18:
    - Resolves hooks from defaults, profiles, and agent-specific config
    - Executes built-in hook types
    - Manages hook state persistence
    """

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.registry = self._load_registry()
        self._state_store = YamlStateStore(
            ".datacore/state/hook_state.yaml",
            default={
                "retry_counts": {},
                "last_executions": {},
                "space_cache": {"discovered_spaces": [], "cache_time": None},
                "error_classifications": {},
                "metrics": {}
            },
        )
        self.state = self._state_store.load()
        self._start_time: Optional[datetime] = None

    def _load_registry(self) -> Dict[str, Any]:
        """Load the agent registry."""
        if not REGISTRY_PATH.exists():
            return {}
        with open(REGISTRY_PATH, 'r') as f:
            return yaml.safe_load(f) or {}

    def _save_state(self) -> None:
        """Save hook state."""
        self.state["updated"] = datetime.now(timezone.utc).isoformat()
        self._state_store.save(self.state)

    def _log(self, message: str) -> None:
        """Log debug message if debug mode enabled."""
        if self.debug:
            print(f"[HOOK] {message}")

    def _get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get agent metadata from registry."""
        agents = self.registry.get("agents", {})
        if agent_id in agents:
            return agents[agent_id]

        # Check module_agents
        module_agents = self.registry.get("module_agents", {})
        if agent_id in module_agents:
            return module_agents[agent_id]

        return None

    def _resolve_hooks(self, agent_id: str, hook_type: str) -> List[Dict[str, Any]]:
        """
        Resolve hooks for an agent with inheritance.

        Order: defaults -> profile -> agent-specific
        """
        resolved = []
        agent = self._get_agent(agent_id)
        if not agent:
            return resolved

        # Get defaults
        defaults = self.registry.get("defaults", {}).get("hooks", {})
        default_hooks = defaults.get(hook_type, [])

        # Get profile hooks
        profile_name = agent.get("profile")
        profile_hooks = []
        if profile_name:
            profiles = self.registry.get("profiles", {})
            profile = profiles.get(profile_name, {})
            profile_hooks = profile.get("hooks", {}).get(hook_type, [])

        # Get agent-specific hooks
        agent_hooks = agent.get("hooks", {}).get(hook_type, [])

        # Process hooks with inheritance
        def process_hooks(hooks: List[Dict], source: str) -> None:
            for hook in hooks:
                if hook.get("inherit") == "defaults":
                    # Insert default hooks at this position
                    for dh in default_hooks:
                        resolved.append(dh.copy())
                        self._log(f"  Inherited from defaults: {dh.get('type')}")
                else:
                    resolved.append(hook.copy())
                    self._log(f"  From {source}: {hook.get('type')}")

        self._log(f"Resolving {hook_type} hooks for {agent_id}")

        # If agent has hooks, use them (with inheritance)
        if agent_hooks:
            process_hooks(agent_hooks, "agent")
        # Else if profile has hooks, use them (with inheritance)
        elif profile_hooks:
            process_hooks(profile_hooks, f"profile:{profile_name}")
        # Else use defaults directly
        elif default_hooks:
            for hook in default_hooks:
                resolved.append(hook.copy())
                self._log(f"  From defaults: {hook.get('type')}")

        return resolved

    # =========================================================================
    # Pre-Execution Hooks
    # =========================================================================

    def execute_pre_hooks(self, agent_id: str, task_context: str) -> Tuple[bool, str]:
        """
        Execute pre-execution hooks.

        Returns:
            Tuple of (should_continue, injected_context)
        """
        self._start_time = datetime.now(timezone.utc)
        self._log(f"Pre-execution for {agent_id}")

        hooks = self._resolve_hooks(agent_id, "pre")
        injected_parts = []

        for hook in hooks:
            hook_type = hook.get("type")
            config = hook.get("config", {})

            self._log(f"  Executing: {hook_type}")

            if hook_type == "context-inject":
                result = self._hook_context_inject(agent_id, task_context, config)
            elif hook_type == "validate-preconditions":
                result = self._hook_validate_preconditions(agent_id, config)
            elif hook_type == "discover-spaces":
                result = self._hook_discover_spaces(config)
            else:
                self._log(f"    Unknown hook type: {hook_type}")
                continue

            if result.abort:
                self._log(f"  ABORTED: {result.message}")
                return (False, result.message)

            if result.context:
                injected_parts.append(result.context)
                self._log(f"    Injected {len(result.context)} chars")

        # Combine all injected context
        combined = ""
        if injected_parts:
            combined = "\n\n---\n\n".join(injected_parts)
            self._log(f"Pre-execution complete: {len(combined)} chars injected")

        return (True, combined)

    def _hook_context_inject(self, agent_id: str, task_context: str, config: Dict) -> HookResult:
        """
        Inject context before agent execution.

        Loads:
        - reads.required files
        - references.dips (Agent Context section only)
        - references.specs
        - session memory (if enabled)
        """
        agent = self._get_agent(agent_id)
        if not agent:
            return HookResult(success=False, message=f"Agent not found: {agent_id}")

        injected = []
        total_tokens = 0
        max_tokens = config.get("max_context_tokens", 50000)

        # 1. Load DIPs first (compact, essential context)
        if config.get("auto_load_dips", True):
            dips = agent.get("references", {}).get("dips", [])
            dip_section = config.get("dip_section", "Agent Context")

            for dip_ref in dips:
                dip_content = self._load_dip_section(dip_ref, dip_section)
                if dip_content:
                    section = f"## {dip_ref} Context\n\n{dip_content}"
                    injected.append(section)
                    self._log(f"    Loaded DIP: {dip_ref}")

        # 2. Load reads.required files
        if config.get("auto_load_reads", True):
            reads = agent.get("reads", {}).get("required", [])
            for path in reads:
                content = self._load_file(path)
                if content:
                    section = f"## File: {path}\n\n{content}"
                    injected.append(section)
                    self._log(f"    Loaded: {path}")

        # 3. Load specs (if enabled)
        if config.get("auto_load_specs", False):
            specs = agent.get("references", {}).get("specs", [])
            for spec in specs:
                content = self._load_file(f".datacore/specs/{spec}")
                if content:
                    section = f"## Spec: {spec}\n\n{content}"
                    injected.append(section)
                    self._log(f"    Loaded spec: {spec}")

        # 4. Session memory (if enabled)
        if config.get("inject_session_memory", False):
            # TODO: Integrate with datacortex for semantic search
            self._log(f"    Session memory injection not yet implemented")

        # Combine and truncate if needed
        combined = "\n\n---\n\n".join(injected)

        # Simple token estimation (1 token ≈ 4 chars)
        estimated_tokens = len(combined) // 4
        if estimated_tokens > max_tokens:
            # Truncate to max_tokens
            max_chars = max_tokens * 4
            combined = combined[:max_chars] + "\n\n[... truncated ...]"
            self._log(f"    Truncated to {max_tokens} tokens")

        return HookResult(success=True, context=combined)

    def _load_file(self, path: str) -> Optional[str]:
        """Load file content, handling relative paths."""
        if path.startswith("/"):
            full_path = Path(path)
        else:
            full_path = DATACORE_ROOT / path

        if full_path.exists() and full_path.is_file():
            try:
                return full_path.read_text()
            except Exception:
                return None
        return None

    def _load_dip_section(self, dip_ref: str, section_name: str) -> Optional[str]:
        """Load a specific section from a DIP file.

        Falls back to Summary section or first 2000 chars if Agent Context not found.
        """
        # Handle both "DIP-0009" and "DIP-0009-gtd-specification" formats
        if not dip_ref.startswith("DIP-"):
            return None

        # Find the DIP file
        dip_files = list(DIPS_PATH.glob(f"{dip_ref}*.md"))
        if not dip_files:
            return None

        dip_path = dip_files[0]
        try:
            content = dip_path.read_text()
        except Exception:
            return None

        # Try to find the requested section
        section_content = self._extract_section(content, section_name)

        # Fallback: try Summary section
        if not section_content:
            section_content = self._extract_section(content, "Summary")
            if section_content:
                self._log(f"      Fallback to Summary section")

        # Fallback: use first portion of DIP (after frontmatter)
        if not section_content:
            # Skip frontmatter table
            lines = content.split("\n")
            start_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("## "):
                    start_idx = i
                    break

            # Take next 50 lines or 2000 chars
            subset = "\n".join(lines[start_idx:start_idx + 50])
            if len(subset) > 2000:
                subset = subset[:2000] + "\n\n[... truncated ...]"
            section_content = subset
            self._log(f"      Fallback to first 2000 chars")

        return section_content

    def _extract_section(self, content: str, section_name: str) -> Optional[str]:
        """Extract a section by name from markdown content."""
        # Look for "## Section Name" or "### Section Name"
        pattern = rf'^##\s*{re.escape(section_name)}\s*$'
        match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)

        if not match:
            # Try with ### prefix
            pattern = rf'^###\s*{re.escape(section_name)}\s*$'
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)

        if not match:
            return None

        # Determine section level (## = 2, ### = 3)
        section_level = match.group().count('#')

        # Extract content until next heading of same or higher level
        start = match.end()

        # Find next heading of same or higher level
        # If we matched "## Section", stop at "## " or "# "
        # If we matched "### Section", stop at "### ", "## ", or "# "
        if section_level == 2:
            # Stop at ## but not at ###
            next_heading = re.search(r'^## [A-Z]', content[start:], re.MULTILINE)
        else:
            # For ### sections, stop at ## or ###
            next_heading = re.search(r'^##[#]? [A-Z]', content[start:], re.MULTILINE)

        if next_heading:
            end = start + next_heading.start()
        else:
            end = len(content)

        section_content = content[start:end].strip()
        return section_content if section_content else None

    def _hook_validate_preconditions(self, agent_id: str, config: Dict) -> HookResult:
        """
        Validate preconditions before execution.

        Checks:
        - required_files exist
        - required_state conditions are met
        """
        errors = []

        # Check required files
        required_files = config.get("required_files", [])
        space = config.get("space", "0-personal")
        for path_template in required_files:
            # Handle {space} placeholder
            path = path_template.replace("{space}", space)
            full_path = DATACORE_ROOT / path

            if not full_path.exists():
                errors.append(f"Required file missing: {path}")

        # Check required state
        required_state = config.get("required_state", [])
        for condition in required_state:
            if condition == "inbox_not_empty":
                inbox_path = DATACORE_ROOT / space / "org" / "inbox.org"
                if inbox_path.exists():
                    content = inbox_path.read_text()
                    # Check if there are any TODO items
                    if not re.search(r'^\*+\s+(TODO|NEXT)', content, re.MULTILINE):
                        errors.append("Inbox is empty - no items to process")

            elif condition == "git_clean":
                result = subprocess.run(
                    ["git", "-C", str(DATACORE_ROOT), "status", "--porcelain"],
                    capture_output=True, text=True, timeout=30
                )
                if result.stdout.strip():
                    errors.append("Git working directory is not clean")

            elif condition == "org_files_readable":
                org_path = DATACORE_ROOT / space / "org"
                if not org_path.exists():
                    errors.append("Org directory not found")

        if errors and config.get("abort_on_failure", True):
            return HookResult(success=False, abort=True, message="\n".join(errors))

        return HookResult(success=True, message="All preconditions met")

    def _hook_discover_spaces(self, config: Dict) -> HookResult:
        """
        Discover active spaces for coordinators.

        Returns space list in result.data["spaces"]
        """
        # Check cache first
        cache = self.state.get("space_cache", {})
        cache_time = cache.get("cache_time")
        cache_ttl = config.get("cache_ttl_minutes", 5)

        if cache_time:
            try:
                cached_at = datetime.fromisoformat(cache_time)
                if datetime.now(timezone.utc) - cached_at < timedelta(minutes=cache_ttl):
                    spaces = cache.get("discovered_spaces", [])
                    self._log(f"    Using cached spaces: {spaces}")
                    return HookResult(success=True, data={"spaces": spaces})
            except Exception:
                pass

        # Discover spaces
        pattern = config.get("pattern", "[0-9]-*/")
        exclude = config.get("exclude", [])
        filter_by_activity = config.get("filter_by_git_activity", False)
        activity_window = config.get("activity_window", "24h")

        spaces = []
        for path in sorted(DATACORE_ROOT.glob(pattern.rstrip("/"))):
            if path.is_dir():
                name = path.name
                # Check exclusions
                if any(ex in name for ex in exclude):
                    continue
                spaces.append(name)

        # Filter by git activity if requested
        if filter_by_activity:
            active_spaces = []
            # Parse activity window (e.g., "24h" -> 24 hours)
            hours = int(activity_window.rstrip("h"))
            since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d")

            for space in spaces:
                space_path = DATACORE_ROOT / space
                result = subprocess.run(
                    ["git", "-C", str(space_path), "log", "--oneline", f"--since={since}", "-1"],
                    capture_output=True, text=True, timeout=30
                )
                if result.stdout.strip():
                    active_spaces.append(space)

            spaces = active_spaces
            self._log(f"    Filtered to active spaces: {spaces}")

        # Update cache
        self.state["space_cache"] = {
            "discovered_spaces": spaces,
            "cache_time": datetime.now(timezone.utc).isoformat()
        }
        self._save_state()

        return HookResult(success=True, data={"spaces": spaces})

    # =========================================================================
    # Post-Execution Hooks
    # =========================================================================

    def execute_post_hooks(self, agent_id: str, result: Dict[str, Any]) -> None:
        """Execute post-execution hooks."""
        self._log(f"Post-execution for {agent_id}")

        hooks = self._resolve_hooks(agent_id, "post")

        for hook in hooks:
            hook_type = hook.get("type")
            config = hook.get("config", {})

            self._log(f"  Executing: {hook_type}")

            if hook_type == "metrics-log":
                self._hook_metrics_log(agent_id, result, config)
            elif hook_type == "learning-extract":
                self._hook_learning_extract(agent_id, result, config)
            elif hook_type == "journal-append":
                self._hook_journal_append(agent_id, result, config)
            elif hook_type == "embed-outputs":
                self._hook_embed_outputs(agent_id, result, config)
            else:
                self._log(f"    Unknown hook type: {hook_type}")

        # Update last execution time
        self.state.setdefault("last_executions", {})[agent_id] = \
            datetime.now(timezone.utc).isoformat()
        self._save_state()

        self._log(f"Post-execution complete")

    def _hook_metrics_log(self, agent_id: str, result: Dict, config: Dict) -> HookResult:
        """Log execution metrics."""
        # Calculate duration
        duration_ms = 0
        if self._start_time:
            duration = datetime.now(timezone.utc) - self._start_time
            duration_ms = int(duration.total_seconds() * 1000)

        metrics = {
            "agent_id": agent_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": result.get("status", "unknown")
        }

        if config.get("log_duration", True):
            metrics["duration_ms"] = duration_ms

        if config.get("log_tokens", True):
            metrics["tokens_in"] = result.get("tokens_in", 0)
            metrics["tokens_out"] = result.get("tokens_out", 0)

        if config.get("log_outputs", True):
            metrics["outputs"] = result.get("outputs", {})

        # Append to execution log
        try:
            from execution_logger import log_execution
            log_execution(
                agent_id=agent_id,
                task_id=result.get("task_id", "unknown"),
                status=result.get("status", "success"),
                outputs=result.get("outputs", {}),
                duration_ms=duration_ms
            )
            self._log(f"    Logged metrics: {duration_ms}ms")
        except ImportError:
            self._log(f"    execution_logger not available")

        return HookResult(success=True, data=metrics)

    def _append_to_yaml_list(self, path: Path, entries: List[Dict[str, Any]]) -> None:
        """Load a YAML list file, extend with new entries, and save back.

        If the file is missing or corrupt the list starts empty.
        Parent directories are created automatically.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: List[Dict[str, Any]] = []
        if path.exists():
            try:
                with open(path, 'r') as f:
                    loaded = yaml.safe_load(f)
                    if isinstance(loaded, list):
                        existing = loaded
            except Exception:
                pass
        existing.extend(entries)
        with open(path, 'w') as f:
            yaml.safe_dump(existing, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def _hook_learning_extract(self, agent_id: str, result: Dict, config: Dict) -> HookResult:
        """
        Extract learnings from agent execution output.

        Analyzes output text for patterns, corrections, and principles using
        heuristic keyword matching and structured section detection. Appends
        extracted candidates to .datacore/state/learning_candidates.yaml.

        Config options:
            extract_patterns (bool): Look for pattern-type learnings (default True)
            extract_corrections (bool): Look for correction-type learnings (default True)
            extract_principles (bool): Look for principle-type learnings (default False)
            target (str): Override output path (default: state/learning_candidates.yaml)
        """
        output_text = result.get("output_text", "")
        if not output_text:
            self._log(f"    No output_text in result, skipping learning extraction")
            return HookResult(success=True, data={"patterns": [], "corrections": [], "principles": []})

        extract_patterns = config.get("extract_patterns", True)
        extract_corrections = config.get("extract_corrections", True)
        extract_principles = config.get("extract_principles", False)

        learnings: Dict[str, List[str]] = {
            "patterns": [],
            "corrections": [],
            "principles": []
        }

        # --- Strategy 1: Extract from structured sections ---
        section_map = {
            "patterns": ["Learnings", "Patterns", "Insights", "Observations", "What Worked"],
            "corrections": ["Corrections", "Mistakes", "Issues", "What Failed", "Errors Found"],
            "principles": ["Principles", "Rules", "Guidelines", "Takeaways"],
        }
        for category, section_names in section_map.items():
            if category == "patterns" and not extract_patterns:
                continue
            if category == "corrections" and not extract_corrections:
                continue
            if category == "principles" and not extract_principles:
                continue
            for section_name in section_names:
                section_content = self._extract_section(output_text, section_name)
                if section_content:
                    # Extract bullet points or lines from the section
                    for line in section_content.split("\n"):
                        line = line.strip()
                        # Match lines starting with -, *, or numbered lists
                        cleaned = re.sub(r'^[-*]\s+|^\d+[.)]\s+', '', line).strip()
                        if cleaned and len(cleaned) > 15 and not cleaned.startswith('#'):
                            learnings[category].append(cleaned)

        # --- Strategy 2: Keyword-based line extraction ---
        pattern_keywords = ["learned", "pattern", "insight", "discovered", "noticed", "realized", "observation"]
        correction_keywords = ["correction", "mistake", "error", "wrong", "should have", "instead of", "fixed"]
        principle_keywords = ["principle", "rule", "always", "never", "important to"]

        for line in output_text.split("\n"):
            line_lower = line.strip().lower()
            if not line_lower or len(line_lower) < 20:
                continue
            # Skip markdown headings and code blocks
            if line.strip().startswith('#') or line.strip().startswith('```'):
                continue

            cleaned = re.sub(r'^[-*]\s+|^\d+[.)]\s+', '', line.strip()).strip()

            if extract_patterns and any(kw in line_lower for kw in pattern_keywords):
                if cleaned not in learnings["patterns"]:
                    learnings["patterns"].append(cleaned)

            if extract_corrections and any(kw in line_lower for kw in correction_keywords):
                if cleaned not in learnings["corrections"]:
                    learnings["corrections"].append(cleaned)

            if extract_principles and any(kw in line_lower for kw in principle_keywords):
                if cleaned not in learnings["principles"]:
                    learnings["principles"].append(cleaned)

        # --- Write candidates to state file ---
        total = sum(len(v) for v in learnings.values())
        if total > 0:
            timestamp = datetime.now(timezone.utc).isoformat()
            new_entries = [
                {
                    "agent": agent_id,
                    "category": category,
                    "text": item,
                    "timestamp": timestamp,
                    "status": STATUS_PENDING,
                }
                for category, items in learnings.items()
                for item in items
            ]
            candidates_path = DATACORE_ROOT / ".datacore" / "state" / "learning_candidates.yaml"
            self._append_to_yaml_list(candidates_path, new_entries)

            self._log(f"    Extracted {total} learning candidates ({len(learnings['patterns'])} patterns, "
                       f"{len(learnings['corrections'])} corrections, {len(learnings['principles'])} principles)")
        else:
            self._log(f"    No learnings extracted from {agent_id} output")

        return HookResult(success=True, data=learnings)

    def _hook_embed_outputs(self, agent_id: str, result: Dict, config: Dict) -> HookResult:
        """
        Embed agent output text into Datacortex for semantic search.

        Uses the datacortex embeddings module (sentence-transformers) if available.
        Falls back to a log message if the module cannot be imported (e.g. when
        running outside the datacortex virtualenv or when dependencies are missing).

        Config options:
            target (str): Target store — currently only "datacortex" supported
            types (list): Document types to embed (e.g. ["zettel", "literature-note"])
        """
        output_text = result.get("output_text", "")
        if not output_text or len(output_text) < 50:
            self._log(f"    Output too short for embedding, skipping")
            return HookResult(success=True)

        target = config.get("target", "datacortex")
        if target != "datacortex":
            self._log(f"    Unknown embed target: {target}, skipping")
            return HookResult(success=True)

        # Try to import the datacortex embedding module
        try:
            datacortex_path = DATACORE_ROOT / ".datacore" / "modules" / "datacortex" / "src"
            if str(datacortex_path) not in sys.path:
                sys.path.insert(0, str(datacortex_path))
            from datacortex.ai.embeddings import embed_text
        except ImportError as e:
            self._log(f"    Embedding requires datacortex module — skipping ({e})")
            # TODO: Consider MCP-based embedding fallback when datacortex
            # exposes an embed endpoint via its MCP server
            return HookResult(success=True, message="Embedding skipped: datacortex not available")

        try:
            # Generate a document ID from agent + timestamp
            timestamp = datetime.now(timezone.utc).isoformat()
            doc_id = f"{agent_id}_{timestamp}"

            # Embed the output (title + first 500 chars as per datacortex convention)
            title = f"Agent output: {agent_id}"
            text_to_embed = f"{title}\n\n{output_text[:500]}"
            embedding = embed_text(text_to_embed)

            # Store embedding metadata in state for later indexing
            embed_log_path = DATACORE_ROOT / ".datacore" / "state" / "embed_queue.yaml"

            self._append_to_yaml_list(embed_log_path, [{
                "doc_id": doc_id,
                "agent": agent_id,
                "title": title,
                "text_preview": output_text[:200],
                "embedding_shape": list(embedding.shape),
                "timestamp": timestamp,
                "status": STATUS_EMBEDDED,
            }])

            self._log(f"    Embedded output ({embedding.shape}) for {agent_id}")
            return HookResult(success=True, data={"embedded": True, "doc_id": doc_id})

        except Exception as e:
            self._log(f"    Embedding failed: {e}")
            return HookResult(success=False, message=f"Embedding failed: {e}")

    def _hook_journal_append(self, agent_id: str, result: Dict, config: Dict) -> HookResult:
        """Append execution summary to daily journal."""
        summary_length = config.get("summary_length", "brief")
        include_outputs = config.get("include_outputs", True)

        # Get today's journal path
        today = datetime.now().strftime("%Y-%m-%d")
        space = config.get("space", "0-personal")
        journal_dir = DATACORE_ROOT / space / "notes" / "journals"
        if not journal_dir.exists():
            journal_dir = DATACORE_ROOT / space / "journal"
        journal_path = journal_dir / f"{today}.md"

        if not journal_path.exists():
            self._log(f"    Journal not found: {journal_path}")
            return HookResult(success=False, message="Journal not found")

        # Build summary
        status = result.get("status", "completed")
        summary = f"\n\n### Agent: {agent_id}\n\n"
        summary += f"**Status**: {status}\n"

        if include_outputs and result.get("outputs"):
            summary += f"**Outputs**: {result['outputs']}\n"

        # Append to journal
        try:
            with open(journal_path, 'a') as f:
                f.write(summary)
            self._log(f"    Appended to journal")
        except Exception as e:
            self._log(f"    Failed to append: {e}")

        return HookResult(success=True)

    # =========================================================================
    # Validation Hooks
    # =========================================================================

    def execute_validate_hooks(self, agent_id: str, result: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Execute validation hooks before marking complete.

        Returns:
            Tuple of (passed, message)
        """
        self._log(f"Validation for {agent_id}")

        hooks = self._resolve_hooks(agent_id, "validate")

        for hook in hooks:
            hook_type = hook.get("type")
            config = hook.get("config", {})

            self._log(f"  Executing: {hook_type}")

            if hook_type == "output-exists":
                hook_result = self._hook_output_exists(result, config)
            elif hook_type == "quality-gate":
                hook_result = self._hook_quality_gate(result, config)
            else:
                self._log(f"    Unknown hook type: {hook_type}")
                continue

            if not hook_result.success:
                self._log(f"  FAILED: {hook_result.message}")
                return (False, hook_result.message)

        self._log(f"Validation passed")
        return (True, "Validation passed")

    def _hook_output_exists(self, result: Dict, config: Dict) -> HookResult:
        """Verify agent produced expected outputs."""
        if not config.get("check_writes", True):
            return HookResult(success=True)

        outputs = result.get("outputs", {})
        files_created = outputs.get("files_created", [])

        if not files_created:
            return HookResult(success=False, message="No output files created")

        min_size = config.get("min_file_size", 0)
        for file_path in files_created:
            path = Path(file_path)
            if not path.exists():
                return HookResult(success=False, message=f"Output file missing: {file_path}")
            if path.stat().st_size < min_size:
                return HookResult(success=False, message=f"Output file too small: {file_path}")

        return HookResult(success=True)

    def _hook_quality_gate(self, result: Dict, config: Dict) -> HookResult:
        """Validate output quality."""
        output_text = result.get("output_text", "")

        # Check minimum length
        min_length = config.get("min_output_length", 0)
        if len(output_text) < min_length:
            return HookResult(success=False, message=f"Output too short: {len(output_text)} < {min_length}")

        # Check forbidden patterns
        forbidden = config.get("forbidden_patterns", [])
        for pattern in forbidden:
            if pattern.lower() in output_text.lower():
                return HookResult(success=False, message=f"Forbidden pattern found: {pattern}")

        return HookResult(success=True)

    # =========================================================================
    # Error Hooks
    # =========================================================================

    def execute_error_hooks(self, agent_id: str, error: Exception) -> Dict[str, Any]:
        """
        Execute error hooks.

        Returns:
            Dict with retry/escalation instructions
        """
        self._log(f"Error handling for {agent_id}: {error}")

        hooks = self._resolve_hooks(agent_id, "on_error")
        instructions = {
            "retry": False,
            "retry_delay": 0,
            "escalate": False,
            "error_type": "unknown"
        }

        for hook in hooks:
            hook_type = hook.get("type")
            config = hook.get("config", {})

            self._log(f"  Executing: {hook_type}")

            if hook_type == "classify-error":
                result = self._hook_classify_error(error, config)
                instructions["error_type"] = result.data.get("error_type", "unknown")

            elif hook_type == "retry-schedule":
                result = self._hook_retry_schedule(agent_id, instructions, config)
                instructions.update(result.data)

            elif hook_type == "escalate":
                result = self._hook_escalate(agent_id, instructions, config)
                instructions.update(result.data)

            else:
                self._log(f"    Unknown hook type: {hook_type}")

        self._save_state()
        return instructions

    def _hook_classify_error(self, error: Exception, config: Dict) -> HookResult:
        """Classify error as transient or permanent."""
        error_str = str(error).lower()

        transient_patterns = config.get("transient_patterns", [])
        permanent_patterns = config.get("permanent_patterns", [])

        for pattern in transient_patterns:
            if pattern.lower() in error_str:
                self._log(f"    Classified as transient: {pattern}")
                return HookResult(success=True, data={"error_type": "transient"})

        for pattern in permanent_patterns:
            if pattern.lower() in error_str:
                self._log(f"    Classified as permanent: {pattern}")
                return HookResult(success=True, data={"error_type": "permanent"})

        self._log(f"    Classified as unknown")
        return HookResult(success=True, data={"error_type": "unknown"})

    def _hook_retry_schedule(self, agent_id: str, instructions: Dict, config: Dict) -> HookResult:
        """Schedule retry for transient errors."""
        # Only retry transient errors if configured
        if config.get("only_transient", True) and instructions.get("error_type") != "transient":
            return HookResult(success=True, data={"retry": False})

        # Get retry count
        retry_counts = self.state.setdefault("retry_counts", {})
        agent_retries = retry_counts.setdefault(agent_id, {})
        task_id = instructions.get("task_id", "default")
        current_retries = agent_retries.get(task_id, 0)

        max_retries = config.get("max_retries", 3)
        backoff = config.get("backoff", [3600, 10800, 21600])  # 1h, 3h, 6h

        if current_retries >= max_retries:
            self._log(f"    Max retries ({max_retries}) exceeded")
            return HookResult(success=True, data={"retry": False})

        # Schedule retry
        delay_index = min(current_retries, len(backoff) - 1)
        delay = backoff[delay_index]

        # Increment retry count
        agent_retries[task_id] = current_retries + 1

        self._log(f"    Scheduled retry {current_retries + 1}/{max_retries} in {delay}s")
        return HookResult(success=True, data={
            "retry": True,
            "retry_delay": delay,
            "retry_count": current_retries + 1,
            "queue": config.get("queue", "nightshift")
        })

    def _hook_escalate(self, agent_id: str, instructions: Dict, config: Dict) -> HookResult:
        """Escalate to human queue."""
        after_retries = config.get("after_retries", 3)
        retry_count = instructions.get("retry_count", 0)

        if retry_count < after_retries and instructions.get("retry", False):
            # Not yet time to escalate
            return HookResult(success=True, data={"escalate": False})

        target = config.get("target", "human_queue")
        notify = config.get("notify", True)

        self._log(f"    Escalating to {target}")

        # Update metrics
        metrics = self.state.setdefault("metrics", {})
        metrics["total_escalations"] = metrics.get("total_escalations", 0) + 1

        return HookResult(success=True, data={
            "escalate": True,
            "escalate_target": target,
            "notify": notify
        })


# =============================================================================
# Convenience Functions
# =============================================================================

def inject_context(agent_id: str, task_context: str = "", debug: bool = False) -> Tuple[bool, str]:
    """
    Convenience function to inject context for an agent.

    Returns:
        Tuple of (should_continue, injected_context)
    """
    executor = HookExecutor(debug=debug)
    return executor.execute_pre_hooks(agent_id, task_context)


def log_completion(agent_id: str, result: Dict[str, Any], debug: bool = False) -> None:
    """Convenience function to run post-execution hooks."""
    executor = HookExecutor(debug=debug)
    executor.execute_post_hooks(agent_id, result)


def handle_error(agent_id: str, error: Exception, debug: bool = False) -> Dict[str, Any]:
    """Convenience function to handle agent errors."""
    executor = HookExecutor(debug=debug)
    return executor.execute_error_hooks(agent_id, error)


# =============================================================================
# CLI Interface
# =============================================================================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python hooks.py <agent_id> [--debug]")
        print("\nTests hook execution for an agent")
        sys.exit(1)

    agent_id = sys.argv[1]
    debug = "--debug" in sys.argv

    print(f"Testing hooks for agent: {agent_id}")
    print("=" * 60)

    # Test pre-hooks
    print("\n[PRE-HOOKS]")
    should_continue, context = inject_context(agent_id, "Test task", debug=debug)
    print(f"Should continue: {should_continue}")
    print(f"Context length: {len(context)} chars")
    if context and debug:
        print(f"Context preview:\n{context[:500]}...")

    # Test post-hooks (with mock result)
    print("\n[POST-HOOKS]")
    mock_result = {
        "status": "success",
        "outputs": {"files_created": []},
        "tokens_in": 1000,
        "tokens_out": 500
    }
    log_completion(agent_id, mock_result, debug=debug)

    # Test error hooks (with mock error)
    print("\n[ERROR-HOOKS]")
    mock_error = Exception("rate_limit exceeded")
    instructions = handle_error(agent_id, mock_error, debug=debug)
    print(f"Instructions: {instructions}")

    print("\n" + "=" * 60)
    print("Hook test complete")
