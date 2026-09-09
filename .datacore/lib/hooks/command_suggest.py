#!/usr/bin/env python3
"""Surface the command that already exists, at the moment the work is asked for.

WHY THIS EXISTS. On 2026-09-09 an agent was asked to do weekly planning. A
ten-step `/weekly-plan` command had been written on 2026-08-31 for exactly that,
capturing each rule with the failure that produced it. The agent looked in
`.datacore/registry/commands.yaml` — the place CLAUDE.md names as the full list
— and the command was not there, because whoever wrote it never registered it.
So the agent improvised a weekly plan, the owner recognised it as worse than the
saved method, and the session was rebuilt from scratch.

The registry gap is now closed and gated by a test. This hook closes the other
half: relying on an agent to *remember to look* is the step that keeps failing.
Here the answer arrives unasked, next to the request.

Reads the same registry an agent would. Matches the prompt against each
command's name, description and trigger. Emits nothing when nothing matches —
silence is the common case and it must stay cheap.

Fail-open: any error emits no context and exits 0. A discovery aid must never
block a prompt.

Register under UserPromptSubmit:

    {"type": "command",
     "command": "python3 ~/Data/.datacore/lib/hooks/command_suggest.py",
     "timeout": 5}
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path.home() / "Data"
REGISTRY = REPO / ".datacore" / "registry" / "commands.yaml"
MAPS = ("commands", "module_commands")
MAX_SUGGESTIONS = 4
MIN_SCORE = 2

# Words that match everything and therefore discriminate nothing.
STOP = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "is", "it",
    "this", "that", "with", "we", "i", "you", "me", "my", "our", "do", "does",
    "can", "should", "would", "get", "got", "let", "lets", "run", "make",
    "command", "please", "now", "then", "up", "out", "what", "how", "check",
}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9-]{2,}", text.lower())
            if w not in STOP}


def _load() -> list[tuple[str, dict]]:
    try:
        import yaml
        doc = yaml.safe_load(REGISTRY.read_text()) or {}
    except Exception:
        return []
    out = []
    for section in MAPS:
        m = doc.get(section)
        if isinstance(m, dict):
            out += [(k, v) for k, v in m.items() if isinstance(v, dict)]
    return out


def _overlap(a: set[str], b: set[str]) -> int:
    """Count matches, treating one token as matching another when either is a
    prefix of the other from four characters. Exact-set intersection missed the
    case this hook exists for: the request said "weekly planning" and the entry
    said "Shape the week ... sequence" — week/weekly and plan/planning never
    met, so /weekly-plan scored zero on its own motivating example."""
    hits = 0
    for x in a:
        for y in b:
            if x == y or (len(x) >= 4 and len(y) >= 4 and (x.startswith(y) or y.startswith(x))):
                hits += 1
                break
    return hits


def _score(prompt_tokens: set[str], key: str, entry: dict) -> int:
    """Name and trigger are what the author chose as the handle; weight them
    above the description, which is prose and matches loosely."""
    name = f"{key} {entry.get('name') or ''}"
    trigger = str(entry.get("trigger") or "").replace("|", " ")
    desc = str(entry.get("description") or "")

    score = 0
    score += 3 * _overlap(prompt_tokens, _tokens(name))
    score += 3 * _overlap(prompt_tokens, _tokens(trigger))
    score += 1 * _overlap(prompt_tokens, _tokens(desc))

    # An exact multi-word trigger phrase appearing in the prompt is decisive.
    for phrase in str(entry.get("trigger") or "").split("|"):
        phrase = phrase.strip().lower()
        if len(phrase) > 6 and phrase in _PROMPT_LOWER:
            score += 6
    return score


_PROMPT_LOWER = ""


def main() -> int:
    global _PROMPT_LOWER
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    prompt = str(payload.get("prompt") or payload.get("user_prompt") or "")
    if len(prompt) < 8:
        return 0
    _PROMPT_LOWER = prompt.lower()

    # Already naming a command explicitly? Then discovery is not the problem.
    if re.search(r"(^|\s)/[a-z][a-z0-9:-]+", prompt):
        return 0

    tokens = _tokens(prompt)
    if not tokens:
        return 0

    scored = []
    for key, entry in _load():
        s = _score(tokens, key, entry)
        if s >= MIN_SCORE:
            scored.append((s, key, entry))
    if not scored:
        return 0

    scored.sort(key=lambda r: (-r[0], r[1]))
    top = scored[:MAX_SUGGESTIONS]

    lines = ["## Commands that already exist for this",
             "",
             "Registered commands matching this request. Read the command file "
             "before improvising an equivalent — these carry decisions and "
             "failures that a fresh approach will not.",
             ""]
    for s, key, entry in top:
        desc = str(entry.get("description") or "").strip().splitlines()
        desc = desc[0] if desc else ""
        src = entry.get("source") or ""
        lines.append(f"- **/{key}** — {desc}" + (f"  \n  `{src}`" if src else ""))

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
