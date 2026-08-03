#!/usr/bin/env python3
"""
Nightshift inbox cleanup: deduplicate, archive, report.
Run from ~/Data directory.
"""

import os
import re
import shutil
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spaces import discover_spaces as _discover_spaces  # noqa: E402

DATA_DIR = Path(os.environ.get("DATA_DIR", Path.home() / "Data"))

# Archive layout: {space}/4-archive/nightshift/{YYYY-MM}/
def archive_path(space: str, filename: str, created: str) -> Path:
    """Get archive destination for a file."""
    # Extract month from created date or filename
    month = None
    if created:
        m = re.search(r'(\d{4}-\d{2})', created)
        if m:
            month = m.group(1)
    if not month:
        m = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
        if m:
            month = m.group(1)[:7]
    if not month:
        month = "unknown"

    return DATA_DIR / space / "4-archive" / "nightshift" / month / filename


def parse_frontmatter(filepath: Path) -> dict:
    """Extract frontmatter fields from a nightshift output file."""
    meta = {"title": "", "score": None, "status": "", "created": "", "task_type": ""}
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return meta

    # Check for YAML frontmatter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            fm = text[3:end]
            for line in fm.split("\n"):
                line = line.strip()
                if line.startswith("title:"):
                    meta["title"] = line[6:].strip().strip('"').strip("'")
                elif line.startswith("score:"):
                    try:
                        meta["score"] = float(line[6:].strip())
                    except ValueError:
                        pass
                elif line.startswith("status:"):
                    meta["status"] = line[7:].strip()
                elif line.startswith("created:"):
                    meta["created"] = line[8:].strip()
                elif line.startswith("task_type:"):
                    meta["task_type"] = line[10:].strip().strip('"')

    # Fallback: get title from first heading
    if not meta["title"]:
        for line in text.split("\n"):
            if line.startswith("# "):
                meta["title"] = line[2:].strip()
                break

    return meta


def normalize_title(title: str) -> str:
    """Normalize title for dedup grouping."""
    t = title.lower().strip()
    # Remove priority markers
    t = re.sub(r'\[#[a-c]\]\s*', '', t)
    # Remove leading/trailing punctuation
    t = t.strip(" -:.")
    return t


def classify_file(filename: str, meta: dict, space: str) -> str:
    """Classify a file into action categories."""
    title_lower = (meta["title"] or "").lower()
    status = meta["status"] or ""

    # Status-only files (no real content)
    status_keywords = [
        "already_completed", "already-implemented", "already-processed",
        "completion", "complete"  # but not as a score status
    ]

    # Summaries
    if "nightshift-summary-" in filename:
        return "archive_summary"

    # Failure/escalation reports
    if "failure-report" in filename or "technical-escalation" in filename:
        return "archive_status"

    # "already completed" status files
    if status in ("already_completed", "already-implemented"):
        return "archive_status"

    # Execution summary / completion report files (no score)
    if meta["score"] is None:
        if any(kw in title_lower for kw in [
            "completion report", "execution summary", "task completion",
            "status update", "completion note", "task complete"
        ]):
            return "archive_status"

    return "keep"


# Stale news topics from Dec 2025 (not datafund-strategic)
STALE_NEWS_PATTERNS = [
    "disney", "gpt-5", "google's secret kitchen", "six-armed",
    "nous research", "ai news platform", "battle of the ai",
    "newsroom", "darla cameron", "aave catches flak",
    "oboe.*personalized learning", "nft",
    "rewardrush", "earning platform"
]

STRATEGIC_PATTERNS = [
    "regulatory", "tokenization", "data rwa", "mckinsey",
    "zk proof", "data standardization", "competitive landscape",
    "critical mass", "marketplace", "verity", "datafund",
    "swarm", "tether", "fineweb", "jpmorgan", "blockchain",
    "consent-verified", "amd sev", "positioning",
    "fiware", "human brain project", "caresyntax", "mode network",
    "alembic"
]


def is_stale_news(title: str) -> bool:
    t = title.lower()
    return any(re.search(p, t) for p in STALE_NEWS_PATTERNS)


