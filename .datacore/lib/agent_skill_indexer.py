#!/usr/bin/env python3
"""
Agent Skill Indexer for DIP-0016

Reads agents.yaml, extracts skill arrays per agent, and builds a flat
inverted index: {skill_keyword: [agent_names]}. Supports both --rebuild
to regenerate the index and --query to find agents by skill keyword
(substring/fuzzy match).

Output: .datacore/state/agent_skill_index.yaml

Usage:
    python agent_skill_indexer.py                # Build index
    python agent_skill_indexer.py --rebuild      # Force rebuild
    python agent_skill_indexer.py --query "gtd"  # Find agents with matching skills
    python agent_skill_indexer.py --query "content" --verbose
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import yaml

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
REGISTRY_PATH = DATACORE_ROOT / ".datacore" / "registry" / "agents.yaml"
INDEX_PATH = DATACORE_ROOT / ".datacore" / "state" / "agent_skill_index.yaml"


def load_registry() -> Dict:
    """Load the agent registry YAML."""
    if not REGISTRY_PATH.exists():
        print(f"Registry not found: {REGISTRY_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(REGISTRY_PATH, "r") as f:
        return yaml.safe_load(f) or {}


def extract_skills(registry: Dict) -> Dict[str, List[str]]:
    """
    Extract skills from all agents (core + module).

    Returns:
        Dict mapping agent_id -> list of skill keywords
    """
    agent_skills: Dict[str, List[str]] = {}

    # Core agents
    for agent_id, agent_data in registry.get("agents", {}).items():
        if not isinstance(agent_data, dict):
            continue
        skills = agent_data.get("skills", [])
        if isinstance(skills, list) and skills:
            agent_skills[agent_id] = skills

    # Module agents
    for agent_id, agent_data in registry.get("module_agents", {}).items():
        if not isinstance(agent_data, dict):
            continue
        skills = agent_data.get("skills", [])
        if isinstance(skills, list) and skills:
            agent_skills[agent_id] = skills

    return agent_skills


def build_inverted_index(agent_skills: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """
    Build inverted index: skill_keyword -> [agent_ids].

    Args:
        agent_skills: Dict mapping agent_id -> list of skill keywords

    Returns:
        Dict mapping skill_keyword -> sorted list of agent_ids
    """
    index: Dict[str, List[str]] = {}

    for agent_id, skills in agent_skills.items():
        for skill in skills:
            skill_key = skill.strip().lower()
            if not skill_key:
                continue
            if skill_key not in index:
                index[skill_key] = []
            if agent_id not in index[skill_key]:
                index[skill_key].append(agent_id)

    # Sort agent lists for deterministic output
    for skill_key in index:
        index[skill_key].sort()

    return dict(sorted(index.items()))


def write_index(inverted_index: Dict[str, List[str]], agent_skills: Dict[str, List[str]]) -> None:
    """Write the skill index to state file."""
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "version": "1.0.0",
        "generated": datetime.now(timezone.utc).isoformat(),
        "source": str(REGISTRY_PATH),
        "stats": {
            "total_agents": len(agent_skills),
            "total_skills": len(inverted_index),
            "total_mappings": sum(len(v) for v in inverted_index.values()),
        },
        "skill_to_agents": inverted_index,
        "agent_to_skills": {k: sorted(v) for k, v in sorted(agent_skills.items())},
    }

    with open(INDEX_PATH, "w") as f:
        yaml.dump(output, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"Index written to {INDEX_PATH}")
    print(f"  {output['stats']['total_agents']} agents, "
          f"{output['stats']['total_skills']} unique skills, "
          f"{output['stats']['total_mappings']} mappings")


def query_index(query: str, verbose: bool = False) -> List[Dict]:
    """
    Query the skill index with substring matching.

    Args:
        query: Search term (case-insensitive substring match)
        verbose: Show full skill lists for matched agents

    Returns:
        List of match dicts with agent_id and matched_skills
    """
    # Load index if it exists, otherwise build on the fly
    if INDEX_PATH.exists():
        with open(INDEX_PATH, "r") as f:
            data = yaml.safe_load(f) or {}
        inverted = data.get("skill_to_agents", {})
        agent_to_skills = data.get("agent_to_skills", {})
    else:
        print("Index not found, building...", file=sys.stderr)
        registry = load_registry()
        agent_skills = extract_skills(registry)
        inverted = build_inverted_index(agent_skills)
        agent_to_skills = {k: sorted(v) for k, v in agent_skills.items()}

    query_lower = query.strip().lower()
    matched_agents: Dict[str, List[str]] = {}

    for skill_key, agent_ids in inverted.items():
        if query_lower in skill_key:
            for agent_id in agent_ids:
                if agent_id not in matched_agents:
                    matched_agents[agent_id] = []
                matched_agents[agent_id].append(skill_key)

    results = []
    for agent_id in sorted(matched_agents.keys()):
        entry = {
            "agent": agent_id,
            "matched_skills": sorted(matched_agents[agent_id]),
        }
        if verbose:
            entry["all_skills"] = agent_to_skills.get(agent_id, [])
        results.append(entry)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Agent Skill Indexer — build and query the skill-to-agent index"
    )
    parser.add_argument("--rebuild", action="store_true", help="Force rebuild the index")
    parser.add_argument("--query", "-q", type=str, help="Query for agents with matching skills")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show full skill lists")
    args = parser.parse_args()

    if args.query:
        results = query_index(args.query, verbose=args.verbose)
        if not results:
            print(f"No agents found matching '{args.query}'")
            sys.exit(0)

        print(f"Agents matching '{args.query}':")
        print()
        for r in results:
            print(f"  {r['agent']}")
            print(f"    matched: {', '.join(r['matched_skills'])}")
            if args.verbose and "all_skills" in r:
                print(f"    all:     {', '.join(r['all_skills'])}")
        print()
        print(f"  {len(results)} agent(s) found")
    else:
        # Build / rebuild
        registry = load_registry()
        agent_skills = extract_skills(registry)

        if not agent_skills:
            print("No agents with skills found in registry", file=sys.stderr)
            sys.exit(1)

        inverted = build_inverted_index(agent_skills)
        write_index(inverted, agent_skills)


if __name__ == "__main__":
    main()
