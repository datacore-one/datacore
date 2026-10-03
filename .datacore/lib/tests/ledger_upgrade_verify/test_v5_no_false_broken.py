"""V5 (PLAN Phase 2; audit C9): no probe reports "broken" for a reason that is
not in the data.

"Broken" sends someone to repair history. The 2026-09-26 morning sweep handed a
repair for a chain that verified OK, because the check timed out (fixed for the
fleet sweep in 8fc7bff/f0b6782, OPS-2). The same class remains elsewhere:

  * a machine that lacks a writer's verify key (a fresh clone, a host the key
    was never distributed to) cannot judge that writer's signatures. That is
    "could not tell" on this machine -- not a forged history. (With signing
    required -- strict mode, Phase 6 -- a signature that cannot be checked
    is an error; strict is not exercised here.)
  * a timeout is "could not run", never "broken".
  * an in-ledger void is found through the space that holds it, never through
    a folder of that NAME under the installation root: hermes and plur-claw
    clone 5-plur as `2-plur` and `2-plur-space` (C9 item 3). The out-of-band
    exception file is retired (decision 6); the checkpoint still looked a void
    up by `DATACORE_ROOT/<folder name>`.

Seeded failure: a consumer mapping "no key here" or a timeout to broken, or the
checkpoint resolving voids by folder name.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

import _verify_fixtures as vf


def _space_signed_by_a_writer_this_machine_has_no_key_for(root):
    space = vf.new_space(root)
    vf.append(space, "alice", 4, sign=True)
    vf.append(space, "bot", 2)
    reg = root / ".datacore" / "keys" / "registry.yaml"
    data = yaml.safe_load(reg.read_text())
    assert "alice" in data["actors"], "fixture: alice's key must have been registered by signing"
    del data["actors"]["alice"]                       # this machine never received alice's key
    reg.write_text(yaml.safe_dump(data))
    return space


def test_a_writer_whose_key_this_machine_lacks_is_could_not_tell(sandbox_root):
    space = _space_signed_by_a_writer_this_machine_has_no_key_for(sandbox_root)
    got = {"cli": vf.cli_verdict(space), "health": vf.health_verdict(sandbox_root),
           "relay": vf.relay_verdict(space)}
    wrong = {k: v for k, v in got.items() if v[0] != "unknown"}
    assert not wrong, ("a missing verify key on this machine is 'could not tell', not 'broken'. These said otherwise:\n"
                       + "\n".join(f"  {k}: '{v[0]}' ({v[1][:300]})" for k, v in wrong.items()))


def test_the_fleet_sweep_reads_it_as_could_not_check(sandbox_root, monkeypatch):
    space = _space_signed_by_a_writer_this_machine_has_no_key_for(sandbox_root)
    import v2_verify
    monkeypatch.setattr(v2_verify, "ROOT", sandbox_root)
    rep = v2_verify.Report()
    v2_verify.check_ledger(rep, quick=True)
    row = next(c for c in rep.checks if c.name == "hash chains")
    assert row.ok is None, f"the sweep must say could-not-check for {space.name}, said {row.ok!r}: {row.detail}"


def test_a_timeout_is_could_not_run(sandbox_root, monkeypatch):
    space = vf.new_space(sandbox_root)
    vf.append(space, "bot", 2)
    import v2_verify
    monkeypatch.setattr(v2_verify, "ROOT", sandbox_root)
    monkeypatch.setattr(v2_verify, "run", lambda args, timeout=180: (124, f"timed out after {timeout}s"))
    rep = v2_verify.Report()
    v2_verify.check_ledger(rep, quick=True)
    row = next(c for c in rep.checks if c.name == "hash chains")
    assert row.ok is None and "timeout" in row.detail, f"a timeout must read as not checked: {row.ok!r} {row.detail}"


def test_the_daily_check_does_not_call_could_not_tell_a_failure_of_the_data(sandbox_root, tmp_path):
    _space_signed_by_a_writer_this_machine_has_no_key_for(sandbox_root)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for f in ("ledger_daily.sh", "runtime_shell.sh", "spaces.py", "yaml_safety.py", "ledger_cli.py"):
        (scripts / f).symlink_to(vf.LIB / f)
    (scripts / "shadow_check.py").write_text("print('drift 0')\n")
    (scripts / "ledger_checkpoint.py").write_text("print('OK checkpoint')\n")
    state = tmp_path / "state"
    env = dict(os.environ, DATACORE_ROOT=str(sandbox_root), DATACORE_STATE=str(state),
               DATACORE_PYTHON=sys.executable)
    proc = subprocess.run(["bash", str(scripts / "ledger_daily.sh")], env=env, capture_output=True,
                          text=True, timeout=300)
    out = (state / "ledger-verify.log").read_text()
    line = next((l for l in out.splitlines() if "0-alpha" in l), "")
    assert "could not" in line.lower() and "FAIL" not in line, (
        f"the space must read 'could not check', not FAIL:\n{out}")
    assert proc.returncode != 0 and not any(l.startswith("OK ") for l in out.splitlines()), (
        f"could-not-check is never a pass: the job must not report OK:\n{out}")


def test_a_void_is_found_by_the_space_not_by_its_folder_name(sandbox_root, tmp_path):
    """A clone of a space under another name, outside the installation root, holds
    an authorised void. Its checkpoint must restore: the void is in the clone."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    space = vf.new_space(elsewhere, "2-plur")             # the installation root has no "2-plur"
    vf.append(space, "alice", 2)
    vf.append(space, "bot", 2)
    seq, h, body = vf.hand_append_bad_hash(vf.log_path(space, "bot"), "bot")
    vf.void(space, "owner", "bot.jsonl", seq, h, body)
    assert vf.cli_verdict(space)[0] == "ok", "fixture: the voided space verifies"
    verdict, detail = vf.checkpoint_verdict(space)
    assert verdict == "ok", f"the checkpoint looked the void up somewhere else than the space: {detail}"
