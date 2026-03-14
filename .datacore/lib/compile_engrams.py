#!/usr/bin/env python3
"""
Compile engrams into agent-specific files for fallback injection.

Layer 3 of the 3-layer engram injection system (DIP-0019):
  Layer 1 (Hook-based):    hooks.py injects at runtime during nightshift
  Layer 2 (Prompt-based):  Agents call datacore.inject MCP tool
  Layer 3 (Compile-time):  This script bakes engrams into static files

Usage:
    python compile_engrams.py                    # Compile all agents
    python compile_engrams.py --agent gtd-inbox-processor  # Single agent
    python compile_engrams.py --check            # Check staleness only
    python compile_engrams.py --clean            # Remove compiled files

Output: .datacore/state/agent-engrams/{agent-name}.md

The compiled files include a source hash so Layer 1/2 can detect staleness
and trigger recompilation when needed.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
AGENTS_DIRS = [
    DATACORE_ROOT / ".datacore" / "agents",
    *sorted(DATACORE_ROOT.glob("[0-9]-*/.datacore/agents")),
]
MODULE_AGENTS = DATACORE_ROOT / ".datacore" / "modules"
OUTPUT_DIR = DATACORE_ROOT / ".datacore" / "state" / "agent-engrams"
ENGRAM_FILES = [
    DATACORE_ROOT / ".datacore" / "learning" / "engrams.yaml",
    *sorted(DATACORE_ROOT.glob("[0-9]-*/.datacore/learning/engrams.yaml")),
]


def load_all_engrams():
    """Load active engrams from all spaces."""
    engrams = []
    for filepath in ENGRAM_FILES:
        if not filepath.exists():
            continue
        try:
            with open(filepath) as f:
                data = yaml.safe_load(f) or []
            if isinstance(data, dict):
                data = data.get("engrams", [])
            for eng in data:
                if eng.get("status") == "active":
                    eng["_source"] = str(filepath)
                    engrams.append(eng)
        except (yaml.YAMLError, OSError) as e:
            print(f"Warning: {filepath}: {e}", file=sys.stderr)
    return engrams


def engrams_hash(engrams):
    """Compute hash of active engrams for staleness detection."""
    # Hash the sorted list of (id, version, statement) tuples
    items = sorted(
        (e.get("id", ""), e.get("version", 1), e.get("statement", ""))
        for e in engrams
    )
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()[:16]


def scope_matches(engram_scope, agent_name):
    """Check if engram scope matches agent context."""
    if not engram_scope or engram_scope == "global":
        return True
    if engram_scope == f"agent:{agent_name}":
        return True
    # Space-scoped engrams match agents in that space
    if engram_scope.startswith("space:"):
        return False  # Space matching requires knowing agent's space
    return False


def select_for_agent(engrams, agent_name, limit=15):
    """Select engrams relevant to a specific agent."""
    # Include global engrams + agent-scoped engrams
    matched = []
    for eng in engrams:
        scope = eng.get("scope", "global")
        rs = eng.get("activation", {}).get("retrieval_strength", 0.5)
        if rs < 0.1:
            continue
        if scope_matches(scope, agent_name):
            matched.append(eng)

    # Sort by retrieval_strength descending
    matched.sort(
        key=lambda e: e.get("activation", {}).get("retrieval_strength", 0),
        reverse=True,
    )

    # Cap: agent-scoped first, then global up to limit
    agent_scoped = [e for e in matched if e.get("scope", "") == f"agent:{agent_name}"]
    global_scoped = [e for e in matched if e.get("scope", "global") == "global"]

    result = agent_scoped[:limit]
    remaining = limit - len(result)
    if remaining > 0:
        result.extend(global_scoped[:remaining])

    return result


def format_engram(eng, detailed=False):
    """Format a single engram for compiled output."""
    statement = eng.get("statement", "")
    line = f"- **{statement}**"
    if detailed:
        rationale = eng.get("rationale", "")
        if rationale:
            line += f"\n  _{rationale}_"
        contras = eng.get("contraindications", [])
        if contras:
            line += f"\n  Except: {', '.join(contras)}"
        dc = eng.get("dual_coding")
        if dc:
            if dc.get("example"):
                line += f"\n  Example: {dc['example']}"
            if dc.get("analogy"):
                line += f"\n  Analogy: {dc['analogy']}"
    return line


def compile_agent(agent_name, engrams, source_hash):
    """Compile engrams for a single agent."""
    selected = select_for_agent(engrams, agent_name)
    if not selected:
        return None

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    detailed = len(selected) < 10

    # Separate directives vs consider
    directives = [e for e in selected if e.get("activation", {}).get("retrieval_strength", 0) > 0.5]
    consider = [e for e in selected if e.get("activation", {}).get("retrieval_strength", 0) <= 0.5]

    lines = [
        f"<!-- compiled-engrams for agent:{agent_name} -->",
        f"<!-- source-hash: {source_hash} -->",
        f"<!-- compiled-at: {now} -->",
        f"<!-- This file is auto-generated by compile_engrams.py -->",
        f"<!-- Regenerate: python .datacore/lib/compile_engrams.py -->",
        "",
        "# Compiled Engrams",
        "",
        "These are pre-compiled engrams for this agent. If `datacore.inject` MCP",
        "tool is available, prefer calling it for fresh results. These compiled",
        "engrams serve as fallback when MCP is unavailable.",
        "",
    ]

    if directives:
        lines.append("## DIRECTIVES\n")
        for eng in directives:
            lines.append(format_engram(eng, detailed))
        lines.append("")

    if consider:
        lines.append("## ALSO CONSIDER\n")
        for eng in consider:
            lines.append(format_engram(eng, detailed=False))
        lines.append("")

    return "\n".join(lines)


def discover_agents():
    """Discover all agent names from .md files."""
    agents = set()
    for agents_dir in AGENTS_DIRS:
        if not agents_dir.exists():
            continue
        for md_file in agents_dir.glob("*.md"):
            agents.add(md_file.stem)

    # Module agents
    if MODULE_AGENTS.exists():
        for module_dir in MODULE_AGENTS.iterdir():
            if not module_dir.is_dir():
                continue
            agents_subdir = module_dir / "agents"
            if agents_subdir.exists():
                for md_file in agents_subdir.glob("*.md"):
                    agents.add(md_file.stem)

    # Filter out non-agent files
    agents.discard("README")

    return sorted(agents)


def read_compiled_hash(filepath):
    """Read source-hash from a compiled file."""
    if not filepath.exists():
        return None
    try:
        content = filepath.read_text()
        match = re.search(r"source-hash: (\w+)", content)
        return match.group(1) if match else None
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser(description="Compile engrams for agent injection")
    parser.add_argument("--agent", help="Compile for specific agent only")
    parser.add_argument("--check", action="store_true", help="Check staleness only")
    parser.add_argument("--clean", action="store_true", help="Remove compiled files")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.clean:
        if OUTPUT_DIR.exists():
            import shutil
            shutil.rmtree(OUTPUT_DIR)
            print("Cleaned compiled engrams directory")
        return

    # Load engrams
    engrams = load_all_engrams()
    source_hash = engrams_hash(engrams)

    if args.verbose:
        print(f"Loaded {len(engrams)} active engrams (hash: {source_hash})")

    # Discover agents
    if args.agent:
        agent_names = [args.agent]
    else:
        agent_names = discover_agents()

    if args.verbose:
        print(f"Found {len(agent_names)} agents")

    # Check staleness mode
    if args.check:
        stale = []
        missing = []
        current = []
        for name in agent_names:
            compiled_path = OUTPUT_DIR / f"{name}.md"
            compiled_hash = read_compiled_hash(compiled_path)
            if compiled_hash is None:
                missing.append(name)
            elif compiled_hash != source_hash:
                stale.append(name)
            else:
                current.append(name)

        print(f"Source hash: {source_hash}")
        print(f"Current: {len(current)} | Stale: {len(stale)} | Missing: {len(missing)}")
        if stale:
            print(f"\nStale agents: {', '.join(stale[:10])}")
        if missing:
            print(f"\nMissing agents: {', '.join(missing[:10])}")
        sys.exit(1 if stale or missing else 0)

    # Compile
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    compiled_count = 0
    skipped_count = 0

    for name in agent_names:
        content = compile_agent(name, engrams, source_hash)
        if content:
            output_path = OUTPUT_DIR / f"{name}.md"
            output_path.write_text(content)
            compiled_count += 1
            if args.verbose:
                selected = select_for_agent(engrams, name)
                print(f"  {name}: {len(selected)} engrams")
        else:
            skipped_count += 1

    print(f"Compiled {compiled_count} agent engram files (skipped {skipped_count} with no matches)")
    print(f"Output: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
