#!/usr/bin/env python3
"""UserPromptSubmit hook — first message triggers engram injection, subsequent skip.

Input: JSON on stdin with {prompt, session_id, ...}
Output: JSON on stdout with {additionalContext} or empty (exit 0)

Latency: ~1ms on hot path (file existence check), ~500ms on first message.
"""
import json, sys, os

sys.path.insert(0, os.path.join(os.path.expanduser("~/Data"), ".datacore", "lib"))
from session_state import session_exists, create_session, _debug
from engram_selector import select_engrams, format_injection

def main():
    # Hot path: session already started → exit immediately (~1ms)
    if session_exists():
        sys.exit(0)

    # Cold path: first message → bootstrap session
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        # Malformed input — still create session to avoid re-triggering
        create_session("")
        sys.exit(0)

    prompt = input_data.get("prompt", "")

    # Always create session state, even with empty prompt
    create_session(prompt)

    if not prompt:
        _debug("first message: empty prompt, session created without injection")
        sys.exit(0)

    # Select and inject relevant engrams
    engrams = select_engrams(scope="global", task_desc=prompt, limit=15)
    if not engrams:
        _debug("first message: no matching engrams")
        sys.exit(0)

    context = format_injection(engrams)
    _debug(f"first message: injected {len(engrams)} engrams")

    # Output additionalContext for Claude
    output = {"additionalContext": f"[Datacore Active Memory — session started]\n\n{context}"}
    json.dump(output, sys.stdout)

if __name__ == "__main__":
    main()
