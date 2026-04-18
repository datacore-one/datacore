#!/usr/bin/env python3
"""
ARCHIVED 2026-04-18 — Stubs removed from Datacore.

Stubs were auto-generated empty .md files for every unresolved [[wikilink]].
14,389 stubs were deleted; unresolved links are now tracked via resolved=0
in the knowledge.db links table. See session journal 2026-04-18 for details.

Original description:
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
MODEL_PRO = "gemini-3.1-pro-preview"

STAGE1_PROMPT = """You are a Zettelkasten knowledge synthesis expert. Given a concept name and excerpts from notes that reference it, extract 2-5 atomic insights and create a hub note.

Concept: {title}
Number of referencing notes: {count}

Connected notes:
{notes_content}

Your job:
1. Read all connected notes carefully
2. Identify 2-5 distinct atomic insights — specific claims, patterns, or mechanisms (not summaries)
3. Each insight should be a standalone idea expressible as a statement title
4. Write each insight in personal, conversational voice — as if explaining to yourself why it matters
5. Connect insights to practical relevance (projects, decisions, strategy)

Output format (markdown, no fences):

## Insights

### [Insight title as a claim/statement]
[2-4 sentences in personal voice. What is the insight? Why does it matter? How does it connect to real work?]
Source: [which connected notes this came from]

### [Next insight title]
[2-4 sentences]
Source: [which notes]

## Related Notes
- [[note title]] — [one-line HOW it relates, not just that it does]
"""

STAGE2_PROMPT = """You are refining extracted Zettelkasten insights. For each insight below, deepen the analysis.

Concept: {title}

Extracted insights:
{summary}

Source notes:
{notes_content}

For each insight:
1. Check: is this genuinely atomic (one idea) or a bundled summary? Split if needed.
2. Add cross-domain connections you see across the source notes
3. Identify tensions or contradictions between insights
4. Flag relevance to active projects: Datafund, Fairdrop, Fair Data Society, Verity, trading, health
5. Write in personal voice — "we" not "one", "matters because" not "it is noteworthy that"

If an insight is too generic or obvious, drop it and explain why.

Output the refined insights in the same format:

### [Insight title as claim]
[2-4 sentences, personal voice, with source and project relevance]
Source: [notes]
Related: [[Note]] — [how it relates]
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


def parse_insights(text):
    """Parse ### headed insights from AI output into list of (title, body) tuples."""
    insights = []
    current_title = None
    current_body = []
    for line in text.split('\n'):
        if line.startswith('### '):
            if current_title:
                insights.append((current_title, '\n'.join(current_body).strip()))
            current_title = line[4:].strip()
            current_body = []
        elif current_title:
            current_body.append(line)
    if current_title:
        insights.append((current_title, '\n'.join(current_body).strip()))
    return insights


def title_to_filename(title):
    """Convert insight title to a valid filename."""
    # Remove quotes, parentheses, colons
    clean = title.replace('"', '').replace("'", '').replace(':', '-').replace('/', '-')
    clean = clean.replace('(', '').replace(')', '').replace(',', '')
    # Replace spaces with hyphens, collapse multiples
    import re
    clean = re.sub(r'\s+', '-', clean.strip())
    clean = re.sub(r'-+', '-', clean)
    clean = clean.strip('-')
    return clean


def expand_stub(entry, db_path, dry_run=False):
    """Expand a single stub: extract atomic zettels + convert stub to hub."""
    title = entry['title']
    links = entry['links']
    stub_path = Path(entry['path'])
    zettel_dir = stub_path.parent

    # Get connected notes
    notes = get_connected_notes(db_path, title)
    if not notes:
        return False, "no connected notes found"

    # Truncate to token cap
    notes = truncate_notes(notes, CHAR_CAP)
    notes_text = format_notes_for_prompt(notes)

    if dry_run:
        return True, f"would expand: {len(notes)} notes, ~{len(notes_text)} chars"

    # Stage 1: Extract insights (always Flash — fast extraction)
    s1_prompt = STAGE1_PROMPT.format(title=title, count=len(notes), notes_content=notes_text)
    s1_model = select_model(links, stage=1)
    s1_result = call_gemini(s1_prompt, s1_model)
    if not s1_result:
        return False, "stage 1 failed"

    # Stage 2: Refine insights (tiered — deeper analysis)
    s2_model = select_model(links, stage=2)
    s2_prompt = STAGE2_PROMPT.format(title=title, summary=s1_result, notes_content=notes_text)
    s2_result = call_gemini(s2_prompt, s2_model)

    # Use stage 2 result if available, otherwise stage 1
    final_insights_text = s2_result if s2_result else s1_result
    insights = parse_insights(final_insights_text)

    if not insights:
        # Fallback: write stage 1 output directly
        stub_path.write_text(f"# {title}\n\n{s1_result}\n\n#auto-synthesized\n")
        return True, f"expanded flat ({s1_model}, {len(notes)} notes, no insights parsed)"

    today = date.today().isoformat()

    # Create atomic zettel files
    zettel_links = []
    created = 0
    for insight_title, insight_body in insights:
        filename = title_to_filename(insight_title)
        if not filename:
            continue
        zettel_path = zettel_dir / f"{filename}.md"
        # Don't overwrite existing non-stub notes
        if zettel_path.exists():
            existing = zettel_path.read_text(errors='ignore')
            if '#auto-synthesized' not in existing and '> This is a stub' not in existing:
                zettel_links.append(f"- [[{insight_title}]]")
                continue

        zettel_content = f"""# {insight_title}

{insight_body}

From: [[{title}]]

#zettel #auto-synthesized
"""
        zettel_path.write_text(zettel_content)
        zettel_links.append(f"- [[{insight_title}]]")
        created += 1

    # Convert original stub to hub note
    # Extract related notes from stage 1 output
    related_section = ""
    if "## Related Notes" in s1_result:
        related_section = s1_result[s1_result.index("## Related Notes"):]
    elif "## Connected Notes" in s1_result:
        related_section = s1_result[s1_result.index("## Connected Notes"):]

    hub_content = f"""# {title}

> Hub note — extracted {len(insights)} atomic insights from {len(notes)} connected notes on {today}.

## Atomic Insights

{chr(10).join(zettel_links)}

{related_section}

#hub #auto-synthesized
"""
    stub_path.write_text(hub_content)
    return True, f"hub + {created} zettels ({s1_model}/{s2_model}, {len(notes)} notes)"


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
