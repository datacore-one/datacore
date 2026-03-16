#!/usr/bin/env python3
"""PreToolUse guard for org-mode files (DIP-0024, DIP-0009).

Fires on Edit|Write. Checks if target file is *.org.
If yes: injects additionalContext warning to use org_workspace_adapter.py.
If no: exits immediately (~1ms).

On malformed input: outputs warning (fail closed per DIP-0024 security).
"""
import json, sys

GTD_WARNING = (
    "[Datacore GTD Guard]\n\n"
    "You are about to directly edit an org-mode file. "
    "Org-mode files are managed by the GTD pipeline.\n\n"
    "REQUIRED: Use org_workspace_adapter.py instead of raw Edit/Write:\n"
    "  python3 .datacore/lib/org_workspace_adapter.py add --file <file> --heading <title> --tags <tags>\n"
    "  python3 .datacore/lib/org_workspace_adapter.py complete --file <file> --match <title>\n\n"
    "For new tasks: capture to inbox.org first, then process via gtd-inbox-processor.\n"
    "Direct edits bypass ID generation, duplicate checking, and GTD routing."
)

def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        # Fail closed: malformed input → output warning anyway
        json.dump({"additionalContext": "[Datacore GTD Guard] Warning: could not parse hook input. If editing org files, use org_workspace_adapter.py."}, sys.stdout)
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path.endswith(".org"):
        sys.exit(0)

    json.dump({"additionalContext": GTD_WARNING}, sys.stdout)

if __name__ == "__main__":
    main()
