#!/usr/bin/env python3
"""
Dedup - Content hash and title similarity for deterministic deduplication.

Used by research-orchestrator to replace prompt-driven dedup with
deterministic matching. Identifies duplicates via SHA256 content hashes
and Jaccard word-token similarity on titles.

Usage:
    python dedup.py --file items.json --field title --threshold 0.8
    python dedup.py --file items.json --content-field content --dry-run
    python dedup.py --file items.json --field title --threshold 0.6 --output deduped.json
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


def content_hash(text: str) -> str:
    """SHA256 hash of normalized text.

    Normalization: lowercase, collapse whitespace, strip leading/trailing.
    """
    normalized = re.sub(r"\s+", " ", text.lower().strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _tokenize(text: str) -> set:
    """Extract lowercase word tokens from text."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def title_similarity(a: str, b: str) -> float:
    """Jaccard similarity on word tokens of two strings.

    Returns 0.0 for no overlap, 1.0 for identical token sets.
    Returns 1.0 if both strings are empty.
    """
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def find_duplicates(
    items: List[Dict],
    key_field: str = "title",
    content_field: str = "content",
    threshold: float = 0.8,
) -> List[Tuple[int, int, str]]:
    """Find duplicate pairs among items.

    Checks two signals:
    1. Exact content hash match (if content_field present)
    2. Title similarity above threshold (if key_field present)

    Returns list of (index_a, index_b, reason) tuples.
    """
    duplicates = []
    n = len(items)

    # Build content hash index for O(n) exact-match detection
    hash_index: Dict[str, List[int]] = {}
    for i, item in enumerate(items):
        if content_field and content_field in item:
            h = content_hash(item[content_field])
            hash_index.setdefault(h, []).append(i)

    # Report exact content duplicates
    for h, indices in hash_index.items():
        if len(indices) > 1:
            for j in range(1, len(indices)):
                duplicates.append((indices[0], indices[j], "exact_content"))

    # Track already-paired indices to avoid double-reporting
    paired = {(a, b) for a, b, _ in duplicates}

    # Title similarity: O(n^2) but typically n is small (research items)
    for i in range(n):
        title_i = items[i].get(key_field, "")
        if not title_i:
            continue
        for j in range(i + 1, n):
            if (i, j) in paired:
                continue
            title_j = items[j].get(key_field, "")
            if not title_j:
                continue
            sim = title_similarity(title_i, title_j)
            if sim >= threshold:
                duplicates.append((i, j, f"title_sim={sim:.3f}"))

    return duplicates


def deduplicate(
    items: List[Dict],
    key_field: str = "title",
    content_field: str = "content",
    threshold: float = 0.8,
) -> List[Dict]:
    """Return deduplicated list, keeping the first occurrence.

    Uses find_duplicates internally. Items identified as duplicates
    of an earlier item are removed.
    """
    dupes = find_duplicates(items, key_field, content_field, threshold)
    # Collect indices to drop (always drop the later index)
    drop = set()
    for _, idx_b, _ in dupes:
        drop.add(idx_b)
    return [item for i, item in enumerate(items) if i not in drop]


def main():
    parser = argparse.ArgumentParser(
        description="Deterministic deduplication via content hash and title similarity"
    )
    parser.add_argument("--file", required=True, help="JSON file with list of items")
    parser.add_argument("--field", default="title", help="Key field for similarity (default: title)")
    parser.add_argument("--content-field", default="content", help="Content field for hashing (default: content)")
    parser.add_argument("--threshold", type=float, default=0.8, help="Similarity threshold 0-1 (default: 0.8)")
    parser.add_argument("--output", help="Output file for deduplicated JSON (default: stdout)")
    parser.add_argument("--dry-run", action="store_true", help="Show duplicates without removing")

    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    with open(file_path) as f:
        items = json.load(f)

    if not isinstance(items, list):
        print("Error: JSON root must be an array of objects", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        dupes = find_duplicates(items, args.field, args.content_field, args.threshold)
        if not dupes:
            print("No duplicates found")
        else:
            print(f"Found {len(dupes)} duplicate pair(s):")
            for idx_a, idx_b, reason in dupes:
                title_a = items[idx_a].get(args.field, "(no title)")
                title_b = items[idx_b].get(args.field, "(no title)")
                print(f"  [{idx_a}] {title_a!r}")
                print(f"  [{idx_b}] {title_b!r}")
                print(f"    reason: {reason}")
                print()
        sys.exit(0)

    result = deduplicate(items, args.field, args.content_field, args.threshold)
    removed = len(items) - len(result)

    output_json = json.dumps(result, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
            f.write("\n")
        print(f"Deduplicated: {len(items)} -> {len(result)} items ({removed} removed)")
        print(f"Written to: {args.output}")
    else:
        print(output_json)
        print(f"\n# Deduplicated: {len(items)} -> {len(result)} items ({removed} removed)", file=sys.stderr)


if __name__ == "__main__":
    main()
