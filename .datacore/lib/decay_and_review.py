#!/usr/bin/env python3
"""Weekly decay-and-review report generator for PLUR engram memory.

Designed to be invoked by a Claude Code agent that has MCP access:
  1. Agent calls plur_batch_decay via MCP
  2. Agent runs this script, optionally passing decay results

Usage:
    python3 decay_and_review.py --dry-run
    python3 decay_and_review.py --decay-json /path/to/decay_results.json
    echo '{"processed":10}' | python3 decay_and_review.py

Outputs:
    stdout  — Markdown report
    stderr  — JSON summary for journal capture
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


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PLUR_HOME = Path.home() / ".plur"
HISTORY_DIR = PLUR_HOME / "history"
ENGRAMS_FILE = PLUR_HOME / "engrams.yaml"
DATACORE_LIB = Path(__file__).resolve().parent
PRUNE_SCRIPT = DATACORE_LIB / "prune_learning_buffer.py"

RECURRENCE_THRESHOLD = 3   # flag at 3+ recurrences
CRITICAL_THRESHOLD = 5     # critical at 5+
HISTORY_MONTHS = 3         # look back 3 months


# ---------------------------------------------------------------------------
# History reading
# ---------------------------------------------------------------------------

def month_keys(months_back: int) -> list[str]:
    """Return YYYY-MM strings for the last N months including current."""
    now = datetime.utcnow()
    keys = []
    for i in range(months_back):
        dt = now - timedelta(days=30 * i)
        keys.append(dt.strftime("%Y-%m"))
    return sorted(set(keys))


def read_history(months_back: int = HISTORY_MONTHS) -> list[dict]:
    """Read PLUR history JSONL files for the last N months."""
    events = []
    for key in month_keys(months_back):
        path = HISTORY_DIR / f"{key}.jsonl"
        if not path.exists():
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


# ---------------------------------------------------------------------------
# Recurrence analysis
# ---------------------------------------------------------------------------

def analyze_recurrences(events: list[dict]) -> dict[str, int]:
    """Count recurrence_detected events per engram_id.

    Also counts repeated negative feedback as a proxy for recurrence
    (since the history may not yet have explicit recurrence_detected events).
    """
    counts: Counter = Counter()

    for ev in events:
        event_type = ev.get("event", "")
        engram_id = ev.get("engram_id", "")
        if not engram_id:
            continue

        if event_type == "recurrence_detected":
            counts[engram_id] += 1
        elif event_type == "feedback_received":
            data = ev.get("data", {})
            if data.get("signal") == "negative":
                counts[engram_id] += 1

    return dict(counts)


def load_engram_statements() -> dict[str, dict]:
    """Load engram id -> {statement, type, domain, tags} from engrams.yaml.

    Uses basic YAML parsing to avoid external deps.
    """
    if not ENGRAMS_FILE.exists():
        return {}

    engrams = {}
    current_id = None
    current = {}
    multiline_key = None
    multiline_buf = []

    with open(ENGRAMS_FILE) as f:
        for line in f:
            stripped = line.rstrip()

            # Flush multiline buffer on new key or new engram
            if multiline_key and (stripped.startswith("  - id:") or
                                   (re.match(r"^    \w", stripped) and
                                    not stripped.startswith("      "))):
                current[multiline_key] = " ".join(multiline_buf).strip()
                multiline_key = None
                multiline_buf = []

            if stripped.startswith("  - id:"):
                # Save previous engram
                if current_id:
                    engrams[current_id] = current
                current_id = stripped.split(":", 1)[1].strip()
                current = {"id": current_id}
                continue

            if current_id is None:
                continue

            # Simple key-value pairs at engram level
            m = re.match(r"^    (\w+):\s*(.*)", stripped)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if key == "statement":
                    if val.startswith(">"):
                        multiline_key = "statement"
                        multiline_buf = []
                    else:
                        current["statement"] = val
                elif key in ("type", "domain", "status"):
                    current[key] = val
                elif key == "tags":
                    current["tags"] = []
                continue

            # Tag list items
            if stripped.startswith("      - ") and "tags" in current:
                current.setdefault("tags", []).append(stripped.strip("- ").strip())
                continue

            # Multiline statement continuation
            if multiline_key and stripped.startswith("      "):
                multiline_buf.append(stripped.strip())

        # Flush last engram
        if multiline_key and current_id:
            current[multiline_key] = " ".join(multiline_buf).strip()
        if current_id:
            engrams[current_id] = current

    return engrams


# ---------------------------------------------------------------------------
# Escalation categorization
# ---------------------------------------------------------------------------

CATEGORY_PATTERNS = [
    # (keywords in statement/domain/tags, category, fix suggestion)
    (
        ["hardcod", "typo", "wrong value", "incorrect", "constant", "literal"],
        "Hardcoding mistake",
        "Pre-commit hook or lint rule",
    ),
    (
        ["deploy", "server", "systemd", "service", "restart", "ssh", "nginx"],
        "Deployment mistake",
        "CI check or deploy checklist",
    ),
    (
        ["api", "endpoint", "sdk", "request", "response", "header", "auth"],
        "API misuse",
        "Wrapper function or SDK extension",
    ),
    (
        ["skip", "forgot", "missing step", "workflow", "process", "checklist"],
        "Process skip",
        "Hook that enforces the step",
    ),
    (
        ["assum", "expect", "thought", "believed", "wrong about"],
        "Wrong assumption",
        "CLAUDE.md addition or agent instruction",
    ),
]


def categorize_engram(engram: dict) -> tuple[str, str]:
    """Return (category, fix_suggestion) for an engram."""
    text = " ".join([
        engram.get("statement", ""),
        engram.get("domain", ""),
        " ".join(engram.get("tags", [])),
    ]).lower()

    for keywords, category, fix in CATEGORY_PATTERNS:
        if any(kw in text for kw in keywords):
            return category, fix

    return "General pattern", "CLAUDE.md addition or agent instruction"


# ---------------------------------------------------------------------------
# Buffer status (prune script integration)
# ---------------------------------------------------------------------------

def get_buffer_status() -> str:
    """Call prune_learning_buffer.py --dry-run if it exists."""
    if not PRUNE_SCRIPT.exists():
        return "_Prune script not found at `{}`_".format(PRUNE_SCRIPT)

    try:
        result = subprocess.run(
            [sys.executable, str(PRUNE_SCRIPT), "--dry-run"],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip() or result.stderr.strip()
        if output:
            return "```\n{}\n```".format(output)
        return "_Prune script returned no output (exit {})_".format(result.returncode)
    except subprocess.TimeoutExpired:
        return "_Prune script timed out_"
    except Exception as e:
        return "_Error running prune script: {}_".format(e)


# ---------------------------------------------------------------------------
# Decay results parsing
# ---------------------------------------------------------------------------

def read_decay_results(args) -> dict | None:
    """Read decay results from file, stdin, or return None for dry-run."""
    if args.dry_run:
        return None

    if args.decay_json:
        with open(args.decay_json) as f:
            return json.load(f)

    # Try stdin if not a TTY
    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                print("Warning: could not parse stdin as JSON", file=sys.stderr)

    return None


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(
    decay_results: dict | None,
    recurrences: dict[str, int],
    engram_data: dict[str, dict],
) -> tuple[str, dict]:
    """Generate markdown report and JSON summary.

    Returns (markdown_str, summary_dict).
    """
    lines = []
    summary = {}

    # Title
    today = datetime.utcnow().strftime("%Y-%m-%d")
    lines.append(f"# Weekly Decay & Review Report — {today}")
    lines.append("")

    # --- Decay Summary ---
    lines.append("## Decay Summary")
    lines.append("")
    if decay_results:
        processed = decay_results.get("processed", 0)
        transitions = decay_results.get("transitions", [])
        skipped = decay_results.get("skipped", 0)
        lines.append(f"- **Processed**: {processed} engrams")
        lines.append(f"- **Transitions**: {len(transitions)} state changes")
        for t in transitions[:20]:
            eid = t.get("engram_id", "?")
            frm = t.get("from", "?")
            to = t.get("to", "?")
            lines.append(f"  - `{eid}`: {frm} -> {to}")
        if len(transitions) > 20:
            lines.append(f"  - ... and {len(transitions) - 20} more")
        lines.append(f"- **Skipped**: {skipped}")
        summary["decay"] = {
            "processed": processed,
            "transitions": len(transitions),
            "skipped": skipped,
        }
    else:
        lines.append("_Dry run — no decay performed._")
        summary["decay"] = None
    lines.append("")

    # --- Recurrence Analysis ---
    flagged = {eid: count for eid, count in recurrences.items()
               if count >= RECURRENCE_THRESHOLD}

    lines.append("## Structural Upgrade Recommendations")
    lines.append("")

    if not flagged:
        lines.append("No engrams reached the recurrence threshold "
                      f"({RECURRENCE_THRESHOLD}+). System is healthy.")
        summary["escalations"] = []
    else:
        # Sort by count descending
        sorted_flagged = sorted(flagged.items(), key=lambda x: -x[1])
        escalations = []

        for eid, count in sorted_flagged:
            severity = "CRITICAL" if count >= CRITICAL_THRESHOLD else "WARNING"
            engram = engram_data.get(eid, {})
            statement = engram.get("statement", "_unknown_")
            status = engram.get("status", "?")
            category, fix = categorize_engram(engram)

            # Truncate long statements
            if len(statement) > 120:
                statement = statement[:117] + "..."

            lines.append(f"### [{severity}] `{eid}` ({count}x)")
            lines.append("")
            lines.append(f"- **Statement**: {statement}")
            lines.append(f"- **Status**: {status}")
            lines.append(f"- **Category**: {category}")
            lines.append(f"- **Suggested fix**: {fix}")
            lines.append("")

            escalations.append({
                "engram_id": eid,
                "count": count,
                "severity": severity,
                "category": category,
                "fix": fix,
            })

        summary["escalations"] = escalations

    lines.append("")

    # --- History Stats ---
    lines.append("## History Stats (last {} months)".format(HISTORY_MONTHS))
    lines.append("")
    events = read_history(HISTORY_MONTHS)
    event_counts = Counter(ev.get("event", "unknown") for ev in events)
    for event_type, count in event_counts.most_common():
        lines.append(f"- `{event_type}`: {count}")
    summary["history_event_counts"] = dict(event_counts)
    lines.append("")

    # --- Buffer Status ---
    lines.append("## Buffer Status")
    lines.append("")
    lines.append(get_buffer_status())
    lines.append("")

    return "\n".join(lines), summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Weekly decay-and-review report for PLUR engram memory."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only, skip decay results.",
    )
    parser.add_argument(
        "--decay-json",
        type=str,
        default=None,
        help="Path to pre-computed decay results JSON file.",
    )
    args = parser.parse_args()

    # 1. Read decay results
    decay_results = read_decay_results(args)

    # 2. Analyze recurrences from history
    events = read_history(HISTORY_MONTHS)
    recurrences = analyze_recurrences(events)

    # 3. Load engram metadata for flagged items
    engram_data = load_engram_statements()

    # 4. Generate report
    report, summary = generate_report(decay_results, recurrences, engram_data)

    # Output
    print(report)
    print(json.dumps(summary, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
