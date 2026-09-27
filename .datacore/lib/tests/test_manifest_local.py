"""INS-3 final batch: this install's own jobs live in the gitignored
jobs/manifest.local.yaml, laid over the tracked jobs/manifest.yaml, and every
reader of the job list sees the same effective list.

A local job replaces the tracked job of the same name; a local-only job is
added. Every test builds its own scratch tree and roster, so the real
install's lists are never read.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from jobs import manifest as jm  # noqa: E402


def _job(name, machine="srv", cmd="true", **extra):
    return {"name": name, "machine": machine, "schedule": "0 3 * * *", "cmd": cmd,
            "artifacts": [{"path": f"/tmp/{name}.log", "check": "regex", "arg": "OK",
                           "max_age_hours": 26}], **extra}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A scratch install: roster declaring srv and edge, a tracked list, a local list."""
    reg = tmp_path / ".datacore" / "registry"
    reg.mkdir(parents=True)
    (reg / "infrastructure.yaml").write_text("servers:\n  srv: {}\n  edge: {}\n")
    jobs = tmp_path / ".datacore" / "lib" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "manifest.yaml").write_text(yaml.safe_dump({"version": 1, "jobs": [
        _job("shared", cmd="neutral.sh"), _job("tracked-only")]}))
    (jobs / "manifest.local.yaml").write_text(yaml.safe_dump({"version": 1, "jobs": [
        _job("shared", cmd="ours.sh --space ours"), _job("ours-only", machine="edge")]}))
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path))
    return tmp_path


def _names(doc):
    return [j["name"] for j in doc["jobs"]]


def test_local_replaces_by_name_and_adds_its_own(tree):
    doc = jm.effective_doc(tree / ".datacore/lib/jobs/manifest.yaml")
    assert _names(doc) == ["shared", "tracked-only", "ours-only"]
    assert doc["jobs"][0]["cmd"] == "ours.sh --space ours"
    assert doc["version"] == 1


def test_load_manifest_validates_the_effective_list(tree):
    jobs = jm.load_manifest(tree / ".datacore/lib/jobs/manifest.yaml")
    assert {j.name: j.machine for j in jobs} == {"shared": "srv", "tracked-only": "srv", "ours-only": "edge"}


def test_the_local_path_gets_the_tracked_list_underneath_for_a_fleet_member(tree):
    doc = jm.effective_doc(tree / ".datacore/lib/jobs/manifest.local.yaml")
    assert _names(doc) == ["shared", "tracked-only", "ours-only"]


def test_a_stranger_gets_only_its_own_list(tree):
    (tree / ".datacore/registry/infrastructure.yaml").write_text("servers:\n  laptop: {}\n")
    doc = jm.effective_doc(tree / ".datacore/lib/jobs/manifest.local.yaml")
    assert _names(doc) == ["shared", "ours-only"]


def test_without_a_local_list_the_tracked_one_is_unchanged(tree):
    (tree / ".datacore/lib/jobs/manifest.local.yaml").unlink()
    doc = jm.effective_doc(tree / ".datacore/lib/jobs/manifest.yaml")
    assert _names(doc) == ["shared", "tracked-only"]


def test_a_scratch_manifest_elsewhere_never_picks_up_the_installs_list(tree, tmp_path_factory):
    other = tmp_path_factory.mktemp("elsewhere") / "manifest.yaml"
    other.write_text(yaml.safe_dump({"version": 1, "jobs": [_job("fixture")]}))
    assert _names(jm.effective_doc(other)) == ["fixture"]


def test_the_codes_own_list_finds_the_local_list_under_the_data_root(tree, tmp_path_factory, monkeypatch):
    """A runner checkout carries tracked files only; the install's list is in its data tree."""
    runner = tmp_path_factory.mktemp("runner") / "manifest.yaml"
    runner.write_text((tree / ".datacore/lib/jobs/manifest.yaml").read_text())
    monkeypatch.setattr(jm, "OWN_TRACKED", runner)
    assert _names(jm.effective_doc(runner)) == ["shared", "tracked-only", "ours-only"]


