#!/usr/bin/env python3
"""
Stub Expander — AI-synthesize stubs into knowledge hubs.

Two-stage pipeline:
  Stage 1 (all stubs): Factual summary via Gemini Flash
  Stage 2 (tiered):    Emergent insights via Gemini Pro (6+) or Flash (3-5)

Usage:
    python stub_expander.py expand.txt                  # Process all
    python stub_expander.py expand.txt --batch-size 10  # Smaller batches
    python stub_expander.py expand.txt --dry-run        # Show plan, don't call API
    python stub_expander.py expand.txt --resume         # Skip already-expanded stubs
"""
import sys
import os
import sqlite3
import argparse
import json
import time
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent))
from zettel_db import get_db_path

try:
    import google.generativeai as genai
except ImportError:
    print("ERROR: pip install google-generativeai")
    sys.exit(1)

TOKEN_CAP = 20_000  # max input tokens per stub (rough: 1 token ~ 4 chars)
CHAR_CAP = TOKEN_CAP * 4
BATCH_SIZE = 50

# Model tiering
MODEL_FLASH = "gemini-2.5-flash"
MODEL_PRO = "gemini-3.1-pro"

STAGE1_PROMPT = """You are a knowledge base analyst. Given a concept name and excerpts from notes that reference it, write a factual summary.

Concept: {title}
Number of referencing notes: {count}

Connected notes:
{notes_content}

Write a concise summary (200-400 words) of what this knowledge base says about "{title}".
Extract 3-7 key themes as bullet points.
For each connected note, write a one-line description of how it references this concept.

Output format (markdown, no fences):
## Summary
[summary]

## Key Themes
- [theme]

## Connected Notes
- [[note title]] — [one-line context]
"""

STAGE2_PROMPT = """You are a knowledge synthesis expert. Given a concept, its summary, and the original source notes, identify emergent insights.

Concept: {title}
Summary: {summary}

Source notes:
{notes_content}

Look for:
- Non-obvious patterns across the references
- Cross-domain connections (e.g., a trading concept appearing in governance contexts)
- Underlying themes that aren't stated explicitly in any single note
- Potential relevance to active projects or strategic decisions
- Contradictions or tensions between different references
- "Aha" moments — what becomes visible only when seeing all references together

Write 100-300 words of emergent insights. Be specific and substantive — don't just say "there are connections", explain what they are. If nothing genuinely emerges, say so briefly rather than fabricating.

Output format (markdown, no fences):
## Emergent Insights
[insights]
"""


def load_expand_list(path):
    """Load expand.txt: path<tab>title<tab>links per line."""
    entries = []
    with open(path) as f:
        for line in f:
            parts = line.strip().split('\t')
            if len(parts) >= 3:
                entries.append({
                    'path': parts[0],
                    'title': parts[1],
                    'links': int(parts[2]),
                })
    return entries


def get_connected_notes(db_path, stub_title):
    """Get non-stub connected note content for a stub."""
    conn = sqlite3.connect(str(db_path))

    # Find the stub's ID
    row = conn.execute("SELECT id FROM files WHERE title = ? AND is_stub = 1", (stub_title,)).fetchone()
    if not row:
        conn.close()
        return []

    stub_id = row[0]

    # Get source files that link TO this stub (non-stub sources only)
    notes = conn.execute("""
        SELECT f.title, f.content, f.path
        FROM links l
        JOIN files f ON f.id = l.source_id
        WHERE l.target_id = ?
        AND f.is_stub = 0
        AND f.content IS NOT NULL
        AND length(f.content) > 50
        ORDER BY length(f.content) DESC
    """, (stub_id,)).fetchall()

    conn.close()
    return [{'title': n[0], 'content': n[1], 'path': n[2]} for n in notes]


def truncate_notes(notes, char_cap):
    """Truncate notes to fit within character cap, keeping longest notes."""
    total = 0
    result = []
    for note in notes:
        content = note['content']
        remaining = char_cap - total
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining] + "\n[...truncated]"
        result.append({**note, 'content': content})
        total += len(content)
    return result


def format_notes_for_prompt(notes):
    """Format notes into a string for the prompt."""
    parts = []
    for note in notes:
        parts.append(f"### [[{note['title']}]]\n{note['content']}\n")
    return "\n".join(parts)


def select_model(links, stage):
    """Select model based on link count and stage."""
    if stage == 1:
        return MODEL_FLASH
    # Stage 2
    if links >= 6:
        return MODEL_PRO
    return MODEL_FLASH


