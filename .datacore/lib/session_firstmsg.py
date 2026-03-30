#!/usr/bin/env python3
"""UserPromptSubmit hook — first message triggers engram injection, subsequent skip.

Input: JSON on stdin with {prompt, session_id, ...}
Output: JSON on stdout with {additionalContext} or empty (exit 0)

Latency: ~1ms on hot path (file existence check), ~500ms on first message.
"""
import json, sys, os
from pathlib import Path

# Get absolute paths using DATACORE_ROOT
DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
sys.path.insert(0, str(DATACORE_ROOT / ".datacore" / "lib"))
from session_state import session_exists, create_session, _debug
import subprocess

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

    # Select and inject relevant engrams via PLUR CLI
    try:
        result = subprocess.run(
            ['npx', '@plur-ai/cli', 'inject', prompt, '--json'],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            _debug(f"first message: PLUR inject failed: {result.stderr.strip()}")
            sys.exit(0)

        import json as _json
        data = _json.loads(result.stdout)
        engrams = data if isinstance(data, list) else data.get('engrams', [])
    except Exception as e:
        _debug(f"first message: engram injection error: {e}")
        sys.exit(0)

    if not engrams:
        _debug("first message: no matching engrams")
        sys.exit(0)

    lines = []
    for e in engrams:
        eid = e.get('id', '?')
        stmt = e.get('statement', e.get('text', ''))
        lines.append(f"- [{eid}] {stmt}")
    context = '\n'.join(lines)
    _debug(f"first message: injected {len(engrams)} engrams via PLUR CLI")

    # Output additionalContext for Claude
    output = {"additionalContext": f"[Datacore Active Memory — session started]\n\n{context}"}
    json.dump(output, sys.stdout)

if __name__ == "__main__":
    main()
