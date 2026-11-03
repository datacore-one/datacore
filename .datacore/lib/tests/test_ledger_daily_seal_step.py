"""The daily ledger check gains a seal step (plan 2026-10-31, phase H; processes
page, "The daily ledger check", what changes [0]).

Verify checks chains and signatures but not that the seals, which define
"final", still recompute. The daily run therefore also judges every hub
space's newest seal: made by the sequencer and recomputing from the events.
It reuses the hub's seal-age check (`ledger_seal_age_check`, phase D2) with
the age limit off -- the hourly contract judges age; the daily one judges the
seal itself. Written before the code.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from tests import test_ledger_hub as T
from ledger import hub as H

LIB = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "runtime"))
    e = T.Env(tmp_path, monkeypatch)
    e.clock.now = int(time.time() * 1000)
    yield e
    e.close()


def test_with_no_age_limit_an_old_seal_with_unsealed_work_is_judged_on_the_seal_alone(env):
    import ledger_seal_age_check as C
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t1", "title": "one"})
    h.seal()
    own.append("item.create", {"id": "t2", "title": "admitted after the seal"})
    later = int(time.time() * 1000) + 30 * 3600 * 1000
    r = C.check_space(env.hub_dir, now_ms=later, registry_path=env.registry, max_age_s=None)
    assert r["ok"] is True, r


def test_with_no_age_limit_a_seal_by_another_sequencer_still_fails(env):
    import ledger_seal_age_check as C
    h = env.hub()
    env.writer("own", H.LocalTransport(h)).append("item.create", {"id": "t1", "title": "one"})
    h.seal()
    r = C.check_space(env.hub_dir, registry_path=env.registry, max_age_s=None, expected="winston")
    assert r["ok"] is False and "winston" in r["detail"], r


def test_no_age_flag_on_the_command_line(env, capsys):
    import ledger_seal_age_check as C
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t1", "title": "one"})
    h.seal()
    root = env.hub_dir.parent
    env.hub_dir.rename(root / "9-hubspace")
    rc = C.main(["--root", str(root), "--registry", str(env.registry), "--no-age"])
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0 and out[-1].startswith("OK "), out


SEAL_STUB = '''import os, sys
from pathlib import Path
Path(os.environ["AUDIT_DIR"], "seal-args").write_text(" ".join(sys.argv[1:]))
mode = os.environ.get("AUDIT_SEAL", "ok")
if mode == "fail":
    print("  FAIL 2-datacore     the newest seal does not verify: state root mismatch")
    print("FAIL 1 of 1 hub space(s) not freshly sealed by their grantor: 2-datacore")
    sys.exit(1)
if mode == "none":
    print("NONE no space under x has a hub -- nothing to judge, not a pass")
    sys.exit(2)
print("  ok   2-datacore     sealed 3 min ago by winston, verifies")
print("OK 1 hub space(s): newest seal by the grantor, verifies")
'''


def _daily(tmp_path, seal="ok", with_checker=True):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    root = tmp_path / "data"
    (root / "2-datacore/.datacore/events").mkdir(parents=True)
    state, audit = tmp_path / "state", tmp_path / "audit"
    audit.mkdir()
    for f in ("ledger_daily.sh", "runtime_shell.sh", "spaces.py", "yaml_safety.py"):
        shutil.copyfile(LIB / f, scripts / f)
    (scripts / "ledger_cli.py").write_text("print('OK 3 files 120 events')\n")
    (scripts / "shadow_check.py").write_text("print('drift 0')\n")
    (scripts / "ledger_checkpoint.py").write_text("print('OK checkpoint')\n")
    if with_checker:
        (scripts / "ledger_seal_age_check.py").write_text(SEAL_STUB)
    env = dict(os.environ, DATACORE_ROOT=str(root), DATACORE_STATE=str(state),
               DATACORE_PYTHON=sys.executable, AUDIT_DIR=str(audit), AUDIT_SEAL=seal)
    proc = subprocess.run(["bash", str(scripts / "ledger_daily.sh")], env=env,
                          capture_output=True, text=True, timeout=60)
    return proc, state, audit


def test_the_daily_run_judges_every_hub_seal_without_the_age_limit(tmp_path):
    proc, state, audit = _daily(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    args = (audit / "seal-args").read_text()
    assert "--no-age" in args and "--root" in args, args
    log = (state / "ledger-seal.log").read_text().splitlines()
    assert log[-1].startswith("OK "), log


def test_a_seal_that_does_not_recompute_fails_the_daily_run(tmp_path):
    proc, state, _ = _daily(tmp_path, seal="fail")
    assert proc.returncode != 0, proc.stdout + proc.stderr
    log = (state / "ledger-seal.log").read_text()
    assert "state root mismatch" in log and not any(l.startswith("OK ") for l in log.splitlines()), log


def test_no_hub_space_is_written_as_not_a_pass_but_does_not_fail_the_verify(tmp_path):
    proc, state, _ = _daily(tmp_path, seal="none")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    log = (state / "ledger-seal.log").read_text().splitlines()
    assert log[-1].startswith("NONE "), log


def test_the_seal_log_is_read_by_the_daily_contract():
    jobs = yaml.safe_load((LIB / "jobs/manifest.yaml").read_text())["jobs"]
    daily = [j for j in jobs if j["name"] == "box-ledger-verify"][0]
    arts = {Path(a["path"]).name: a for a in daily["artifacts"]}
    assert "ledger-seal.log" in arts, arts
    assert arts["ledger-seal.log"]["check"] == "last_line_regex"
    assert arts["ledger-seal.log"]["arg"].startswith("^OK ")