def test_a_broken_local_list_is_a_manifest_error(tree):
    (tree / ".datacore/lib/jobs/manifest.local.yaml").write_text("jobs: [\n")
    with pytest.raises(jm.ManifestError):
        jm.effective_doc(tree / ".datacore/lib/jobs/manifest.yaml")


def test_the_cli_prints_the_effective_list(tree, capsys):
    assert jm._cli(["jobs", str(tree / ".datacore/lib/jobs/manifest.yaml")]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["shared\tsrv\tours.sh --space ours", "tracked-only\tsrv\ttrue", "ours-only\tedge\ttrue"]


# ── every reader sees the local jobs ─────────────────────────────────────────

def test_job_verify_default_sees_the_whole_list(tree, monkeypatch):
    import job_verify
    monkeypatch.setattr(job_verify, "DATACORE_ROOT", tree)
    path = job_verify._default_manifest_path()
    assert {j.name for j in jm.load_manifest(path)} == {"shared", "tracked-only", "ours-only"}


def test_fixtures_grounded_and_dashboard_read_the_effective_list(tree, monkeypatch):
    from jobs import fixtures, grounded
    import reliability_dashboard
    tracked = tree / ".datacore/lib/jobs/manifest.yaml"
    monkeypatch.setattr(fixtures, "MANIFEST", tracked)
    monkeypatch.setattr(grounded, "MANIFEST", tracked)
    monkeypatch.setattr(reliability_dashboard, "MANIFEST", tracked)
    assert {c[0] for c in fixtures.regex_checks()} == {"shared", "tracked-only", "ours-only"}
    assert "ours-only" in {r.get("job") for r in grounded.check(machine="edge")}
    assert reliability_dashboard.jobs()["by_machine"]["edge"]["total"] == 1


def test_contract_sha_hashes_the_effective_entry(tree):
    from jobs.fix_check import contract_sha
    tracked = tree / ".datacore/lib/jobs/manifest.yaml"
    assert contract_sha("ours-only", tracked)
    before = contract_sha("shared", tracked)
    (tree / ".datacore/lib/jobs/manifest.local.yaml").unlink()
    assert contract_sha("shared", tracked) != before, "the local entry is the contract that runs"


def test_visitor_duties_principals_and_doctor_see_local_jobs(tree):
    import install_doctor
    import principals_check
    import visitor_join
    local = tree / ".datacore/lib/jobs/manifest.local.yaml"
    doc = yaml.safe_load(local.read_text())
    doc["jobs"].append({**_job("ours-duty", machine="edge"), "trigger": "join"})
    local.write_text(yaml.safe_dump(doc))
    assert visitor_join.duties({"join"}, machine="edge",
                               manifest=tree / ".datacore/lib/jobs/manifest.yaml") == ["ours-duty"]
    assert "ours-only" in principals_check._manifest_jobs(tree)
    item = install_doctor.check_jobs(tree)
    assert item["ok"] is True and "4 job(s)" in item["detail"], item


def test_module_removal_takes_its_job_out_of_the_local_list_too(tree):
    import module_remove
    local = tree / ".datacore/lib/jobs/manifest.local.yaml"
    doc = yaml.safe_load(local.read_text())
    doc["jobs"].append(_job("mod-job", cmd="~/Data/.datacore/modules/leaving/run.sh"))
    local.write_text(yaml.safe_dump(doc))
    mod = tree / ".datacore/modules/leaving"
    mod.mkdir(parents=True)
    (mod / "module.yaml").write_text("name: leaving\nschedules: [mod-job]\n")
    rc = module_remove.main(["leaving"])   # the root is $DATACORE_ROOT, the scratch tree
    assert rc == 0
    assert "mod-job" not in _names(yaml.safe_load(local.read_text()))
    assert _names(yaml.safe_load(local.read_text())) == ["shared", "ours-only"]
