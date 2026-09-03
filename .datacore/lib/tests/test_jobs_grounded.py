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


def test_runner_manifest_matches_canonical():
    """The copy that EXECUTES must equal the copy that is edited.

    job_verify_notify.sh reads `$HOME/.datacore/v2-runner`, not `~/Data`, so on
    every host that invokes it — mac, plur-claw, hermes — the runner copy is
    what actually governs verification. Nothing synced it.

    Measured 2026-09-03: seven copies of jobs/manifest.yaml existed across the
    fleet in four different states. The canonical file carried the corrected
    `^#{2,3}\\s+Your Agenda`; mac's runner still carried the broken `^###` form
    fixed the day before, and plur-claw/hermes carried a 33-job copy older
    still. Editing the canonical file had changed what runs on exactly one of
    four hosts.

    That is bug class 1 at the deployment layer: the fix landed on the copy
    someone was looking at. This test fails when they diverge again.
    """
    canonical = ROOT / ".datacore" / "lib" / "jobs" / "manifest.yaml"
    runner = pathlib.Path.home() / ".datacore" / "v2-runner" / ".datacore" / "lib" / "jobs" / "manifest.yaml"
    if not runner.exists():
        pytest.skip("no v2-runner deployment on this host")
    assert runner.read_bytes() == canonical.read_bytes(), (
        f"the runner manifest that job_verify_notify.sh actually reads has "
        f"drifted from the canonical one.\n"
        f"  canonical: {canonical}\n"
        f"  runner:    {runner}\n"
        f"Editing the canonical file does not change what runs until this is "
        f"synced.")


# --- audit 2026-09-03: the schedule binding was a bare substring ---------------

def test_schedule_binding_rejects_a_decoy_line_with_the_same_basename():
    """`cli.py` must not be satisfied by an unrelated cron line that happens to
    mention some other cli.py. A verifier that passes on coincidence is worse
    than no verifier: it says "scheduled" about a job that is not."""
    decoy = "0 5 * * * /usr/bin/python3 /opt/other/tool/cli.py --unrelated\n"
    assert not grounded._schedule_seen("~/Data/.datacore/lib/ledger_cli.py", "ledger_cli.py", decoy)
    assert not grounded._schedule_seen("", "cli.py", "0 5 * * * /x/mycli.py\n")


def test_schedule_binding_accepts_the_path_as_written_and_the_bounded_basename():
    cron = "20 7 * * * /usr/bin/python3 ~/Data/.datacore/lib/ledger_cli.py verify > out.log\n"
    assert grounded._schedule_seen("~/Data/.datacore/lib/ledger_cli.py", "ledger_cli.py", cron)
    assert grounded._schedule_seen("", "ledger_cli.py", cron)


def test_runner_binding_reports_drift_as_fail_and_absence_as_none(tmp_path, monkeypatch):
    """The operational counterpart of test_runner_manifest_matches_canonical,
    which skips wherever there is no runner -- i.e. in every CI run."""
    canon = tmp_path / "canon.yaml"; canon.write_text("jobs: []\n")
    runner = tmp_path / "runner.yaml"
    monkeypatch.setattr(grounded, "MANIFEST", canon)
    monkeypatch.setattr(grounded, "RUNNER_MANIFEST", runner)
    assert grounded._runner_binding() is None
    runner.write_text("jobs: []\n")
    assert grounded._runner_binding()["state"] == "ok"
    runner.write_text("jobs: [drifted]\n")
    assert grounded._runner_binding()["state"] == "FAIL"


def test_remote_stat_quotes_the_path(monkeypatch):
    """grounded's --live cmd@host check sends a manifest path over ssh."""
    import shlex
    hostile = "~/Data/x y; touch /tmp/pwned.sh"
    assert shlex.quote(hostile).startswith("'"), "shlex.quote must wrap a metachar path"


def test_declared_timer_unit_binds_a_systemd_job():
    """list-timers shows units, not scripts. The manifest's declared unit is
    the only thing that can bind a timer-driven job -- and only that unit."""
    timers = ("NEXT LEFT LAST PASSED UNIT ACTIVATES\n"
              "Mon 06:10 4h Sun 18:10 8h datacore-fleet-sync.timer datacore-fleet-sync.service\n")
    assert grounded._declared_timer_seen("systemd datacore-fleet-sync.timer, 06:10 and 18:10 UTC", timers)
    assert not grounded._declared_timer_seen("systemd datacore-fleet-sync.timer", "other.timer other.service\n")
    assert not grounded._declared_timer_seen("systemd fleet-sync.timer", timers), "a shorter name is not the unit"
    assert not grounded._declared_timer_seen("cron 06:10", timers)
    assert not grounded._declared_timer_seen("systemd timer", timers), "no unit named, nothing to bind"
    assert not grounded._declared_timer_seen(None, timers)


def test_declared_launchd_label_binds_a_mac_job():
    """launchctl list shows labels, not scripts. Same rule as systemd units."""
    listing = ("PID\tStatus\tLabel\n"
               "-\t0\tcom.datacore.artifact-pull\n"
               "412\t0\tio.datacore.config-drift\n")
    assert grounded._declared_launchd_seen("every 30 min (launchd StartInterval 1800, com.datacore.artifact-pull)", listing)
    assert grounded._declared_launchd_seen("launchd io.datacore.config-drift @ 08:50, 14:50, 20:50", listing)
    assert grounded._declared_launchd_seen("every 30 min (launchd StartInterval 1800, com.datacore.artifact-pull.plist)", listing), \
        "the manifest names the plist file; launchctl lists the label"
    assert not grounded._declared_launchd_seen("launchd com.datacore.artifact", listing), "a prefix is not the label"
    assert not grounded._declared_launchd_seen("launchd com.datacore.lens-sync", listing)
    assert not grounded._declared_launchd_seen("0 7 * * *", listing)
    assert not grounded._declared_launchd_seen("launchd", listing), "no label named, nothing to bind"
    assert not grounded._declared_launchd_seen("launchd io.datacore.config-drift", None)


def test_schedule_binding_rejects_a_suffixed_or_directory_form_of_the_basename():
    """cli.py.bak and cli.py/ are not the script."""
    assert not grounded._schedule_seen("", "cli.py", "0 5 * * * /x/cli.py.bak\n")
    assert not grounded._schedule_seen("", "cli.py", "0 5 * * * /x/cli.py/run\n")
    assert grounded._schedule_seen("", "cli.py", "0 5 * * * /x/cli.py --flag\n")
