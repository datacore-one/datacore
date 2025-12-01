#!/usr/bin/env python3
"""
Agent Context Loader for DIP-0016 Knowledge Pre-Fetch

This module provides functions to load an agent's required context
before execution, based on the registry's reads.required and reads.contextual.

Usage:
    from agent_loader import load_agent_context

    # Get context for an agent
    context = load_agent_context("knowledge-extractor")

    # Context includes:
    # - required_files: Dict of path -> content
    # - contextual_results: Dict of query -> search results
    # - dip_content: Dict of DIP reference -> content
"""

import os
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List
import yaml

# Default paths
DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
REGISTRY_PATH = DATACORE_ROOT / ".datacore" / "registry" / "agents.yaml"


def load_registry() -> Dict[str, Any]:
    """Load the agent registry."""
    if not REGISTRY_PATH.exists():
        return {"agents": {}, "module_agents": {}}

    with open(REGISTRY_PATH, 'r') as f:
        return yaml.safe_load(f) or {}


def get_agent_metadata(agent_id: str) -> Optional[Dict[str, Any]]:
    """Get metadata for a specific agent from registry."""
    registry = load_registry()

    # Check core agents
    if agent_id in registry.get("agents", {}):
        return registry["agents"][agent_id]

    # Check module agents
    if agent_id in registry.get("module_agents", {}):
        return registry["module_agents"][agent_id]

    return None


def load_file_content(path: str) -> Optional[str]:
    """Load content from a file path.

    Handles both relative (to DATACORE_ROOT) and absolute paths.
    Supports glob patterns for directories.
    """
    # Resolve path
    if path.startswith("/"):
        full_path = Path(path)
    else:
        full_path = DATACORE_ROOT / path

    # Handle glob patterns
    if "*" in str(full_path):
        parent = full_path.parent
        pattern = full_path.name

        if not parent.exists():
            return None

        contents = []
        for file_path in parent.glob(pattern):
            if file_path.is_file():
                try:
                    contents.append(f"=== {file_path.name} ===\n{file_path.read_text()}")
                except Exception:
                    pass

        return "\n\n".join(contents) if contents else None

    # Regular file
    if full_path.exists() and full_path.is_file():
        try:
            return full_path.read_text()
        except Exception:
            return None

    # Directory - read all .md files
    if full_path.exists() and full_path.is_dir():
        contents = []
        for file_path in sorted(full_path.glob("*.md")):
            try:
                contents.append(f"=== {file_path.name} ===\n{file_path.read_text()}")
            except Exception:
                pass
        return "\n\n".join(contents) if contents else None

    return None


