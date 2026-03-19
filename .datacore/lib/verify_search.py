#!/usr/bin/env python3
"""
Search Coverage Verification — Tests 20 queries against FTS5 + engrams.

Compares results against the baseline analysis from 2026-03-18.
Run after knowledge cleanup and search infrastructure changes.

Usage:
    python verify_search.py              # Run all 20 queries
    python verify_search.py --baseline   # Show baseline for comparison
"""
import sys
import sqlite3
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from zettel_db import get_db_path

QUERIES = [
    ("nightshift server deployment", "infrastructure"),
    ("blackpi raspberry pi setup", "infrastructure"),
    ("Fairdrop escrow architecture", "project"),
    ("ADE CLI commands", "project"),
    ("org-workspace library", "project"),
    ("Verity data marketplace", "project"),
    ("why we chose Bun over Node", "decisions"),
    ("layered context DIP-0002", "architecture"),
    ("engram memory model", "architecture"),
    ("Datafund team members", "people"),
    ("Fair Data Society partners", "people"),
    ("SOL trading framework", "trading"),
    ("position health score calculation", "trading"),
    ("HRV trends and sleep", "health"),
    ("org-mode task tagging", "conventions"),
    ("zettelkasten note format", "conventions"),
    ("megaphone SaaS deployment", "recent"),
    ("datacore-bench benchmarking", "recent"),
    ("Santorio project", "niche"),
    ("Dubai pilot", "niche"),
]

BASELINE = {
    "total_with_results": 7,  # out of 20 (35%)
    "engram_quality_yes": 6,
    "engram_quality_partial": 7,
    "engram_quality_no": 7,
}


def fts_search(db_path, query, limit=5):
    """Search FTS5 index directly."""
    conn = sqlite3.connect(str(db_path))
    # Tokenize: split words, join with AND (matches fts.ts tokenizeQuery: len > 1)
    words = [w for w in query.split() if len(w) > 1]
    fts_query = ' AND '.join(words) if words else query

    try:
        rows = conn.execute("""
            SELECT f.title, f.type, f.is_stub,
                   snippet(files_fts, 1, '', '', '...', 20) as snippet
            FROM files_fts
            JOIN files f ON f.rowid = files_fts.rowid
            WHERE files_fts MATCH ?
            AND f.is_stub = 0
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit)).fetchall()
    except Exception as e:
        rows = []
        print(f"  FTS error: {e}")

    conn.close()
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--space', default='personal')
    args = parser.parse_args()

    if args.baseline:
        print("BASELINE (2026-03-18, before cleanup):")
        print(f"  Queries with FTS results: {BASELINE['total_with_results']}/20 (35%)")
        print(f"  Engram quality: {BASELINE['engram_quality_yes']} yes, {BASELINE['engram_quality_partial']} partial, {BASELINE['engram_quality_no']} no")
        return

    db_path = get_db_path(args.space)
    print(f"Testing against: {db_path}\n")

    hits = 0

    print(f"{'#':>2} {'Domain':<14} {'Hits':>4} {'Query'}")
    print("-" * 70)

    for i, (query, domain) in enumerate(QUERIES, 1):
        results = fts_search(db_path, query)
        hit_count = len(results)
        if hit_count > 0:
            hits += 1

        marker = "+" if hit_count > 0 else "x"
        print(f"{i:>2} {domain:<14} {hit_count:>4} {marker} {query}")

    print(f"\n{'='*70}")
    print(f"RESULTS: {hits}/20 queries returned results ({hits*100//20}%)")
    print(f"BASELINE: {BASELINE['total_with_results']}/20 (35%)")
    print(f"TARGET: 14/20 (70%)")
    improvement = hits - BASELINE['total_with_results']
    print(f"CHANGE: {'+' if improvement >= 0 else ''}{improvement} queries improved")


if __name__ == '__main__':
    main()
