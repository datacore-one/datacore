#!/usr/bin/env python3
"""
Knowledge Base Quality Gate — Evaluate files using embeddings.

Uses pre-computed embeddings to:
1. Semantic dedup: find near-duplicate pairs (cosine sim > threshold)
2. Quality scoring: compare AI zettels to approved exemplars
3. Redundancy check: AI zettels that duplicate human-written content

Usage:
    python quality_gate.py                          # Dry-run analysis
    python quality_gate.py --execute                # Move flagged files to archive
    python quality_gate.py --dedup-threshold 0.92   # Adjust similarity threshold
"""
import sys
import sqlite3
import argparse
import shutil
import numpy as np
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from zettel_db import get_db_path, DATA_ROOT

ARCHIVE_DIR = DATA_ROOT / '.datacore' / 'state' / 'quality-gate-archive'
DEDUP_THRESHOLD = 0.92  # cosine similarity for near-duplicate
REDUNDANCY_THRESHOLD = 0.88  # AI zettel vs human zettel
QUALITY_THRESHOLD = 0.25  # min similarity to exemplars (low = probably junk)

# Approved exemplar files — the zettels you reviewed and liked
EXEMPLAR_TITLES = [
    "Decentralized Moats Are Built on Freedom to Leave",
    "Progressive Decentralization as Go-to-Market Strategy",
    "Intangible Capital Drives Decentralized Growth",
    "Decentralized Growth Is Architectural Not Promotional",
]


def load_embeddings(db_path):
    """Load all embeddings from DB. Returns dict of file_id -> (embedding, metadata)."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("""
        SELECT e.file_id, e.embedding, f.title, f.is_stub, f.word_count, f.content, f.path
        FROM embeddings e
        JOIN files f ON f.id = e.file_id
    """).fetchall()
    conn.close()

    result = {}
    for file_id, emb_blob, title, is_stub, word_count, content, path in rows:
        emb = np.frombuffer(emb_blob, dtype=np.float32)
        is_ai = content and '#auto-synthesized' in content if content else False
        is_hub = content and '#hub' in content if content else False
        result[file_id] = {
            'embedding': emb,
            'title': title,
            'is_stub': bool(is_stub),
            'is_ai': is_ai,
            'is_hub': is_hub,
            'word_count': word_count or 0,
            'path': path,
            'content': content or '',
        }
    return result


def cosine_sim(a, b):
    """Cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def find_duplicates(files, threshold):
    """Find near-duplicate pairs above threshold. Returns list of (sim, id_a, id_b)."""
    # Only compare non-stub files with >50 words
    candidates = {fid: f for fid, f in files.items()
                  if not f['is_stub'] and f['word_count'] > 50}

    print(f"  Comparing {len(candidates)} files for duplicates...")

    # Build embedding matrix for batch computation
    ids = list(candidates.keys())
    if len(ids) < 2:
        return []

    matrix = np.array([candidates[fid]['embedding'] for fid in ids])
    # Normalize
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    matrix = matrix / norms

    # Find pairs — process in chunks to manage memory
    duplicates = []
    chunk_size = 1000
    for i in range(0, len(ids), chunk_size):
        chunk = matrix[i:i+chunk_size]
        # Similarity of chunk against all
        sims = chunk @ matrix.T
        for ci, row in enumerate(sims):
            global_i = i + ci
            # Only check upper triangle (j > global_i)
            for j in range(global_i + 1, len(ids)):
                if row[j] > threshold:
                    duplicates.append((float(row[j]), ids[global_i], ids[j]))

        if i % 5000 == 0 and i > 0:
            print(f"    ...processed {i}/{len(ids)}")

    duplicates.sort(key=lambda x: x[0], reverse=True)
    return duplicates


def score_quality(files, exemplar_titles):
    """Score AI zettels by similarity to approved exemplars."""
    # Find exemplar embeddings
    exemplar_embs = []
    for fid, f in files.items():
        if f['title'] in exemplar_titles and not f['is_stub']:
            exemplar_embs.append(f['embedding'])

    if not exemplar_embs:
        print("  WARNING: No exemplar files found in DB")
        return {}

    exemplar_mean = np.mean(exemplar_embs, axis=0)
    exemplar_norm = np.linalg.norm(exemplar_mean)
    if exemplar_norm > 0:
        exemplar_mean = exemplar_mean / exemplar_norm

    # Score each AI zettel
    scores = {}
    for fid, f in files.items():
        if not f['is_ai'] or f['is_hub'] or f['is_stub']:
            continue
        emb = f['embedding']
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb = emb / norm
        scores[fid] = float(np.dot(emb, exemplar_mean))

    return scores