def run_contextual_query(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """Run a datacortex search query for contextual knowledge.

    Args:
        query: Search query (from reads.contextual)
        top_k: Number of results to return

    Returns:
        List of search results with title, path, and snippet
    """
    # Strip "query: " prefix if present
    if query.startswith("query:"):
        query = query[6:].strip()

    try:
        # Run datacortex search
        result = subprocess.run(
            ["datacortex", "search", query, "--top", str(top_k), "--no-expand"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(DATACORE_ROOT)
        )

        if result.returncode != 0:
            return []

        # Parse output - datacortex outputs a temp file path
        output_lines = result.stdout.strip().split("\n")
        if output_lines:
            temp_path = Path(output_lines[-1].strip())
            if temp_path.exists():
                content = temp_path.read_text()
                return [{"raw": content}]

        return []

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def load_dip_content(dip_ref: str) -> Optional[str]:
    """Load content from a DIP reference.

    Args:
        dip_ref: DIP reference (e.g., "DIP-0009", "DIP-0016")

    Returns:
        DIP content or None
    """
    dips_dir = DATACORE_ROOT / ".datacore" / "dips"

    # Find matching DIP file
    for dip_file in dips_dir.glob(f"{dip_ref}*.md"):
        try:
            return dip_file.read_text()
        except Exception:
            pass

    return None


def load_agent_context(
    agent_id: str,
    include_dips: bool = True,
    include_contextual: bool = True,
    max_context_size: int = 50000
) -> Dict[str, Any]:
    """Load full context for an agent before execution.

    This implements the Knowledge Pre-Fetch pattern from DIP-0016.

    Args:
        agent_id: Agent identifier
        include_dips: Whether to load referenced DIPs
        include_contextual: Whether to run contextual queries
        max_context_size: Maximum total context size in characters

    Returns:
        Dict with:
            - agent_id: str
            - agent_metadata: Dict
            - required_files: Dict[path, content]
            - contextual_results: Dict[query, results]
            - dip_content: Dict[dip_ref, content]
            - total_size: int (characters)
            - truncated: bool
    """
    metadata = get_agent_metadata(agent_id)

    if not metadata:
        return {
            "agent_id": agent_id,
            "agent_metadata": None,
            "required_files": {},
            "contextual_results": {},
            "dip_content": {},
            "total_size": 0,
            "truncated": False,
            "error": f"Agent not found in registry: {agent_id}"
        }

    result = {
        "agent_id": agent_id,
        "agent_metadata": metadata,
        "required_files": {},
        "contextual_results": {},
        "dip_content": {},
        "total_size": 0,
        "truncated": False
    }

    current_size = 0

    # Load required files
    reads = metadata.get("reads", {})
    required_paths = reads.get("required", [])

    for path in required_paths:
        if current_size >= max_context_size:
            result["truncated"] = True
            break

        content = load_file_content(path)
        if content:
            # Truncate if needed
            remaining = max_context_size - current_size
            if len(content) > remaining:
                content = content[:remaining] + "\n... [truncated]"
                result["truncated"] = True

            result["required_files"][path] = content
            current_size += len(content)

    # Load DIPs
    if include_dips and current_size < max_context_size:
        refs = metadata.get("references", {})
        dip_refs = refs.get("dips", [])

        for dip_ref in dip_refs:
            if current_size >= max_context_size:
                result["truncated"] = True
                break

            content = load_dip_content(dip_ref)
            if content:
                remaining = max_context_size - current_size
                if len(content) > remaining:
                    content = content[:remaining] + "\n... [truncated]"
                    result["truncated"] = True

                result["dip_content"][dip_ref] = content
                current_size += len(content)

    # Run contextual queries
    if include_contextual and current_size < max_context_size:
        contextual_queries = reads.get("contextual", [])

        for query in contextual_queries:
            if current_size >= max_context_size:
                result["truncated"] = True
                break

            search_results = run_contextual_query(query)
            if search_results:
                result["contextual_results"][query] = search_results

                # Estimate size
                for sr in search_results:
                    current_size += len(str(sr))

    result["total_size"] = current_size
    return result


def format_context_for_prompt(context: Dict[str, Any]) -> str:
    """Format loaded context as a prompt section.

    Args:
        context: Result from load_agent_context()

    Returns:
        Formatted string for prompt injection
    """
    lines = ["## Pre-Loaded Context (DIP-0016)", ""]

    # Required files
    if context.get("required_files"):
        lines.append("### Required Files")
        lines.append("")
        for path, content in context["required_files"].items():
            lines.append(f"#### {path}")
            lines.append("```")
            # Limit content preview
            preview = content[:2000] if len(content) > 2000 else content
            lines.append(preview)
            if len(content) > 2000:
                lines.append(f"... ({len(content)} total chars)")
            lines.append("```")
            lines.append("")

    # DIP content
    if context.get("dip_content"):
        lines.append("### Referenced DIPs")
        lines.append("")
        for dip_ref, content in context["dip_content"].items():
            lines.append(f"#### {dip_ref}")
            # Just show summary for DIPs
            lines.append(content[:1500] + "..." if len(content) > 1500 else content)
            lines.append("")

    # Contextual results
    if context.get("contextual_results"):
        lines.append("### Contextual Search Results")
        lines.append("")
        for query, results in context["contextual_results"].items():
            lines.append(f"#### Query: {query}")
            for r in results:
                lines.append(str(r.get("raw", r))[:500])
            lines.append("")

    if context.get("truncated"):
        lines.append("")
        lines.append("*Note: Context was truncated due to size limits*")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: agent_loader.py <agent_id> [--format]")
        print("  --format: Output formatted for prompt injection")
        sys.exit(1)

    agent_id = sys.argv[1]
    format_output = "--format" in sys.argv

    context = load_agent_context(agent_id)

    if format_output:
        print(format_context_for_prompt(context))
    else:
        print(yaml.dump(context, default_flow_style=False))
