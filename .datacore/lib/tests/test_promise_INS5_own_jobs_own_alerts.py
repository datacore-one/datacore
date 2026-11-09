"""INS-5: "Each install chooses its own scheduled jobs and where alerts go, and
alerts can go somewhere other than Telegram."

Kind: deterministic.
  - On a fresh install (git archive HEAD + INSTALL.md, see _fresh_install.py)
    the job list the verifier reads by default belongs to the install: it is
    not a file Datacore tracks (a tracked list is this fleet's list and every
    `git pull` would overwrite the install's choice -- audit C14: the tracked
    manifest carries 57 fleet jobs), and a job the install adds there is the
    one that gets checked.
  - Alerts: the manifest accepts, and job_verify --alert offers, at least one
    delivery channel besides stderr logging and Telegram, and a failing job
    routed to it is delivered there (not to Telegram).

Seeded failure: the default manifest resolves to the tracked
.datacore/lib/jobs/manifest.yaml; on_fail accepts only log|telegram.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fresh_install as F  # noqa: E402

import job_verify  # noqa: E402
from jobs import manifest as jm  # noqa: E402


@pytest.fixture(scope="module")
def install(tmp_path_factory):
    return F.follow_guide(tmp_path_factory.mktemp("ins5"))


def test_the_default_job_list_belongs_to_the_install(install):
    p = install.run("python3 -c 'import sys; sys.path.insert(0, \".datacore/lib\"); "
                    "import job_verify; print(job_verify._default_manifest_path())'")
    assert p.returncode == 0, p.stderr[-300:]
    path = Path(p.stdout.strip().splitlines()[-1])
    rel = path.resolve().relative_to(install.data.resolve()) if str(path.resolve()).startswith(
        str(install.data.resolve())) else None
    tracked = rel is not None and subprocess.run(
        F.GIT + ["-C", str(install.data), "ls-files", "--error-unmatch", str(rel)],
        capture_output=True, timeout=30).returncode == 0
    assert not tracked, (f"the verifier's default job list is {rel}, a file Datacore tracks: "
                         "the install cannot choose its own jobs without editing Datacore")


def _other_channels() -> set[str]:
    return set(jm.ON_FAILS) - {"log", "telegram"}


def test_alerts_have_a_channel_besides_telegram():
    assert _other_channels(), f"on_fail accepts only {sorted(jm.ON_FAILS)}"
    alert = next(a for a in job_verify.build_parser()._actions if "--alert" in a.option_strings)
    assert set(alert.choices or ()) - {"log", "telegram"}, f"--alert offers only {alert.choices}"


def test_a_failure_routed_elsewhere_does_not_go_to_telegram(tmp_path, monkeypatch):
    others = sorted(_other_channels())
    if not others:
        pytest.fail("no non-Telegram channel exists to route to")
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
    sent = []
    monkeypatch.setattr(job_verify, "_send_telegram", lambda m: sent.append(m) or True)
    monkeypatch.setattr(job_verify, "_delegate_repair", lambda *a, **k: ("refused", "fixture"))
    import yaml
    manifest = tmp_path / "m.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "jobs": [{
        "name": "fixture", "machine": "box", "schedule": "0 3 * * *", "cmd": "true",
        "on_fail": others[0], "artifacts": [{"path": str(tmp_path / "missing.log")}]}]}))
    (tmp_path / "space").mkdir()
    with pytest.raises(SystemExit):
        job_verify.main(["--machine", "box", "--manifest", str(manifest), "--space", str(tmp_path / "space")])
    assert sent == [], f"routed to {others[0]!r} but delivered to Telegram"
