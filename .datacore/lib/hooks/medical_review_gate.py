#!/usr/bin/env python3
"""Stop hook: a medical-analysis reply does not go out without a Verification status block.

Reads the session transcript named on stdin. Looks only at the current turn (everything
after the last human prompt). If a tool call in that turn touched a person's medical record
(a path under health-people/ or the records database) and the final assistant text has no
"Verification status" block, the stop is refused with the reason. Owner, 2026-10-15, after
an analysis went out with a series claim that had not been checked against the latest row.

Fails open on every error: a broken hook must never trap a session.
"""
import json
import sys

RECORD_MARKERS = ("health-people/", "records.db", "/imports/jobs/")
STATUS_MARKER = "Verification status"


def _text_blocks(content):
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return []


def _is_human_prompt(entry):
    if entry.get("type") != "user":
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "text" for b in content)
    return False


def decide(transcript_path):
    entries = []
    with open(transcript_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    start = 0
    for i, e in enumerate(entries):
        if _is_human_prompt(e):
            start = i
    turn = entries[start:]
    touched = False
    last_text = ""
    for e in turn:
        if e.get("type") != "assistant":
            continue
        content = (e.get("message") or {}).get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    blob = json.dumps(b.get("input", {}), ensure_ascii=False)
                    if any(m in blob for m in RECORD_MARKERS):
                        touched = True
        texts = [t for t in _text_blocks(content) if t.strip()]
        if texts:
            last_text = "\n".join(texts)
    if touched and STATUS_MARKER not in last_text:
        return {
            "decision": "block",
            "reason": (
                "This turn read a person's medical record, so the reply is a medical analysis. "
                "It cannot end without the reviewer pass and the closing 'Verification status' block "
                "(see 0-personal/1-active/health-people/CLAUDE.md): list the claims with sources, spawn the "
                "Practice reviewer in analysis mode, fold in every WRONG, then end with the block."
            ),
        }
    return {}


def main():
    try:
        payload = json.loads(sys.stdin.read(4 * 1024 * 1024) or "{}")
        if payload.get("stop_hook_active"):
            return
        path = payload.get("transcript_path")
        if not path:
            return
        result = decide(path)
        if result:
            sys.stdout.write(json.dumps(result))
    except Exception:
        return


if __name__ == "__main__":
    main()