def call_gemini(prompt, model_name):
    """Call Gemini API with retry."""
    model = genai.GenerativeModel(model_name)
    for attempt in range(3):
        try:
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  Retry in {wait}s: {e}")
                time.sleep(wait)
            else:
                print(f"  FAILED after 3 attempts: {e}")
                return None


def expand_stub(entry, db_path, dry_run=False):
    """Expand a single stub through two-stage pipeline."""
    title = entry['title']
    links = entry['links']
    stub_path = Path(entry['path'])

    # Get connected notes
    notes = get_connected_notes(db_path, title)
    if not notes:
        return False, "no connected notes found"

    # Truncate to token cap
    notes = truncate_notes(notes, CHAR_CAP)
    notes_text = format_notes_for_prompt(notes)

    if dry_run:
        return True, f"would expand: {len(notes)} notes, ~{len(notes_text)} chars"

    # Stage 1: Summary (always Flash)
    s1_prompt = STAGE1_PROMPT.format(title=title, count=len(notes), notes_content=notes_text)
    s1_model = select_model(links, stage=1)
    s1_result = call_gemini(s1_prompt, s1_model)
    if not s1_result:
        return False, "stage 1 failed"

    # Stage 2: Emergent insights (tiered)
    s2_model = select_model(links, stage=2)
    s2_prompt = STAGE2_PROMPT.format(title=title, summary=s1_result, notes_content=notes_text)
    s2_result = call_gemini(s2_prompt, s2_model)

    # Compose final document
    today = date.today().isoformat()
    emergent_section = ""
    if s2_result:
        emergent_section = f"\n{s2_result}\n"
    else:
        emergent_section = "\n## Emergent Insights\n\n_Stage 2 processing failed. Re-run with --resume to retry._\n"

    output = f"""# {title}

> Auto-synthesized from {len(notes)} connected notes on {today}.
> Expanded by Datacore knowledge metabolism.

{s1_result}
{emergent_section}
#auto-synthesized #stub-expanded
"""

    # Write to file (overwrite stub)
    stub_path.write_text(output)
    return True, f"expanded ({s1_model}/{s2_model}, {len(notes)} notes)"


def main():
    parser = argparse.ArgumentParser(description='Stub Expander')
    parser.add_argument('expand_list', help='Path to expand.txt from stub_triage.py')
    parser.add_argument('--batch-size', type=int, default=BATCH_SIZE)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Skip stubs already containing #auto-synthesized')
    parser.add_argument('--space', default='personal')
    parser.add_argument('--limit', type=int, help='Process only first N stubs')
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key and not args.dry_run:
        print("ERROR: GEMINI_API_KEY not set")
        sys.exit(1)
    if api_key:
        genai.configure(api_key=api_key)

    db_path = get_db_path(args.space)
    entries = load_expand_list(args.expand_list)

    if args.limit:
        entries = entries[:args.limit]

    if args.resume:
        original = len(entries)
        def _is_expanded(p):
            try:
                return '#auto-synthesized' in Path(p).read_text(errors='ignore')
            except (FileNotFoundError, OSError):
                return True  # skip missing files
        entries = [e for e in entries if not _is_expanded(e['path'])]
        print(f"Resume mode: {original - len(entries)} already expanded, {len(entries)} remaining")

    print(f"Processing {len(entries)} stubs in batches of {args.batch_size}")
    print(f"Model tiers: 20+ -> {MODEL_PRO}, 6-19 -> {MODEL_PRO}, 3-5 -> {MODEL_FLASH}")
    if args.dry_run:
        print("DRY RUN -- no API calls")

    success, fail = 0, 0
    for i, entry in enumerate(entries):
        batch_num = i // args.batch_size + 1
        if i > 0 and i % args.batch_size == 0:
            print(f"\n--- Batch {batch_num} ({i}/{len(entries)}) | OK={success} FAIL={fail} ---\n")

        print(f"  [{i+1}/{len(entries)}] {entry['title']} ({entry['links']} links) ", end="", flush=True)
        ok, msg = expand_stub(entry, db_path, dry_run=args.dry_run)
        if ok:
            success += 1
            print(f"+ {msg}")
        else:
            fail += 1
            print(f"x {msg}")

        # Rate limiting: small delay between API calls
        if not args.dry_run and i < len(entries) - 1:
            time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"DONE: {success} expanded, {fail} failed")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
