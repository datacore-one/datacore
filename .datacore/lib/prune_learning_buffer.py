#!/usr/bin/env python3
"""Prune learning buffer entries that have been promoted to PLUR engrams.

Scans patterns.md, corrections.md, preferences.md across all spaces.
Removes entries older than --days that have a matching engram in PLUR.
Preserves frontmatter and file headers above the first ## entry.

Usage:
    python3 prune_learning_buffer.py --dry-run
    python3 prune_learning_buffer.py --days 90
    python3 prune_learning_buffer.py --days 60 --verbose
"""

import argparse
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spaces import discover_spaces  # noqa: E402

# Base directory — script lives in .datacore/lib/, root is two levels up
ROOT = Path(__file__).resolve().parent.parent.parent

# Default engrams file
ENGRAMS_FILE = Path.home() / ".plur" / "engrams.yaml"

# Files to scan
TARGET_FILES = ("patterns.md", "corrections.md", "preferences.md")

# Entry heading pattern: ## YYYY-MM-DD with optional colon/title after
ENTRY_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})")

# Patterns to extract the core statement from an entry body
STATEMENT_PATTERNS = [
    re.compile(r"\*\*Rule\*\*:\s*(.+)", re.IGNORECASE),
    re.compile(r"\*\*Correction\*\*:\s*(.+)", re.IGNORECASE),
    re.compile(r"\*\*Pattern\*\*:\s*(.+)", re.IGNORECASE),
    re.compile(r"\*\*Prevention\*\*:\s*(.+)", re.IGNORECASE),
]

# Minimum keyword overlap to consider a match
MIN_KEYWORD_OVERLAP = 3
# Minimum ratio of matching keywords to total keywords
MIN_KEYWORD_RATIO = 0.4


def find_learning_files():
    """Find all target learning files across root and spaces."""
    files = []

    # Root: .datacore/learning/
    for name in TARGET_FILES:
        p = ROOT / ".datacore" / "learning" / name
        if p.exists():
            files.append(p)

    # Spaces: [space]/.datacore/learning/
    for space in discover_spaces(ROOT):
        for name in TARGET_FILES:
            p = space.path / ".datacore" / "learning" / name
            if p.exists():
                files.append(p)

    return files


def parse_entries(content):
    """Parse a learning file into (header, entries).

    Returns:
        header: str -- everything before the first ## YYYY-MM-DD entry
        entries: list of dict with keys: date, date_str, heading, body, raw
    """
    lines = content.split("\n")
    header_lines = []
    entries = []
    current = None

    for line in lines:
        m = ENTRY_RE.match(line)
        if m:
            # Save previous entry
            if current is not None:
                current["raw"] = "\n".join(current["_lines"])
                current["body"] = current["raw"]
                del current["_lines"]
                entries.append(current)

            date_str = m.group(1)
            try:
                entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                entry_date = None

            current = {
                "date": entry_date,
                "date_str": date_str,
                "heading": line,
                "_lines": [line],
            }
        elif current is not None:
            current["_lines"].append(line)
        else:
            header_lines.append(line)

    # Don't forget the last entry
    if current is not None:
        current["raw"] = "\n".join(current["_lines"])
        current["body"] = current["raw"]
        del current["_lines"]
        entries.append(current)

    header = "\n".join(header_lines)
    return header, entries


def extract_statement(entry):
    """Extract the core statement from an entry for engram matching."""
    body = entry["body"]

    # Try known patterns
    for pattern in STATEMENT_PATTERNS:
        m = pattern.search(body)
        if m:
            statement = m.group(1).strip()
            if len(statement) > 200:
                statement = statement[:200]
            return statement

    # Fallback: use heading text after the date
    heading = entry["heading"]
    cleaned = re.sub(r"^##\s+\d{4}-\d{2}-\d{2}:?\s*", "", heading).strip()
    if cleaned:
        return cleaned

    return None


