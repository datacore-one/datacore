"""Every module command must be reachable through the registry.

On 2026-08-31 an agent captured the weekly-planning method as
`.datacore/modules/chief-of-staff/commands/weekly-plan.md` and declared it in
that module's module.yaml, but did not add it to the registry (commit 050d441
touched two files; the registry was not one). On 2026-09-09 another agent asked
to plan the week looked in the registry — the place CLAUDE.md names as the full
list — found nothing, and improvised instead of running the ten-step command
that existed for exactly that. The whole planning session was rebuilt afterwards.

An audit that morning found 36 module commands unreachable the same way,
including entire modules: metacognition (9), ventures (5), megaphone (4),
lens (3).

This test is the gate. A new command file that nobody registers fails here
rather than three weeks later in someone's planning session.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AUDIT = REPO / ".datacore" / "lib" / "command_registry_audit.py"


def test_every_module_command_is_registered():
    r = subprocess.run([sys.executable, str(AUDIT)], capture_output=True, text=True)
    assert r.returncode == 0, (
        "A module command is unreachable through the registry.\n"
        "Fix with: python3 .datacore/lib/command_registry_audit.py --fix\n\n"
        + r.stdout + r.stderr
    )


def test_the_audit_reads_both_registry_maps():
    """The registry keeps commands in `commands:` AND `module_commands:`.
    Reading one reported 85 missing when 36 were. Guard the guard."""
    src = AUDIT.read_text()
    assert '"commands", "module_commands"' in src


def test_the_audit_accepts_every_key_shape_in_use():
    """Bare name, <module>-<command>, and <module>:<command> are all live in the
    registry today. Matching only one overstates the gap by 16."""
    src = AUDIT.read_text()
    assert 'f"{mod}-{name}"' in src and 'f"{mod}:{name}"' in src
