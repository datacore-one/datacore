#!/usr/bin/env python3
"""
Engram Selector - Three-stage selection for runtime injection.

Usage:
  engram_selector.py --scope <scope> --task <task_desc> [--limit N]
  engram_selector.py --review
  engram_selector.py --decay

Stages:
  1. Relevance filter: scope matching (eliminates ~80%)
  2. Activation ranking: by retrieval_strength
  3. Diversity penalty: suppress near-duplicate statements

Per DIP-0019: Learning Architecture - The Engram Model
"""

import argparse
import glob
import math
import os
import sys
from datetime import datetime, date

try:
    import yaml
except ImportError:
    # Fallback: try to find yaml
    print("PyYAML not installed. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


DATACORE_ROOT = os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data"))
DECAY_RATE = 0.05  # per day


def find_engram_files():
    """Find all engrams.yaml files across spaces."""
    patterns = [
        os.path.join(DATACORE_ROOT, ".datacore", "learning", "engrams.yaml"),
        os.path.join(DATACORE_ROOT, "[0-9]-*", ".datacore", "learning", "engrams.yaml"),
    ]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    return files


def load_all_engrams():
    """Load engrams from all spaces."""
    engrams = []
    for filepath in find_engram_files():
        try:
            with open(filepath, "r") as f:
                data = yaml.safe_load(f) or []
                if isinstance(data, dict):
                    data = data.get("engrams", [])
                for eng in data:
                    eng["_source_file"] = filepath
                engrams.extend(data)
        except (yaml.YAMLError, OSError) as e:
            print(f"Warning: Could not load {filepath}: {e}", file=sys.stderr)
    return engrams


def apply_decay(engrams):
    """Apply time-based decay to retrieval_strength."""
    today = date.today()
    for eng in engrams:
        if eng.get("status") not in ("active", "candidate"):
            continue
        activation = eng.get("activation", {})
        last_accessed = activation.get("last_accessed")
        if last_accessed:
            if isinstance(last_accessed, str):
                try:
                    last_date = datetime.strptime(last_accessed, "%Y-%m-%d").date()
                except ValueError:
                    continue
            elif isinstance(last_accessed, date):
                last_date = last_accessed
            else:
                continue
            days_since = (today - last_date).days
            if days_since > 0:
                rs = activation.get("retrieval_strength", 0.5)
                # Exponential decay
                new_rs = rs * math.exp(-DECAY_RATE * days_since)
                activation["retrieval_strength"] = round(new_rs, 4)
    return engrams


def scope_matches(engram_scope, target_scope):
    """Check if engram scope matches the target context."""
    if not engram_scope or engram_scope == "global":
        return True
    if not target_scope:
        return engram_scope == "global"
    # Exact match
    if engram_scope == target_scope:
        return True
    # Prefix match: agent:dip-preparer matches agent:dip-preparer
    if target_scope.startswith(engram_scope):
        return True
    # Space match
    if engram_scope.startswith("space:"):
        return True
    return False


def keyword_overlap(statement, task_desc):
    """Simple keyword overlap score between engram statement and task."""
    if not task_desc:
        return 0.5  # neutral if no task context
    s_words = set(statement.lower().split())
    t_words = set(task_desc.lower().split())
    # Remove common words
    stopwords = {"the", "a", "an", "is", "are", "to", "for", "of", "in", "on", "and", "or", "with", "that", "this"}
    s_words -= stopwords
    t_words -= stopwords
    if not s_words or not t_words:
        return 0.3
    overlap = len(s_words & t_words)
    return min(1.0, overlap / max(len(s_words), 1) * 2)


def diversity_filter(ranked_engrams, max_count):
    """Remove near-duplicate engrams from the ranked list."""
    selected = []
    seen_keywords = set()
    for eng in ranked_engrams:
        words = set(eng.get("statement", "").lower().split()[:5])
        # If >60% overlap with already selected, skip
        if seen_keywords:
            overlap = len(words & seen_keywords) / max(len(words), 1)
            if overlap > 0.6:
                continue
        selected.append(eng)
        seen_keywords.update(words)
        if len(selected) >= max_count:
            break
    return selected


def select_engrams(scope, task_desc, limit=15):
    """Three-stage engram selection."""
    engrams = load_all_engrams()
    engrams = apply_decay(engrams)

    # Stage 1: Relevance filter
    relevant = []
    for eng in engrams:
        if eng.get("status") not in ("active",):
            continue
        rs = eng.get("activation", {}).get("retrieval_strength", 0)
        if rs < 0.1:
            continue
        eng_scope = eng.get("scope", "global")
        if scope_matches(eng_scope, scope):
            # Boost with keyword overlap
            kw_score = keyword_overlap(eng.get("statement", ""), task_desc)
            eng["_relevance"] = kw_score
            relevant.append(eng)
        # Also check abstract engrams
        abstract = eng.get("abstract")
        if abstract and isinstance(abstract, dict):
            applies_when = abstract.get("applies_when", "")
            if applies_when and task_desc:
                kw_score = keyword_overlap(applies_when, task_desc)
                if kw_score > 0.3:
                    eng["_relevance"] = kw_score
                    if eng not in relevant:
                        relevant.append(eng)

    # Stage 2: Activation ranking
    def sort_key(eng):
        rs = eng.get("activation", {}).get("retrieval_strength", 0)
        relevance = eng.get("_relevance", 0.5)
        return rs * 0.6 + relevance * 0.4

    relevant.sort(key=sort_key, reverse=True)

    # Stage 3: Diversity filter
    selected = diversity_filter(relevant, limit)
    return selected


def format_injection(engrams, limit=15):
    """Format engrams for injection into agent context."""
    directives = [e for e in engrams if e.get("activation", {}).get("retrieval_strength", 0) > 0.5]
    also_consider = [e for e in engrams if e.get("activation", {}).get("retrieval_strength", 0) <= 0.5]

    directives = directives[:10]
    also_consider = also_consider[:5]

    lines = []
    if directives:
        lines.append("## Directives")
        lines.append("")
        for i, eng in enumerate(directives, 1):
            rs = eng.get("activation", {}).get("retrieval_strength", 0)
            lines.append(f"{i}. [{eng.get('type', 'behavioral')}] {eng.get('statement', '')} (strength: {rs:.2f})")
        lines.append("")

    if also_consider:
        lines.append("## Also Consider")
        lines.append("")
        for i, eng in enumerate(also_consider, 1):
            rs = eng.get("activation", {}).get("retrieval_strength", 0)
            lines.append(f"{i}. [{eng.get('type', 'behavioral')}] {eng.get('statement', '')} (strength: {rs:.2f})")
        lines.append("")

    if not directives and not also_consider:
        lines.append("No relevant engrams for current context.")

    return "\n".join(lines)


def format_review():
    """Format engrams for daily review."""
    engrams = load_all_engrams()
    engrams = apply_decay(engrams)

    candidates = [e for e in engrams if e.get("status") == "candidate"]
    fading = [e for e in engrams if e.get("status") == "active"
              and 0.1 <= e.get("activation", {}).get("retrieval_strength", 0) <= 0.3]

    lines = []
    if candidates:
        lines.append(f"## NEW Candidates ({len(candidates)})")
        lines.append("")
        for i, eng in enumerate(candidates[:10], 1):
            lines.append(f"{i}. [{eng.get('type', 'behavioral')}] \"{eng.get('statement', '')}\"")
            lines.append(f"   Scope: {eng.get('scope', 'global')} | Sources: {eng.get('source_patterns', [])}")
            lines.append(f"   ID: {eng.get('id', 'unknown')}")
            lines.append("")

    if fading:
        lines.append(f"## FADING ({len(fading)})")
        lines.append("")
        for eng in fading[:5]:
            rs = eng.get("activation", {}).get("retrieval_strength", 0)
            lines.append(f"- {eng.get('id', '?')}: \"{eng.get('statement', '')}\" (strength: {rs:.2f})")
        lines.append("")

    if not candidates and not fading:
        lines.append("No items pending review.")

    return "\n".join(lines)


def run_decay():
    """Apply decay to all engram files and save."""
    for filepath in find_engram_files():
        try:
            with open(filepath, "r") as f:
                data = yaml.safe_load(f) or []
            if isinstance(data, dict):
                engrams_list = data.get("engrams", [])
            else:
                engrams_list = data
            apply_decay(engrams_list)
            with open(filepath, "w") as f:
                if isinstance(data, dict):
                    data["engrams"] = engrams_list
                    yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
                else:
                    yaml.dump(engrams_list, f, default_flow_style=False, allow_unicode=True)
            print(f"Decay applied to {filepath}")
        except (yaml.YAMLError, OSError) as e:
            print(f"Error processing {filepath}: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Engram Selector (DIP-0019)")
    parser.add_argument("--scope", default="global", help="Target scope (e.g., agent:dip-preparer)")
    parser.add_argument("--task", default="", help="Task description for relevance matching")
    parser.add_argument("--limit", type=int, default=15, help="Max engrams to return")
    parser.add_argument("--review", action="store_true", help="Output review format")
    parser.add_argument("--decay", action="store_true", help="Apply decay and save")
    args = parser.parse_args()

    if args.review:
        print(format_review())
    elif args.decay:
        run_decay()
    else:
        selected = select_engrams(args.scope, args.task, args.limit)
        print(format_injection(selected, args.limit))


if __name__ == "__main__":
    main()
