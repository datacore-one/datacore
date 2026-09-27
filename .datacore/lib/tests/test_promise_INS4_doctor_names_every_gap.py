"""INS-4: "One doctor command tells me everything that is missing or
misconfigured, and how to fix each item."

Kind: deterministic, on a fresh install built by following INSTALL.md (see
_fresh_install.py). The install is left with the gaps a new team really has
after the guide -- the identity still the guide's placeholder `your-name`, no
principals, a personal space the guide created but never labelled (no space
config saying what it is), and a job manifest naming machines that are not in
this install -- and `datacore doctor --format json` (the CLI's doctor,
`@datacore-one/cli`) is run against it (DATACORE_ROOT).

Owner decision 2026-09-27: since the guide now creates the personal inbox, the
identity file and the personal event log (INSTALL.md step 2), the eval tests the
gaps that remain after it: the placeholder name and the unlabelled space. The
identity gap must name the placeholder itself, so a doctor that merely mentions
"actor" somewhere does not pass by accident.

Every one of those gaps must appear as a not-ok item, and every not-ok item
must carry a fix (a command or instruction). A doctor that says "could not
tell" (ok: null) about a gap has not told me what is missing.

Seeded failure: doctor checks dependencies only (the state on 2026-09-26:
identity, principals, ledger and jobs are not covered).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fresh_install as F  # noqa: E402

GAPS = {
    "placeholder identity": ("your-name", "placeholder"),
    "principals": ("principal",),
    "unlabelled personal space": ("0-personal",),
    "jobs": ("job", "manifest", "schedule", "cron"),
}
FIX_KEYS = ("fix", "installCommand", "hint", "remedy", "howToFix", "command")


def _items(node, path=""):
    """Every dict in the report that carries a verdict."""
    if isinstance(node, dict):
        if any(k in node for k in ("ok", "installed", "status")) and ("name" in node or "detail" in node):
            yield path, node
        for k, v in node.items():
            yield from _items(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _items(v, f"{path}[{i}]")


def _not_ok(item: dict) -> bool:
    return item.get("ok") in (False, None) if "ok" in item else item.get("installed") is False


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    if not shutil.which("datacore"):
        pytest.fail("no `datacore` CLI on PATH: the doctor cannot be run (could not tell is not a pass)")
    inst = F.follow_guide(tmp_path_factory.mktemp("ins4"))
    p = inst.run("datacore doctor --format json", timeout=50)
    text = p.stdout
    assert "{" in text, f"doctor gave no JSON (rc {p.returncode}): {(p.stdout + p.stderr)[-400:]}"
    return json.loads(text[text.index("{"):])


@pytest.mark.parametrize("gap", sorted(GAPS))
def test_doctor_reports_the_gap(report, gap):
    words = GAPS[gap]
    flagged = [(path, it) for path, it in _items(report) if _not_ok(it)
               and any(w in json.dumps(it).lower() for w in words)]
    assert flagged, f"doctor says nothing is wrong with {gap!r} on a fresh install:\n{json.dumps(report)[:1500]}"


def test_every_problem_carries_a_fix(report):
    bare = [f"{path}: {json.dumps(it)[:120]}" for path, it in _items(report)
            if _not_ok(it) and not any(it.get(k) for k in FIX_KEYS)]
    assert bare == [], "doctor names problems without saying how to fix them:\n" + "\n".join(bare)
