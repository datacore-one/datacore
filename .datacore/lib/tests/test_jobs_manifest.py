"""Tests for jobs.manifest - job manifest schema + loader.

`load_manifest` is strict about shape but tolerant about extras: unknown
top-level or per-job keys are ignored (forward compatibility), while
wrong-typed or missing required keys are collected into a single
`ManifestError` -- every problem in the manifest, not just the first one
found.
"""

from pathlib import Path

import pytest
import yaml

from jobs.manifest import MACHINES, Artifact, Job, ManifestError, load_manifest

# The real manifest lives at .datacore/lib/jobs/manifest.yaml; this test
# file lives at .datacore/lib/tests/. Resolve relative to this file rather
# than assuming a cwd or DATACORE_ROOT, so the test works regardless of
# where pytest is invoked from.
REAL_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "jobs" / "manifest.yaml"


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


# --- valid manifests ---------------------------------------------------


def test_valid_manifest_round_trips_all_fields(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "backup-notes",
                "machine": "mac",
                "schedule": "0 3 * * *",
                "cmd": "restic backup ~/Data",
                "artifacts": [{"path": "/tmp/backup.log"}],
            },
            {
                "name": "sync-check",
                "machine": "nightshift",
                "schedule": "*/15 * * * *",
                "cmd": "python3 sync.py",
                "artifacts": [
                    {
                        "path": "/tmp/status.json",
                        "check": "json_has_keys",
                        "arg": ["ok", "ts"],
                        "max_age_hours": 2.5,
                    },
                    {"path": "/tmp/status.log", "check": "regex", "arg": r"^OK\b"},
                ],
                "required_env": ["HOME", "PATH"],
                "on_fail": "telegram",
            },
        ],
    }
    path = _write(tmp_path, data)

    jobs = load_manifest(path)

    assert len(jobs) == 2

    j1 = jobs[0]
    assert j1 == Job(
        name="backup-notes",
        machine="mac",
        schedule="0 3 * * *",
        cmd="restic backup ~/Data",
        artifacts=[Artifact(path="/tmp/backup.log")],
    )
    # defaults land correctly
    assert j1.required_env == []
    assert j1.on_fail == "log"
    assert j1.artifacts[0].check == "exists"
    assert j1.artifacts[0].max_age_hours is None
    assert j1.artifacts[0].arg is None

    j2 = jobs[1]
    assert j2.name == "sync-check"
    assert j2.machine == "nightshift"
    assert j2.required_env == ["HOME", "PATH"]
    assert j2.on_fail == "telegram"
    assert len(j2.artifacts) == 2
    assert j2.artifacts[0] == Artifact(
        path="/tmp/status.json", check="json_has_keys", max_age_hours=2.5, arg=["ok", "ts"]
    )
    assert j2.artifacts[1] == Artifact(path="/tmp/status.log", check="regex", arg=r"^OK\b")


def test_unknown_top_level_and_per_job_keys_are_ignored(tmp_path):
    data = {
        "version": 1,
        "extra_top_level_thing": {"whatever": True},
        "jobs": [
            {
                "name": "job-a",
                "machine": "box",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x", "description": "unused extra key"}],
                "notes": "this key is not in the schema",
            }
        ],
    }
    path = _write(tmp_path, data)

    jobs = load_manifest(path)

    assert len(jobs) == 1
    assert jobs[0].name == "job-a"


# --- version ------------------------------------------------------------


def test_missing_version_is_error(tmp_path):
    data = {
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
            }
        ]
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    assert "version" in str(exc_info.value)


