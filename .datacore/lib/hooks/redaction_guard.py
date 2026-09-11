#!/usr/bin/env python3
"""UserPromptSubmit hook — re-assert redaction rules in context EVERY turn.

WHY THIS EXISTS
---------------
2026-09-07: during a live demo, a customer name, their invoice timing, and
infrastructure health were narrated to an audience. Four engrams forbidding
exactly that were injected at session start and never read:

    ENG-2026-08-20-064  DEMO MODE — redact before displaying
    ENG-2026-0802-015   never name a client unless the user raises them first
    ENG-2026-0802-014   never name enterprise customers in any artifact
    ENG-2026-0724-004   customer-named work runs in a dedicated session

They were not missing. They were at char offsets 35k-55k of a 115,052-char
plur_session_start payload that exceeded the tool-result limit, got spilled to
a file, and was read at 4%.

The lesson is NOT "read more carefully". A safety rule delivered inside a
100KB blob is a safety rule that will eventually be skipped. This hook makes
the redaction rules impossible to truncate away: a small block, re-injected on
every single prompt, that costs ~800 chars and cannot fail open.

DESIGN CONSTRAINTS
------------------
- No network, no PLUR CLI call, no imports beyond stdlib. A safety block that
  can time out is a safety block that fails open. The text is distilled from
  the engrams above and carries their IDs so drift is auditable.
- Always fires. The client-name rule is unconditional, not demo-only.
- Demo/screen-share detection ESCALATES the block; it never gates it.

Input:  JSON on stdin {prompt, session_id, ...}
Output: JSON on stdout with hookSpecificOutput.additionalContext
"""
import json
import re
import sys

# Sourced from the engrams named above. Keep IDs attached: if these rules are
# edited, the engrams must be edited too, and vice versa.
BASE_RULES = """\
<hard-redaction-rules source="ENG-2026-0802-015,ENG-2026-0802-014,ENG-2026-0724-004">
NEVER name a client, customer, or counterparty unless the user has raised that
name first in the current conversation. This is unconditional — it holds in
private repos, internal docs, commit messages, journals and chat alike. Say
"the customer", "an enterprise deployment", "the pilot". Naming an account to
illustrate impact, severity, urgency or who is affected is exactly the
prohibited case, not an exception to it.

Commercial detail attached to a named or nameable party — invoices, amounts,
dates, terms, strategy, internal assessments — is subject to the same rule.
</hard-redaction-rules>"""

DEMO_RULES = """\
<demo-mode-active source="ENG-2026-08-20-064">
This session looks like a live demo, screen-share, recording, or presentation.
REDACT BEFORE DISPLAYING. Do not print, quote, or render:
  - client/customer names, or any scope description carrying commercial terms,
    pricing, strategy, or internal assessment of a third party
  - operational state: service health, outage/reachability status, queue and
    outbox depths, retry counts, token expiry, permission gaps, deploy state,
    store inventories, per-scope volumes, org/team topology
  - credential-broker mechanics: index paths, secret file locations, sync or
    distribution commands, rotation practice
  - internal hostnames, usernames, absolute home paths, private/VPN IPs,
    git remote URLs
  - verbatim engram text about security posture

Introspection tools (plur_doctor, plur_outbox, plur_stores_list, plur_status,
plur_history, unscoped plur_recall) emit the above BY DEFAULT. Do not run them
on screen. Demonstrate BEHAVIOUR against a scope prepared for the demo.

An audience cannot tell a normal degraded state from a broken product: any
operational detail reads as a defect and becomes the story. Anything
operational you notice is a PRIVATE follow-up afterwards, never narrated live.
</demo-mode-active>"""

DEMO_PAT = re.compile(
    r"\b(demo|demoing|demo'?d|screen[\s-]?shar\w*|screensharing|recording|"
    r"presenting|presentation|live\s+session|webinar|walkthrough|"
    r"on\s+a\s+call|audience|prospect\s+call)\b",
    re.I,
)

# Once a session has been flagged as a demo, it stays flagged. A demo does not
# stop being a demo because the next prompt lacks the word.
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hook_state import state_path
from file_utils import atomic_write_text


def _sticky_path(sid: str) -> str:
    return str(state_path("demo-mode", sid))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        data = {}

    prompt = data.get("prompt", "") or ""
    sid = data.get("session_id", "") or ""
    path = _sticky_path(sid)

    demo = False
    try:
        import os

        if os.path.exists(path):
            demo = True
        elif DEMO_PAT.search(prompt):
            demo = True
            atomic_write_text(Path(path), "1")
    except OSError:
        # Sticky state is an optimisation. Fall back to per-prompt detection
        # rather than failing open on the rules themselves.
        demo = demo or bool(DEMO_PAT.search(prompt))

    context = BASE_RULES if not demo else BASE_RULES + "\n\n" + DEMO_RULES

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context,
                }
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
