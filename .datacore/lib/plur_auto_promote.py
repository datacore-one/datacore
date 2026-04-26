#!/usr/bin/env python3
"""PLUR Auto-Promote: Cross-Space Engram Promotion

Scans observation logs and existing engrams to find patterns that appear
across multiple Datacore spaces. When the same pattern is observed in 2+
spaces, promotes it to global scope (or creates a new global engram).

This bridges observation data → engram promotion, feeding the meta-engram
pipeline. A meta-engram aggregates multiple promoted patterns into
structural knowledge; auto-promote generates the inputs.

Usage:
    python3 plur_auto_promote.py [--days 14] [--min-spaces 2] [--dry-run]

Pipeline:
    observations (plur_observe.py)
      → analyzer (plur_observation_analyzer.py)
        → auto-promote (this script)
          → meta-engrams (future: cluster promoted engrams)
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

OBS_DIR = Path(os.path.expanduser("~/.plur/observations"))
ENGRAMS_FILE = Path(os.path.expanduser("~/.plur/engrams.yaml"))

# Datacore spaces match [0-9]-* pattern (discovered, not hardcoded)
SPACE_PATTERN = re.compile(r"^[0-9]-[a-zA-Z0-9_-]+$")


def load_observations(days=14):
    """Load observations from the last N days."""
    observations = []
    cutoff = datetime.now() - timedelta(days=days)

    if not OBS_DIR.exists():
        return observations

    for f in sorted(OBS_DIR.glob("*.jsonl")):
        try:
            date = datetime.strptime(f.stem, "%Y-%m-%d")
            if date < cutoff:
                continue
        except ValueError:
            continue

        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        observations.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    return observations


def extract_space(cwd):
    """Extract Datacore space from working directory."""
    if "/Data/" not in cwd:
        return None
    after_data = cwd.split("/Data/")[1]
    first_dir = after_data.split("/")[0]
    if SPACE_PATTERN.match(first_dir):
        return first_dir
    return None


def find_cross_space_error_patterns(observations, min_spaces=2):
    """Find error patterns that occur in multiple spaces."""
    # Group errors by tool+error_prefix across spaces
    error_by_space = defaultdict(lambda: defaultdict(int))

    for obs in observations:
        if obs.get("success") is False or obs.get("error"):
            space = extract_space(obs.get("cwd", ""))
            if not space:
                continue
            tool = obs.get("tool", "unknown")
            error = str(obs.get("error", ""))[:100]  # Normalize by prefix
            key = f"{tool}:{error[:50]}"
            error_by_space[key][space] += 1

    # Filter to patterns in min_spaces+ spaces
    return {
        key: dict(spaces)
        for key, spaces in error_by_space.items()
        if len(spaces) >= min_spaces
    }


def find_cross_space_tool_sequences(observations, min_spaces=2):
    """Find tool sequences that repeat across multiple spaces."""
    # Group by session, track space per session
    sessions = defaultdict(lambda: {"tools": [], "space": None})
    for obs in observations:
        if obs.get("event") != "PreToolUse":
            continue
        sid = obs.get("session_id", "")
        sessions[sid]["tools"].append(obs.get("tool", ""))
        space = extract_space(obs.get("cwd", ""))
        if space:
            sessions[sid]["space"] = space

    # Extract 3-tool sequences per space
    seq_by_space = defaultdict(lambda: defaultdict(int))
    for session in sessions.values():
        space = session["space"]
        if not space:
            continue
        tools = session["tools"]
        for i in range(len(tools) - 2):
            seq = " → ".join(tools[i:i+3])
            seq_by_space[seq][space] += 1

    # Filter to sequences in min_spaces+ spaces, each with 2+ occurrences per space
    return {
        seq: dict(spaces)
        for seq, spaces in seq_by_space.items()
        if len(spaces) >= min_spaces
        and all(count >= 2 for count in spaces.values())
    }


def find_cross_space_tool_preferences(observations, min_spaces=3):
    """Find tools heavily used across many spaces → universal workflow patterns."""
    tool_spaces = defaultdict(lambda: defaultdict(int))

    for obs in observations:
        space = extract_space(obs.get("cwd", ""))
        if not space:
            continue
        tool = obs.get("tool", "")
        if tool:
            tool_spaces[tool][space] += 1

    # Tools used in 3+ spaces with 5+ uses each
    return {
        tool: dict(spaces)
        for tool, spaces in tool_spaces.items()
        if len(spaces) >= min_spaces
        and all(count >= 5 for count in spaces.values())
    }


def generate_promotion_candidates(observations, min_spaces=2):
    """Generate engram candidates from cross-space patterns."""
    candidates = []

    # Cross-space error patterns
    errors = find_cross_space_error_patterns(observations, min_spaces)
    for key, spaces in errors.items():
        tool, error = key.split(":", 1)
        total = sum(spaces.values())
        candidates.append({
            "type": "behavioral",
            "scope": "global",
            "domain": "tool-usage",
            "statement": f"{tool} commonly fails across spaces: {error}",
            "rationale": f"Observed in {len(spaces)} spaces ({', '.join(spaces.keys())}), {total} total failures. Cross-space pattern → global engram.",
            "tags": ["auto-promoted", "cross-space", "error-pattern", tool.lower()],
            "confidence": min(0.5 + (len(spaces) * 0.15), 0.9),
            "spaces": list(spaces.keys()),
        })

    # Cross-space sequences
    sequences = find_cross_space_tool_sequences(observations, min_spaces)
    for seq, spaces in list(sorted(sequences.items(), key=lambda x: -sum(x[1].values())))[:5]:
        total = sum(spaces.values())
        candidates.append({
            "type": "procedural",
            "scope": "global",
            "domain": "workflow",
            "statement": f"Common cross-space workflow: {seq}",
            "rationale": f"Sequence appears in {len(spaces)} spaces ({', '.join(spaces.keys())}), {total} total occurrences. Promoting to global.",
            "tags": ["auto-promoted", "cross-space", "workflow-pattern"],
            "confidence": min(0.4 + (len(spaces) * 0.1) + (total * 0.02), 0.85),
            "spaces": list(spaces.keys()),
        })

    return candidates


def create_engram_via_mcp(candidate):
    """Create an engram via plur_learn MCP tool (through CLI)."""
    # Use plur CLI to create engram
    cmd = [
        "npx", "@plur-ai/cli", "learn",
        "--statement", candidate["statement"],
        "--type", candidate["type"],
        "--scope", candidate.get("scope", "global"),
        "--domain", candidate.get("domain", ""),
        "--source", "auto-promote",
    ]
    for tag in candidate.get("tags", []):
        cmd.extend(["--tag", tag])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0, result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="Auto-promote cross-space patterns to global engrams")
    parser.add_argument("--days", type=int, default=14, help="Days of history to analyze")
    parser.add_argument("--min-spaces", type=int, default=2, help="Min spaces for promotion")
    parser.add_argument("--dry-run", action="store_true", help="Show candidates without creating engrams")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    observations = load_observations(args.days)
    if not observations:
        print(f"No observations in {OBS_DIR} for last {args.days} days.")
        print("Run some sessions with plur_observe hook active first.")
        sys.exit(0)

    # Count observations per space
    space_counts = Counter()
    for obs in observations:
        space = extract_space(obs.get("cwd", ""))
        if space:
            space_counts[space] += 1

    print(f"Loaded {len(observations)} observations from last {args.days} days")
    print(f"Spaces covered: {dict(space_counts)}\n")

    if len(space_counts) < args.min_spaces:
        print(f"Need observations from {args.min_spaces}+ spaces, only have {len(space_counts)}.")
        print("Work in more spaces to generate cross-space patterns.")
        sys.exit(0)

    candidates = generate_promotion_candidates(observations, args.min_spaces)

    if not candidates:
        print("No cross-space patterns found for promotion.")
        print("This is normal early on — patterns emerge after consistent use across spaces.")
        sys.exit(0)

    print(f"## Promotion Candidates ({len(candidates)})\n")
    for i, c in enumerate(candidates, 1):
        print(f"  [{i}] [{c['type']}] {c['statement']}")
        print(f"      Spaces: {', '.join(c['spaces'])} | Confidence: {c['confidence']:.2f}")
        print(f"      {c['rationale']}")
        print()

    if args.json:
        print(json.dumps(candidates, indent=2))

    if args.dry_run:
        print("(dry-run mode — no engrams created)")
        return

    # Create engrams
    print("## Creating engrams...\n")
    created = 0
    for c in candidates:
        ok, msg = create_engram_via_mcp(c)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {c['statement'][:80]}")
        if ok:
            created += 1
        elif msg:
            print(f"         {msg}")

    print(f"\nCreated {created}/{len(candidates)} engrams.")


if __name__ == "__main__":
    main()
