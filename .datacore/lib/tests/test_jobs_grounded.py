"""Conformance tests for DIP-0035 — the binding it left out.

DIP-0035 built the unified verifier and is marked Implemented. Its own body
puts this binding out of scope: adding a job means "add one entry to
jobs/manifest.yaml", a convention with nothing behind it. Two consequences
were live on 2026-09-02 and had been failing daily:

    box-ledger-verify   checks a log written once, on 2026-08-31, by something
                        no longer present. The only file in .datacore/lib that
                        mentions it is manifest.yaml itself.
    box-registry-gc     names a script that exists and is in no crontab.
    box-briefing        checked `^###\\s+Your Agenda` while the interactive
                        /today generator writes H2. Red for six days against
                        briefings that were complete.

These tests are what lets DIP-0035's status be DERIVED rather than asserted:
they are the executable counterpart dip_conformance.py looks for. Static only,
so they run in CI with no host reachable — the --live bindings are operational
checks, not build gates.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / ".datacore" / "lib" / "jobs" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


grounded = _load("grounded")
fixtures = _load("fixtures")


def _fails(rows):
    return [r for r in rows if r["state"] == "FAIL"]


def test_every_job_cmd_resolves():
    """A job may not name a script that does not exist.

    `n-a` is not a pass and not a failure: a cmd on another machine cannot be
    verified from here, and absence proves nothing. Only FAIL gates the build.
    """
    bad = [r for r in _fails(grounded.check()) if r["binding"] == "cmd"]
    assert not bad, "jobs naming a script that does not exist:\n" + "\n".join(
        f"  {r['job']}: {r['detail']}" for r in bad)


def test_no_freshness_check_without_a_producer():
    """This is the box-ledger-verify bug, as a build failure.

    A `max_age_hours` check on an artifact whose producer does not exist can
    never pass, and its daily alert is indistinguishable from a job that ran
    and found real problems.
    """
    bad = [r for r in _fails(grounded.check()) if r["binding"] == "orphan"]
    assert not bad, "freshness checks with no producer:\n" + "\n".join(
        f"  {r['job']}: {r['detail']}" for r in bad)


def test_production_code_is_versioned_or_deliberately_ignored():
    """Untracked is a defect; gitignored is a decision.

    .gitignore:312 reads "machine-local operational scripts — private, never
    tracked here", so fourteen cos_*.sh jobs are policy and report as such.
    Anything untracked and NOT ignored is unversioned production code.
    """
    bad = [r for r in _fails(grounded.check()) if r["binding"] == "vcs"]
    assert not bad, "unversioned production code:\n" + "\n".join(
        f"  {r['job']}: {r['detail']}" for r in bad)


@pytest.mark.parametrize(
    "job,machine,idx,path,pattern", fixtures.regex_checks(),
    ids=lambda v: str(v) if isinstance(v, (str, int)) else "")
def test_regex_check_has_a_fixture_it_can_match(job, machine, idx, path, pattern):
    """Every `check: regex` is a claim about another program's output format.

    Until now it was the only claim in the system with no counterpart to check
    against, which is why box-briefing could ask for a heading level its
    producer has never emitted and stay red for six days. The fixture answers
    one question — can this regex ever match this producer? — and deliberately
    not "is the system healthy", which is the alert's job.
    """
    import re
    f = ROOT / ".datacore" / "lib" / "tests" / "fixtures" / "jobs" / f"{job}.{idx}.txt"
    assert f.exists(), (
        f"{job} artifact[{idx}] has no fixture. Capture one with:\n"
        f"  python3 .datacore/lib/jobs/fixtures.py --harvest")
    body = f.read_text().split("# ---\n", 1)[-1]
    assert re.search(pattern, body, re.M), (
        f"{job} artifact[{idx}]: regex {pattern!r} cannot match a real sample of\n"
        f"{path} on {machine}. Either the regex is wrong or the producer changed\n"
        f"its output format. This is the box-briefing failure.")
