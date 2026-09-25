#!/usr/bin/env python3
"""What job_verify_notify.sh may relay: the operator-facing part, and only that.

The wrapper runs job_verify with `--alert log` and used to relay its ENTIRE
output whenever the exit code was non-zero. job_verify's own decisions -- "alert
withheld: same artifact already counted", "delegated repair ...; operator not
alerted", "alert suppressed: recurring, already escalated today" -- were
printed, logged, and then relayed anyway, because the wrapper never read them.
Measured 2026-09-22: one mac-suite-audit failure was relayed eleven times
between 03:35 and 07:07, every line of it marked "withheld". Every dedup,
delegation and escalation rule in job_verify was in force, and none of them
reached the phone.

job_verify's output is blocks. A block is "job 'X' FAILED:" followed by its
"  - detail" lines and then one decision line. This keeps a block only when its
decision is `alert: ...` -- the one line job_verify prints when the OPERATOR is
the intended reader -- and drops housekeeping ("recurrence: forgot ...") and
"OK N jobs". Anything else that is not part of a block (a broken manifest, a
crash of the verifier itself) is kept: unknown output from a failing verifier
is exactly what must reach a person.

    job_verify_alert_filter.py < job_verify_output   ->  relayable text, or nothing
"""
from __future__ import annotations

import sys

#: Lines that begin a failure block.
_BLOCK_START = "job '"
#: Decision lines that END a block. The first token decides.
_QUIET = ("alert withheld:", "delegated repair of", "alert suppressed:")
_LOUD = ("alert:",)
#: Lines inside or beside a block that carry no decision.
#: "recovered: task ... closed" is bookkeeping -- the repair task of a failure the
#: operator was never told about. Relayed alone it arrived as "job_verify FAILED
#: on mac" (2026-09-25) for a job that had just come right.
_NOISE = ("recurrence: forgot ", "recurring: filed task ", "could NOT delegate ", "sent ", "recovered: task ")


def operator_facing(text: str) -> str:
    kept: list[str] = []
    block: list[str] = []
    in_block = False

    def close(loud: bool) -> None:
        nonlocal block, in_block
        if loud:
            kept.extend(block)
        block, in_block = [], False

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(_BLOCK_START) and s.endswith("FAILED:"):
            if in_block:
                close(False)          # a block with no decision line is not for the operator
            in_block, block = True, [line]
            continue
        if in_block:
            if s.startswith("- ") or s.startswith("could NOT delegate "):
                block.append(line)
                continue
            if s.startswith(_LOUD):
                block.append(line)
                close(True)
                continue
            if s.startswith(_QUIET):
                close(False)
                continue
            close(False)              # an unexpected line ends the block; judged on its own below
        if s.startswith("OK ") and " jobs " in s:
            continue
        if s.startswith(_NOISE):
            continue
        if s.startswith(_LOUD):
            kept.append(line)         # an alert with no block above it
            continue
        if s.startswith(_QUIET):
            continue
        kept.append(line)             # the verifier itself is complaining: relay it
    if in_block:
        close(False)
    return "\n".join(kept)


if __name__ == "__main__":
    out = operator_facing(sys.stdin.read())
    if out:
        sys.stdout.write(out + "\n")