def test_wrong_version_is_error(tmp_path):
    data = {
        "version": 2,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    assert "version" in str(exc_info.value)


# --- per-job required fields ---------------------------------------------


def test_missing_name_machine_cmd_all_reported_in_one_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "schedule": "daily",
                "artifacts": [{"path": "/tmp/x"}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "missing required field 'name'" in msg
    assert "missing required field 'machine'" in msg
    assert "missing required field 'cmd'" in msg
    # one problem per line, none swallowed
    assert len(msg.splitlines()) == 3


def test_empty_string_name_is_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "name" in msg
    # unnamed (empty string doesn't count as a usable name) -- falls back
    # to positional identification, just like a wholly missing name
    assert "job #0" in msg


def test_non_string_machine_is_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": 123,
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "a" in msg
    assert "machine" in msg
    assert "123" in msg


def test_empty_string_cmd_is_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "",
                "artifacts": [{"path": "/tmp/x"}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "a" in msg
    assert "cmd" in msg


def test_unknown_machine_is_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "spaceship",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "a" in msg
    assert "spaceship" in msg
    assert "machine" in msg


def test_unknown_on_fail_is_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
                "on_fail": "page-me",
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "a" in msg
    assert "page-me" in msg
    assert "on_fail" in msg


def test_job_without_artifacts_missing_key_is_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {"name": "a", "machine": "mac", "schedule": "daily", "cmd": "true"}
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "a" in msg
    assert "artifact" in msg


def test_job_without_artifacts_empty_list_is_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "a" in msg
    assert "artifact" in msg


def test_duplicate_job_names_is_error(tmp_path):
    job = {
        "name": "dupe",
        "machine": "mac",
        "schedule": "daily",
        "cmd": "true",
        "artifacts": [{"path": "/tmp/x"}],
    }
    data = {"version": 1, "jobs": [dict(job), dict(job)]}
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "duplicate" in msg
    assert "dupe" in msg


# --- artifact check + arg validation --------------------------------------


def test_required_env_non_list_is_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
                "required_env": "not-a-list",
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "a" in msg
    assert "required_env" in msg


def test_required_env_non_string_element_is_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
                "required_env": [123],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "a" in msg
    assert "required_env" in msg


def test_unknown_check_is_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x", "check": "sniff"}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "a" in msg
    assert "sniff" in msg
    assert "check" in msg


def test_max_age_hours_non_numeric_string_is_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x", "max_age_hours": "nope"}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "a" in msg
    assert "max_age_hours" in msg


def test_max_age_hours_bool_is_error(tmp_path):
    # bool is an int subclass in Python -- must be explicitly excluded so
    # `true`/`false` don't silently pass as "numeric".
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x", "max_age_hours": True}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "a" in msg
    assert "max_age_hours" in msg


def test_json_has_keys_requires_list_arg(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [
                    {"path": "/tmp/x", "check": "json_has_keys", "arg": "not-a-list"}
                ],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "json_has_keys" in msg
    assert "list" in msg


def test_regex_requires_string_arg(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x", "check": "regex", "arg": ["not", "a", "string"]}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "regex" in msg
    assert "string" in msg


def test_exists_check_must_have_no_arg(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x", "check": "exists", "arg": "unexpected"}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "exists" in msg
    assert "arg" in msg


def test_nonempty_check_must_have_no_arg(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                "name": "a",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x", "check": "nonempty", "arg": "unexpected"}],
            }
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    assert "nonempty" in msg
    assert "arg" in msg


# --- everything wrong at once: one raise, every problem listed -----------


def test_multiple_problems_across_manifest_collected_into_one_error(tmp_path):
    data = {
        "version": 1,
        "jobs": [
            {
                # missing name
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
            },
            {
                "name": "b",
                "machine": "toaster",  # unknown machine
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
            },
            {
                "name": "c",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [],  # no artifacts
            },
            {
                "name": "d",
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
                "on_fail": "page-me",  # unknown on_fail
            },
            {
                "name": "d",  # duplicate of the job above
                "machine": "mac",
                "schedule": "daily",
                "cmd": "true",
                "artifacts": [{"path": "/tmp/x"}],
            },
        ],
    }
    path = _write(tmp_path, data)

    with pytest.raises(ManifestError) as exc_info:
        load_manifest(path)
    msg = str(exc_info.value)
    lines = msg.splitlines()

    assert "missing required field 'name'" in msg
    assert "toaster" in msg and "machine" in msg
    assert "c" in msg and "artifact" in msg
    assert "page-me" in msg and "on_fail" in msg
    assert "duplicate" in msg and "d" in msg
    # five distinct problems, five lines -- nothing swallowed by fail-fast
    assert len(lines) == 5


# --- the real manifest (Task 2.4) -----------------------------------------


def test_real_manifest_loads_clean():
    """The production manifest must parse with no ManifestError.

    This is the whole point of a strict, fail-together loader: a manifest
    someone actually deploys must be valid, not just the fixtures above.
    """
    assert REAL_MANIFEST_PATH.is_file(), f"real manifest not found at {REAL_MANIFEST_PATH}"
    jobs = load_manifest(REAL_MANIFEST_PATH)
    assert len(jobs) > 0


def test_real_manifest_every_job_has_at_least_one_artifact():
    jobs = load_manifest(REAL_MANIFEST_PATH)
    for job in jobs:
        assert len(job.artifacts) >= 1, f"job {job.name!r} has no artifacts"


def test_real_manifest_covers_all_three_machines():
    jobs = load_manifest(REAL_MANIFEST_PATH)
    machines_present = {job.machine for job in jobs}
    assert machines_present == set(MACHINES), (
        f"expected all machines {sorted(MACHINES)}, got {sorted(machines_present)}"
    )


def test_real_manifest_every_schedule_is_non_empty():
    jobs = load_manifest(REAL_MANIFEST_PATH)
    for job in jobs:
        assert isinstance(job.schedule, str) and job.schedule.strip(), (
            f"job {job.name!r} has a blank schedule"
        )


def test_real_manifest_has_box_merge_runs_job():
    """Winston's merge gatekeeper (cos_merge_runs.py) runs at 50 3 * * *
    -- deliberately off cos_sync.sh's */15 grid (2026-08-01 adversarial-
    review Critical 1: the merge critical section takes a per-repo flock,
    but cos_sync.sh doesn't lock, so this also shrinks the window where
    the two could race on the same space repo) -- and still just before
    box-briefing (0 4), "before briefing generation" per the design. Its
    status artifact gets THREE distinct artifact entries on the same
    file: expected keys present, stale_branches specifically empty, and
    (Important 4) errors specifically empty -- three different failure
    signals worth telling apart in job_verify's output rather than
    folding them into one check.
    """
    jobs = load_manifest(REAL_MANIFEST_PATH)
    by_name = {job.name: job for job in jobs}

    assert "box-merge-runs" in by_name
    job = by_name["box-merge-runs"]
    assert job.machine == "box"
    assert job.schedule == "50 3 * * *"
    assert job.on_fail == "telegram"

    status_artifacts = [a for a in job.artifacts if a.path.endswith("merge_runs_status.json")]
    assert len(status_artifacts) == 3

    checks = [a.check for a in status_artifacts]
    assert checks.count("json_has_keys") == 1
    assert checks.count("regex") == 2

    keys_artifact = next(a for a in status_artifacts if a.check == "json_has_keys")
    assert set(keys_artifact.arg) >= {"generated_at", "stale_branches"}

    regex_args = {a.arg for a in status_artifacts if a.check == "regex"}
    assert regex_args == {'"stale_branches": \\[\\]', '"errors": \\[\\]'}

    for artifact in status_artifacts:
        assert artifact.max_age_hours == 26