def _extract_keywords(text):
    """Extract significant keywords from text for matching."""
    # Lowercase, remove markdown formatting, split on non-alpha
    text = text.lower()
    text = re.sub(r"[*_`\[\](){}]", " ", text)
    words = re.split(r"[^a-z0-9]+", text)
    # Filter short/common words
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "out", "off", "over",
        "under", "again", "further", "then", "once", "here", "there", "when",
        "where", "why", "how", "all", "both", "each", "few", "more", "most",
        "other", "some", "such", "no", "nor", "not", "only", "own", "same",
        "so", "than", "too", "very", "just", "don", "now", "and", "but", "or",
        "if", "this", "that", "these", "those", "it", "its", "what", "which",
        "who", "whom", "use", "using", "used", "also", "any", "about",
    }
    return {w for w in words if len(w) >= 3 and w not in stopwords}


def load_engram_index(engrams_path=None):
    """Load engram statements from YAML via fast line-by-line parsing.

    Returns a list of (keywords_set, statement_text) tuples for matching.
    Avoids loading the full YAML (slow for 600K+ lines).
    """
    path = engrams_path or ENGRAMS_FILE
    if not path.exists():
        return None

    statements = []
    in_statement = False
    continuation_lines = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()

            if stripped.startswith("statement:"):
                val = stripped.split("statement:", 1)[1].strip()
                if val.startswith(">"):
                    # Multiline block scalar — collect continuation lines
                    in_statement = True
                    continuation_lines = []
                elif val.startswith('"') or val.startswith("'"):
                    # Quoted inline value
                    statements.append(val.strip("\"'"))
                    in_statement = False
                elif val:
                    statements.append(val)
                    in_statement = False
                else:
                    # Empty — next lines are continuation
                    in_statement = True
                    continuation_lines = []
            elif in_statement:
                # Continuation lines are indented (typically 6+ spaces)
                if line.startswith("      ") and stripped:
                    continuation_lines.append(stripped)
                else:
                    # End of multiline
                    if continuation_lines:
                        statements.append(" ".join(continuation_lines))
                    continuation_lines = []
                    in_statement = False

    # Handle trailing multiline
    if continuation_lines:
        statements.append(" ".join(continuation_lines))

    # Build keyword index
    index = []
    for stmt in statements:
        kw = _extract_keywords(stmt)
        if kw:
            index.append((kw, stmt))

    return index


def check_engram_exists(statement, engram_index):
    """Check if a matching engram exists using keyword overlap.

    Returns True if a sufficiently similar engram is found, False otherwise,
    None if index is unavailable.
    """
    if not statement:
        return None

    if engram_index is None:
        return None

    entry_kw = _extract_keywords(statement)
    if not entry_kw or len(entry_kw) < 2:
        return None

    best_overlap = 0
    best_ratio = 0.0

    for engram_kw, _stmt in engram_index:
        overlap = len(entry_kw & engram_kw)
        if overlap > best_overlap:
            # Ratio relative to the entry's keywords (not the engram's)
            ratio = overlap / len(entry_kw)
            if ratio > best_ratio:
                best_overlap = overlap
                best_ratio = ratio

    if best_overlap >= MIN_KEYWORD_OVERLAP and best_ratio >= MIN_KEYWORD_RATIO:
        return True

    return False


