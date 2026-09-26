"""MEM-46: I get exactly one audio briefing each morning. No second copy comes
from another machine or bot.

Kind: deterministic (declared schedule) + production contract (read-only ssh).
Owner decision: only Winston sends the audio briefing.
  * every job in .datacore/lib/jobs/manifest.yaml that can send audio to
    Telegram is found by following its command: cos_morning.sh / winston_audio.sh
    / winston_speak / speak_brief --telegram, today_orchestrator.py run
    WITHOUT --no-audio (its default is run_tts(telegram=True)), or
    `nightshift run --command=/today` unless run.py passes --no-audio. Exactly
    one such job may exist, and it runs on the box (Winston);
  * the voice-terminal /today hook never passes --telegram;
  * production: no host (winston, nightshift, hermes, plur-claw, this mac)
    schedules an audio producer outside the job manifest (crontab, systemd
    units, launchd).

Seeded failure: a second manifest job on nightshift running
today_orchestrator.py without --no-audio (the pre-2026-09-07 duplicate).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parents[2]
DC = ROOT / ".datacore"
MANIFEST = DC / "lib" / "jobs" / "manifest.yaml"
PRODUCERS = re.compile(r"cos_morning\.sh|winston_audio\.sh|winston_speak")
HOSTS = ("winston", "nightshift", "hermes", "plur-claw")


def _nightshift_today_sends_audio() -> bool:
    src = (DC / "modules" / "nightshift" / "lib" / "run.py").read_text(encoding="utf-8")
    m = re.search(r"orchestrator_args\s*=\s*\{[^}]*'/today'\s*:\s*\[([^\]]*)\]", src)
    return not (m and "--no-audio" in m.group(1))


def audio_jobs(manifest: dict) -> list[tuple[str, str]]:
    out = []
    for job in manifest.get("jobs", []) or []:
        cmd = str(job.get("cmd", ""))
        sends = (bool(PRODUCERS.search(cmd))
                 or ("speak_brief" in cmd and "--telegram" in cmd)
                 or ("today_orchestrator" in cmd and "--no-audio" not in cmd and "--dry-run" not in cmd)
                 or ("--command=/today" in cmd and _nightshift_today_sends_audio()))
        if sends:
            out.append((job.get("name"), job.get("machine")))
    return out


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_exactly_one_scheduled_audio_briefing_and_it_is_winstons():
    jobs = audio_jobs(_manifest())
    assert len(jobs) == 1 and jobs[0][1] == "box", f"audio briefing producers in the manifest: {jobs}"


def test_detector_sees_a_second_producer():
    m = _manifest()
    m["jobs"] = list(m["jobs"]) + [{"name": "dup", "machine": "nightshift",
                                    "cmd": "python3 ~/Data/.datacore/modules/nightshift/lib/today_orchestrator.py"}]
    assert len(audio_jobs(m)) == 2


def test_voice_terminal_today_hook_sends_no_audio():
    doc = yaml.safe_load((DC / "modules" / "voice-terminal" / "module.yaml").read_text(encoding="utf-8"))
    hook = str((doc.get("hooks") or {}).get("today", {}).get("instructions", ""))
    assert "--telegram" not in hook
    assert (doc.get("settings") or {}).get("telegram_delivery") is False


SCAN = ("crontab -l 2>/dev/null | grep -v '^#' | grep -E 'cos_morning|winston_audio|winston_speak|speak_brief|today_orchestrator'; "
        "grep -rlE 'cos_morning|winston_audio|winston_speak|speak_brief|today_orchestrator.py' "
        "/etc/systemd/system ~/.config/systemd/user 2>/dev/null; true")


@pytest.mark.production
def test_no_host_schedules_audio_outside_the_manifest():
    found, unreachable = [], []
    for h in HOSTS:
        try:
            p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", h, SCAN],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            unreachable.append(h)
            continue
        if p.returncode == 255:
            unreachable.append(h)
            continue
        found += [f"{h}: {l.strip()}" for l in p.stdout.splitlines() if l.strip()]
    mac = subprocess.run(["bash", "-c", "grep -lE 'cos_morning|winston_audio|winston_speak|speak_brief|today_orchestrator' "
                          "~/Library/LaunchAgents/*.plist 2>/dev/null; crontab -l 2>/dev/null | "
                          "grep -E 'cos_morning|winston_audio|winston_speak|speak_brief|today_orchestrator'; true"],
                         capture_output=True, text=True, timeout=30)
    found += [f"mac: {l.strip()}" for l in mac.stdout.splitlines() if l.strip()]
    assert not unreachable, f"could not tell (unreachable): {unreachable}"
    assert not found, f"audio producers scheduled outside the job manifest: {found}"
