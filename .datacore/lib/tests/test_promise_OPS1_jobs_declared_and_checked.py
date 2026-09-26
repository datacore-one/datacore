"""OPS-1: "Every scheduled job is declared in one list, and each job's output is
checked daily; a missing or stale output alerts me."

Kind: deterministic (the verifier alerts on a missing / stale output) plus
production contract (the list really is complete: every job scheduled on the
box and on this mac appears in jobs/manifest.yaml, the verifier itself is
scheduled at least daily there, and every declared job carries a freshness
bound so "stale" is detectable at all).

Seeded failure: the verifier stops alerting on a missing or stale output (the
dispatcher is a no-op, or freshness is not checked), or a job is added to a
crontab / launchd without a manifest entry.
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import time
from pathlib import Path

import pytest
import yaml

import job_verify

LIB = Path(__file__).resolve().parents[1]
MANIFEST = LIB / "jobs" / "manifest.yaml"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def _run(tmp_path, monkeypatch, artifact: dict) -> tuple[int, list[str]]:
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "jobs": [{
        "name": "fixture-job", "machine": "box", "schedule": "0 3 * * *",
        "cmd": "true", "on_fail": "telegram", "artifacts": [artifact]}]}))
    space = tmp_path / "space"
    space.mkdir()
    sent: list[str] = []
    monkeypatch.setattr(job_verify, "_send_telegram", lambda m: sent.append(m) or True)
    # No agent to hand the repair to: the operator must be told.
    monkeypatch.setattr(job_verify, "_delegate_repair", lambda *a, **k: ("refused", "fixture"))
    monkeypatch.setattr(job_verify, "_file_task", lambda *a, **k: None)
    try:
        job_verify.main(["--machine", "box", "--manifest", str(manifest), "--space", str(space)])
        code = 0
    except SystemExit as exc:
        code = exc.code or 0
    return code, sent


def test_missing_output_alerts_me(tmp_path, monkeypatch):
    code, sent = _run(tmp_path, monkeypatch, {"path": str(tmp_path / "never-written.log"),
                                              "max_age_hours": 26})
    assert code != 0
    assert len(sent) == 1 and "fixture-job" in sent[0], sent


def test_stale_output_alerts_me(tmp_path, monkeypatch):
    out = tmp_path / "old.log"
    out.write_text("OK done\n")
    old = time.time() - 30 * 3600
    os.utime(out, (old, old))
    code, sent = _run(tmp_path, monkeypatch, {"path": str(out), "max_age_hours": 26})
    assert code != 0, "an output 30h old against a 26h bound is stale"
    assert len(sent) == 1 and "fixture-job" in sent[0], sent


def test_fresh_output_does_not_alert(tmp_path, monkeypatch):
    out = tmp_path / "fresh.log"
    out.write_text("OK done\n")
    code, sent = _run(tmp_path, monkeypatch, {"path": str(out), "max_age_hours": 26})
    assert code == 0 and sent == []


def test_every_declared_job_has_a_freshness_bound():
    """Without max_age_hours an output that stopped being written is never 'stale'."""
    jobs = yaml.safe_load(MANIFEST.read_text())["jobs"]
    unbounded = [f"{j['machine']}:{j['name']}" for j in jobs
                 if not any(a.get("max_age_hours") for a in j.get("artifacts") or [])]
    assert unbounded == [], f"jobs whose output can never be reported stale: {unbounded}"


# ── production: the list is the whole list ──────────────────────────────────

_SCRIPT = re.compile(r"([\w.-]+\.(?:sh|py))\b")
_TAG = re.compile(r"#\s*datacore-job:([\w.-]+)")
_VERIFIER = ("job_verify.py", "job_verify_notify.sh")


def _declared(machine: str) -> tuple[set[str], str]:
    jobs = [j for j in yaml.safe_load(MANIFEST.read_text())["jobs"] if j["machine"] == machine]
    return {j["name"] for j in jobs}, "\n".join(j["cmd"] for j in jobs)


def _undeclared(lines: list[str], machine: str) -> list[str]:
    names, cmds = _declared(machine)
    missing = []
    for line in lines:
        scripts = _SCRIPT.findall(line)
        if any(s in _VERIFIER for s in scripts):
            continue  # the checker itself
        tag = _TAG.search(line)
        if tag:
            if tag.group(1) not in names:
                missing.append(line[:160])
            continue
        key = scripts[0] if scripts else line.split(None, 5)[-1][:60]
        if key not in cmds:
            missing.append(line[:160])
    return missing


def _cron_jobs(text: str) -> list[str]:
    return [l for l in text.splitlines()
            if l.strip() and not l.lstrip().startswith("#") and "=" not in l.split()[0]]


@pytest.mark.production
def test_every_box_cron_job_is_declared_and_verified_daily():
    try:
        out = subprocess.run(SSH + ["winston", "crontab -l"], capture_output=True,
                             text=True, timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail("could not read the box crontab (timeout) -- could not tell is not a pass")
    assert out.returncode == 0, f"could not read the box crontab: {out.stderr[-200:]}"
    lines = _cron_jobs(out.stdout)
    assert any("job_verify" in l for l in lines), "the verifier is not scheduled on the box"
    missing = _undeclared(lines, "box")
    assert missing == [], "box jobs scheduled but not in the manifest:\n" + "\n".join(missing)


@pytest.mark.production
def test_every_mac_datacore_job_is_declared_and_verified_daily():
    if not Path.home().joinpath("Library/LaunchAgents").is_dir():
        pytest.fail("not the mac: run this eval on the mac")
    lines = []
    out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
    lines += [l for l in _cron_jobs(out.stdout) if ".datacore" in l]
    for p in sorted(Path.home().joinpath("Library/LaunchAgents").glob("*.plist")):
        try:
            d = plistlib.loads(p.read_bytes())
        except Exception:  # noqa: BLE001
            continue
        label = str(d.get("Label", p.stem))
        if not label.split(".")[1:2] == ["datacore"]:
            continue
        if "StartCalendarInterval" not in d and "StartInterval" not in d:
            continue  # a daemon, not a scheduled job
        args = " ".join(d.get("ProgramArguments") or [d.get("Program", "")])
        lines.append(f"launchd {label}: {args}")
    assert any("job_verify" in l for l in lines), "the verifier is not scheduled on the mac"
    missing = _undeclared(lines, "mac")
    assert missing == [], "mac jobs scheduled but not in the manifest:\n" + "\n".join(missing)
