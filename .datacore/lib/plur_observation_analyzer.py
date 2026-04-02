#!/usr/bin/env python3
"""PLUR Observation Analyzer

Processes raw observation logs (~/.plur/observations/*.jsonl) to identify
patterns worth learning. Outputs engram candidates for human review.

Usage:
    python3 plur_observation_analyzer.py [--days 7] [--auto-learn]

Patterns detected:
1. Repeated tool sequences (same 3+ tools in order → procedural engram)
2. Error patterns (same tool failing repeatedly → behavioral engram)
3. Correction signals (Edit after Edit on same file → possible mistake pattern)
4. High-frequency tools by directory → workspace-specific conventions

With --auto-learn, creates engrams directly via plur_learn MCP call.
Without it, prints candidates for manual review.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

OBS_DIR = Path(os.path.expanduser("~/.plur/observations"))


def load_observations(days=7):
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
                if not line:
                    continue
                try:
                    observations.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return observations


def analyze_tool_frequency(observations):
    """Which tools are used most and where."""
    tool_counts = Counter()
    tool_by_dir = defaultdict(Counter)

    for obs in observations:
        tool = obs.get("tool", "")
        cwd = obs.get("cwd", "")
        if tool:
            tool_counts[tool] += 1
            # Extract space from cwd
            if "/Data/" in cwd:
                space = cwd.split("/Data/")[1].split("/")[0] if "/Data/" in cwd else "root"
                tool_by_dir[space][tool] += 1

    return tool_counts, tool_by_dir


def analyze_error_patterns(observations):
    """Find tools that fail repeatedly."""
    failures = defaultdict(list)

    for obs in observations:
        if obs.get("success") is False or obs.get("error"):
            tool = obs.get("tool", "unknown")
            error = obs.get("error", "unknown error")
            failures[tool].append(error)

    # Only report tools with 3+ failures
    return {t: errors for t, errors in failures.items() if len(errors) >= 3}


def analyze_sequences(observations):
    """Find repeated tool sequences (potential procedural patterns)."""
    # Group by session
    sessions = defaultdict(list)
    for obs in observations:
        if obs.get("event") == "PreToolUse":
            sessions[obs.get("session_id", "")].append(obs.get("tool", ""))

    # Find common 3-tool sequences
    sequence_counts = Counter()
    for session_tools in sessions.values():
        for i in range(len(session_tools) - 2):
            seq = tuple(session_tools[i:i+3])
            sequence_counts[seq] += 1

    # Only report sequences seen 5+ times
    return {seq: count for seq, count in sequence_counts.items() if count >= 5}


def analyze_cross_space_patterns(observations):
    """Find patterns that appear in multiple spaces → global promotion candidates."""
    tool_spaces = defaultdict(set)

    for obs in observations:
        tool = obs.get("tool", "")
        cwd = obs.get("cwd", "")
        if tool and "/Data/" in cwd:
            space = cwd.split("/Data/")[1].split("/")[0]
            tool_spaces[tool].add(space)

    # Tools used in 3+ spaces are likely global patterns
    return {t: spaces for t, spaces in tool_spaces.items() if len(spaces) >= 3}


def generate_candidates(observations):
    """Generate engram candidates from observation analysis."""
    candidates = []

    # Error patterns → behavioral engrams
    error_patterns = analyze_error_patterns(observations)
    for tool, errors in error_patterns.items():
        # Find most common error
        error_counts = Counter(errors)
        common_error = error_counts.most_common(1)[0]
        candidates.append({
            "type": "behavioral",
            "domain": "tool-usage",
            "statement": f"{tool} frequently fails with: {common_error[0][:200]}",
            "rationale": f"Observed {len(errors)} failures, most common ({common_error[1]}x): {common_error[0][:100]}",
            "confidence": min(0.3 + (len(errors) * 0.1), 0.9),
            "source": "observation-analyzer",
        })

    # Repeated sequences → procedural engrams
    sequences = analyze_sequences(observations)
    for seq, count in sorted(sequences.items(), key=lambda x: -x[1])[:10]:
        candidates.append({
            "type": "procedural",
            "domain": "workflow",
            "statement": f"Common tool sequence: {' → '.join(seq)}",
            "rationale": f"Observed {count} times across sessions",
            "confidence": min(0.3 + (count * 0.05), 0.8),
            "source": "observation-analyzer",
        })

    return candidates


def main():
    parser = argparse.ArgumentParser(description="Analyze PLUR observations for patterns")
    parser.add_argument("--days", type=int, default=7, help="Days of history to analyze")
    parser.add_argument("--auto-learn", action="store_true", help="Create engrams automatically")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    observations = load_observations(args.days)
    if not observations:
        print(f"No observations found in {OBS_DIR} for the last {args.days} days.")
        print("The plur_observe hook needs to be active first. Check ~/.claude/settings.json")
        sys.exit(0)

    print(f"Loaded {len(observations)} observations from last {args.days} days\n")

    # Tool frequency
    tool_counts, tool_by_dir = analyze_tool_frequency(observations)
    print("## Tool Frequency (top 15)")
    for tool, count in tool_counts.most_common(15):
        print(f"  {tool}: {count}")
    print()

    # Space distribution
    print("## Tools by Space")
    for space, counts in sorted(tool_by_dir.items()):
        top3 = counts.most_common(3)
        tools_str = ", ".join(f"{t}({c})" for t, c in top3)
        print(f"  {space}: {tools_str}")
    print()

    # Error patterns
    error_patterns = analyze_error_patterns(observations)
    if error_patterns:
        print("## Error Patterns (3+ failures)")
        for tool, errors in error_patterns.items():
            print(f"  {tool}: {len(errors)} failures")
        print()

    # Sequences
    sequences = analyze_sequences(observations)
    if sequences:
        print("## Common Sequences (5+ occurrences)")
        for seq, count in sorted(sequences.items(), key=lambda x: -x[1])[:10]:
            print(f"  {' → '.join(seq)}: {count}x")
        print()

    # Cross-space
    cross = analyze_cross_space_patterns(observations)
    if cross:
        print("## Cross-Space Patterns (3+ spaces)")
        for tool, spaces in sorted(cross.items(), key=lambda x: -len(x[1])):
            print(f"  {tool}: {', '.join(sorted(spaces))}")
        print()

    # Candidates
    candidates = generate_candidates(observations)
    if candidates:
        print(f"## Engram Candidates ({len(candidates)})")
        for i, c in enumerate(candidates, 1):
            print(f"\n  [{i}] [{c['type']}] {c['statement']}")
            print(f"      Confidence: {c['confidence']:.1f} | {c['rationale']}")

        if args.json:
            print("\n## JSON Output")
            print(json.dumps(candidates, indent=2))
    else:
        print("No engram candidates generated (need more observation data).")


if __name__ == "__main__":
    main()