def find_ai_human_redundancy(files, threshold):
    """Find AI zettels that are redundant with human-written content."""
    ai_files = {fid: f for fid, f in files.items()
                if f['is_ai'] and not f['is_hub'] and not f['is_stub'] and f['word_count'] > 50}
    human_files = {fid: f for fid, f in files.items()
                   if not f['is_ai'] and not f['is_stub'] and f['word_count'] > 100}

    if not ai_files or not human_files:
        return []

    print(f"  Comparing {len(ai_files)} AI zettels against {len(human_files)} human files...")

    # Build human embedding matrix
    human_ids = list(human_files.keys())
    human_matrix = np.array([human_files[fid]['embedding'] for fid in human_ids])
    norms = np.linalg.norm(human_matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    human_matrix = human_matrix / norms

    redundant = []
    for ai_id, ai_f in ai_files.items():
        emb = ai_f['embedding']
        norm = np.linalg.norm(emb)
        if norm == 0:
            continue
        emb = emb / norm

        sims = human_matrix @ emb
        max_idx = np.argmax(sims)
        max_sim = float(sims[max_idx])

        if max_sim > threshold:
            redundant.append((max_sim, ai_id, human_ids[max_idx]))

    redundant.sort(key=lambda x: x[0], reverse=True)
    return redundant


def main():
    parser = argparse.ArgumentParser(description='Knowledge Base Quality Gate')
    parser.add_argument('--execute', action='store_true', help='Move flagged files to archive')
    parser.add_argument('--dedup-threshold', type=float, default=DEDUP_THRESHOLD)
    parser.add_argument('--redundancy-threshold', type=float, default=REDUNDANCY_THRESHOLD)
    parser.add_argument('--quality-threshold', type=float, default=QUALITY_THRESHOLD)
    parser.add_argument('--space', default='personal')
    args = parser.parse_args()

    db_path = get_db_path(args.space)
    print(f"Loading embeddings from: {db_path}")
    files = load_embeddings(db_path)
    print(f"Loaded {len(files)} files with embeddings\n")

    flagged = {}  # file_id -> (reason, detail)

    # Pass 1: Semantic deduplication
    print(f"[1/3] Semantic dedup (threshold={args.dedup_threshold})...")
    dupes = find_duplicates(files, args.dedup_threshold)
    print(f"  Found {len(dupes)} duplicate pairs")
    for sim, id_a, id_b in dupes:
        fa, fb = files[id_a], files[id_b]
        # Keep the one with more words; if AI vs human, keep human
        if fa['is_ai'] and not fb['is_ai']:
            loser, winner = id_a, id_b
        elif fb['is_ai'] and not fa['is_ai']:
            loser, winner = id_b, id_a
        elif fa['word_count'] >= fb['word_count']:
            loser, winner = id_b, id_a
        else:
            loser, winner = id_a, id_b
        if loser not in flagged:
            flagged[loser] = ('duplicate', f"sim={sim:.3f} of '{files[winner]['title'][:50]}'")

    # Pass 2: AI quality scoring
    print(f"\n[2/3] Quality scoring against exemplars...")
    quality_scores = score_quality(files, EXEMPLAR_TITLES)
    low_quality = [(fid, score) for fid, score in quality_scores.items() if score < args.quality_threshold]
    low_quality.sort(key=lambda x: x[1])
    print(f"  Scored {len(quality_scores)} AI zettels, {len(low_quality)} below threshold ({args.quality_threshold})")
    for fid, score in low_quality:
        if fid not in flagged:
            flagged[fid] = ('low_quality', f"score={score:.3f}")

    # Pass 3: AI-human redundancy
    print(f"\n[3/3] AI-human redundancy (threshold={args.redundancy_threshold})...")
    redundant = find_ai_human_redundancy(files, args.redundancy_threshold)
    print(f"  Found {len(redundant)} redundant AI zettels")
    for sim, ai_id, human_id in redundant:
        if ai_id not in flagged:
            flagged[ai_id] = ('redundant_with_human', f"sim={sim:.3f} of '{files[human_id]['title'][:50]}'")

    # Summary
    reasons = defaultdict(int)
    for _, (reason, _) in flagged.items():
        reasons[reason] += 1

    print(f"\n{'='*60}")
    print(f"QUALITY GATE RESULTS")
    print(f"{'='*60}")
    print(f"  Total files analyzed: {len(files)}")
    print(f"  Flagged for removal:  {len(flagged)}")
    for reason, count in sorted(reasons.items()):
        print(f"    {reason}: {count}")
    print(f"  Passing:              {len(files) - len(flagged)}")

    # Show top flagged
    print(f"\nTop 20 flagged files:")
    sorted_flagged = sorted(flagged.items(), key=lambda x: x[1][1])
    for fid, (reason, detail) in sorted_flagged[:20]:
        f = files[fid]
        print(f"  [{reason}] {f['title'][:50]} ({f['word_count']}w) — {detail}")

    # Quality score distribution for AI zettels
    if quality_scores:
        scores = list(quality_scores.values())
        print(f"\nAI zettel quality distribution:")
        print(f"  Min: {min(scores):.3f}  Max: {max(scores):.3f}  Mean: {np.mean(scores):.3f}  Median: {np.median(scores):.3f}")
        buckets = [0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
        for i in range(len(buckets)-1):
            count = sum(1 for s in scores if buckets[i] <= s < buckets[i+1])
            print(f"  {buckets[i]:.1f}-{buckets[i+1]:.1f}: {count}")

    if args.execute:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        log_path = ARCHIVE_DIR / f"quality-gate-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        moved = 0
        with open(log_path, 'w') as log:
            for fid, (reason, detail) in flagged.items():
                f = files[fid]
                src = Path(f['path'])
                if not src.exists():
                    continue
                dst = ARCHIVE_DIR / src.name
                if dst.exists():
                    dst = ARCHIVE_DIR / f"{src.stem}_{moved}{src.suffix}"
                shutil.move(str(src), str(dst))
                log.write(f"{reason}\t{detail}\t{f['title']}\t{f['path']}\n")
                moved += 1
        print(f"\nMoved {moved} files to {ARCHIVE_DIR}")
        print(f"Log: {log_path}")
    else:
        print(f"\nDry run. Use --execute to archive flagged files.")


if __name__ == '__main__':
    main()