def prune_file(filepath, cutoff_date, engram_index, dry_run=False, verbose=False):
    """Process a single learning file. Returns stats dict."""
    stats = {
        "file": str(filepath), "total": 0, "eligible": 0,
        "pruned": 0, "kept": 0, "skipped": 0,
    }

    content = filepath.read_text(encoding="utf-8")
    header, entries = parse_entries(content)
    stats["total"] = len(entries)

    if not entries:
        return stats

    keep = []
    pruned_entries = []

    for entry in entries:
        # Skip entries without a parseable date
        if entry["date"] is None:
            keep.append(entry)
            stats["skipped"] += 1
            if verbose:
                print(f"  SKIP (no date): {entry['heading'][:80]}")
            continue

        # Skip entries newer than cutoff
        if entry["date"] > cutoff_date:
            keep.append(entry)
            stats["kept"] += 1
            if verbose:
                print(f"  KEEP (recent):  {entry['heading'][:80]}")
            continue

        stats["eligible"] += 1
        statement = extract_statement(entry)

        if verbose:
            print(f"  CHECK: {entry['heading'][:80]}")
            if statement:
                print(f"         query: {statement[:100]}")

        result = check_engram_exists(statement, engram_index)

        if result is True:
            pruned_entries.append(entry)
            stats["pruned"] += 1
            if verbose:
                print(f"         -> PRUNE (engram exists)")
        elif result is None:
            # Check failed — keep the entry to be safe
            keep.append(entry)
            stats["kept"] += 1
            if verbose:
                print(f"         -> KEEP (check failed)")
        else:
            # No engram found — keep
            keep.append(entry)
            stats["kept"] += 1
            if verbose:
                print(f"         -> KEEP (no engram)")

    if not dry_run and pruned_entries:
        # Rebuild file content
        parts = [header.rstrip("\n")]

        for entry in keep:
            raw = entry["raw"]
            # Strip trailing whitespace/separators from entry
            raw = raw.rstrip("\n").rstrip("-").rstrip("\n")
            parts.append("")  # blank line before entry
            parts.append(raw)
            parts.append("")
            parts.append("---")

        new_content = "\n".join(parts) + "\n"

        # Write atomically via temp file
        tmp = filepath.with_suffix(".md.tmp")
        tmp.write_text(new_content, encoding="utf-8")
        shutil.move(str(tmp), str(filepath))

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Prune learning buffer entries promoted to PLUR engrams"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Minimum age in days for entries to be eligible (default: 90)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only, do not modify files",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show per-entry decisions",
    )
    parser.add_argument(
        "--engrams",
        type=Path,
        default=ENGRAMS_FILE,
        help=f"Path to engrams.yaml (default: {ENGRAMS_FILE})",
    )
    args = parser.parse_args()

    cutoff_date = date.today() - timedelta(days=args.days)

    # Load engram index
    print("Loading engram index...", end=" ", flush=True)
    engram_index = load_engram_index(args.engrams)
    if engram_index is None:
        print(f"FAILED (file not found: {args.engrams})")
        print("Entries will be kept if engram check cannot be performed.")
    else:
        print(f"OK ({len(engram_index)} engrams)")

    files = find_learning_files()

    if not files:
        print("No learning files found.")
        sys.exit(0)

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"\n=== Learning Buffer Pruner ({mode}) ===")
    print(f"Cutoff: entries older than {args.days} days (before {cutoff_date})")
    print(f"Files found: {len(files)}")
    print()

    totals = {
        "files": 0, "total": 0, "eligible": 0,
        "pruned": 0, "kept": 0, "skipped": 0,
    }

    for filepath in files:
        rel = filepath.relative_to(ROOT)
        print(f"Processing: {rel}")

        stats = prune_file(
            filepath, cutoff_date, engram_index,
            dry_run=args.dry_run, verbose=args.verbose,
        )
        totals["files"] += 1
        for key in ("total", "eligible", "pruned", "kept", "skipped"):
            totals[key] += stats[key]

        print(f"  entries: {stats['total']}  eligible: {stats['eligible']}  "
              f"pruned: {stats['pruned']}  kept: {stats['kept']}")
        print()

    print("=== Summary ===")
    print(f"Files processed:  {totals['files']}")
    print(f"Total entries:    {totals['total']}")
    print(f"Eligible (old):   {totals['eligible']}")
    print(f"Pruned:           {totals['pruned']}")
    print(f"Kept:             {totals['kept']}")
    if totals["skipped"]:
        print(f"Skipped (no date): {totals['skipped']}")

    if args.dry_run and totals["pruned"] > 0:
        print(f"\nRe-run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