def discover_spaces() -> list:
    """Space directory names. See lib/spaces.py and DIP-0015."""
    return [s.path.name for s in _discover_spaces(DATA_DIR)]


def main():
    spaces = discover_spaces()

    all_files = []  # (space, filepath, meta)
    for space in spaces:
        inbox = DATA_DIR / space / "0-inbox"
        if not inbox.exists():
            continue
        for f in sorted(inbox.glob("nightshift-*.md")):
            meta = parse_frontmatter(f)
            all_files.append((space, f, meta))

    print(f"Total nightshift files found: {len(all_files)}")
    print()

    # Step 1: Group by normalized title for dedup
    title_groups = defaultdict(list)
    no_title = []
    for space, fp, meta in all_files:
        nt = normalize_title(meta["title"])
        if nt:
            title_groups[nt].append((space, fp, meta))
        else:
            no_title.append((space, fp, meta))

    # Step 2: Identify duplicates (groups with >1 file)
    to_archive = []  # (space, filepath, reason)
    to_keep = []     # (space, filepath, meta)

    dup_count = 0
    for title, group in title_groups.items():
        if len(group) > 1:
            # Sort by score descending (None scores last)
            scored = sorted(group, key=lambda x: x[2]["score"] if x[2]["score"] is not None else -1, reverse=True)
            # Keep the best one
            best = scored[0]
            to_keep.append(best)
            # Archive the rest
            for item in scored[1:]:
                to_archive.append((item[0], item[1], f"duplicate (best: {best[2]['score']})"))
                dup_count += 1
        else:
            to_keep.append(group[0])

    # Also process no-title files
    for item in no_title:
        to_keep.append(item)

    print(f"Duplicates to archive: {dup_count}")
    print()

    # Step 3: Classify remaining files
    final_keep = []
    for space, fp, meta in to_keep:
        filename = fp.name
        cat = classify_file(filename, meta, space)

        if cat.startswith("archive_"):
            to_archive.append((space, fp, cat))
        else:
            # Check for stale Dec 2025 news
            if meta.get("created", "").startswith("2025-12"):
                if is_stale_news(meta["title"]):
                    to_archive.append((space, fp, "stale_news_dec2025"))
                else:
                    final_keep.append((space, fp, meta))
            else:
                final_keep.append((space, fp, meta))

    # Report
    print("=" * 60)
    print("ARCHIVE PLAN")
    print("=" * 60)

    reasons = defaultdict(list)
    for space, fp, reason in to_archive:
        reasons[reason].append((space, fp))

    for reason, files in sorted(reasons.items()):
        print(f"\n### {reason} ({len(files)} files)")
        for space, fp in files[:5]:
            print(f"  {space}: {fp.name}")
        if len(files) > 5:
            print(f"  ... and {len(files) - 5} more")

    print()
    print("=" * 60)
    print(f"KEEP: {len(final_keep)} files")
    print(f"ARCHIVE: {len(to_archive)} files")
    print("=" * 60)

    # Group kept files by topic for overview
    print("\n### Files to KEEP (by space):")
    for space in spaces:
        space_files = [(fp, meta) for s, fp, meta in final_keep if s == space]
        if space_files:
            print(f"\n  {space} ({len(space_files)} files):")
            for fp, meta in sorted(space_files, key=lambda x: x[1].get("score") or 0, reverse=True)[:15]:
                score = f"{meta['score']:.2f}" if meta["score"] else "----"
                title = meta["title"][:55] if meta["title"] else fp.name[:55]
                print(f"    [{score}] {title}")
            if len(space_files) > 15:
                print(f"    ... and {len(space_files) - 15} more")

    # Execute
    print("\n" + "=" * 60)
    print("EXECUTING ARCHIVE...")
    print("=" * 60)

    archived = 0
    errors = 0
    for space, fp, reason in to_archive:
        dest = archive_path(space, fp.name, parse_frontmatter(fp).get("created", ""))
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(fp), str(dest))
            archived += 1
        except Exception as e:
            print(f"  ERROR moving {fp.name}: {e}")
            errors += 1

    print(f"\nArchived: {archived} files")
    print(f"Errors: {errors}")
    print(f"Remaining in inboxes: {len(final_keep)}")


if __name__ == "__main__":
    main()
