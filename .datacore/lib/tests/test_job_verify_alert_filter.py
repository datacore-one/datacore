"""The wrapper relays what job_verify decided the operator should see, nothing else."""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from job_verify_alert_filter import operator_facing  # noqa: E402

WITHHELD = """\
job 'mac-suite-audit' FAILED:
  - ~/.datacore/state/suite-audit.log: last line does not match '0 unexplained' -- last line: '8648 passed, 9 unexplained'
alert withheld: mac-suite-audit failed on the same artifact already counted (1x); nothing new to report
"""

DELEGATED = """\
recurrence: forgot mac-seq-gap (no longer in the manifest)
job 'nightshift-overnight' FAILED:
  - ~/Data/.datacore/state/nightshift/manifest-latest.yaml: stale (age 29.14h exceeds max_age_hours=26)
delegated repair of nightshift-overnight (autofix-nightshift-overnight-20260922); operator not alerted
"""

ESCALATED = """\
job 'mac-drills' FAILED:
  - ~/.datacore/state/drills.log: last line does not match 'drills: 1/1 held' -- last line: 'drills: 3/3 held'
could NOT delegate mac-drills (autofix unavailable: ModuleNotFoundError: No module named 'fix_check'); escalating to the operator
alert: job.verify FAILED: mac-drills (1 failure(s))
"""


def test_a_withheld_alert_is_not_relayed():
    """Relayed eleven times overnight on 2026-09-22, every copy marked withheld."""
    assert operator_facing(WITHHELD) == ""


def test_a_delegated_repair_is_not_relayed_and_housekeeping_is_dropped():
    assert operator_facing(DELEGATED) == ""


def test_an_escalation_is_relayed_with_its_detail():
    out = operator_facing(ESCALATED)
    assert out.startswith("job 'mac-drills' FAILED:") and "drills: 3/3 held" in out
    assert "could NOT delegate" in out and out.rstrip().endswith("alert: job.verify FAILED: mac-drills (1 failure(s))")


def test_mixed_output_keeps_only_the_loud_block():
    out = operator_facing(WITHHELD + DELEGATED + ESCALATED + "OK 12 jobs 12 artifacts\n")
    assert "mac-suite-audit" not in out and "nightshift-overnight" not in out and "OK 12 jobs" not in out
    assert "job 'mac-drills' FAILED:" in out


def test_a_verifier_that_could_not_run_is_always_relayed():
    """Not a block, not a decision: the verifier itself is broken. That must reach a person."""
    assert "ManifestError" in operator_facing("ManifestError: jobs/manifest.yaml: duplicate job name 'x'\n")
    assert operator_facing("Traceback (most recent call last):\n  File x\nKeyError: 'y'\n").count("\n") == 2


def test_a_recovery_alone_is_not_an_operator_alert():
    from job_verify_alert_filter import operator_facing
    out = ("recovered: task 0a96 closed for mac-artifact-pull\n"
           "job 'mac-suite-audit' FAILED:\n  - suite-audit.log: last line does not match\n"
           "alert withheld: mac-suite-audit failed on the same artifact already counted (21x)\n")
    assert operator_facing(out).strip() == ""
