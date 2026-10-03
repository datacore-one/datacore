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

What is NOT a second sender (owner decision 2026-10-03): the morning's own
catch-up. `cos_morning_run.py catch-up` re-runs cos_morning.sh only when the
04:00 run never finished, at most twice, and never once the voice message went
out. It is excused only when all of these hold:
  * the manifest declares it: a job whose cmd is the catch-up entry point, on
    the same machine as the one audio job;
  * its code refuses after audio: run here against a temporary state dir that
    says today's voice briefing went out, it re-runs nothing, in every run state
    (never started, crashed, machine restarted, finished), and it never re-runs
    more than twice;
  * in a crontab, the line carries that job's `# datacore-job:<name>` marker
    AND runs the catch-up entry point itself. A marker on a line that runs
    cos_morning.sh directly is still a second sender.

Seeded failures: a second manifest job on nightshift running
today_orchestrator.py without --no-audio (the pre-2026-09-07 duplicate); a
crontab line running cos_morning.sh on another host; a catch-up on another
machine; a catch-up whose code would re-run after audio went out.
"""
from __future__ import annotations

import importlib.util
import json
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
CATCHUP_ENTRY = re.compile(r"cos_morning_run\.py\s+catch-up\b")
MARKER = re.compile(r"#\s*datacore-job:([\w.-]+)")
CATCHUP_CODE = DC / "modules" / "chief-of-staff" / "server" / "lib" / "cos_morning_run.py"


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


def catchup_jobs(manifest: dict) -> list[tuple[str, str]]:
    """Manifest jobs that run the morning's catch-up (which can run cos_morning.sh again)."""
    return [(j.get("name"), j.get("machine")) for j in manifest.get("jobs", []) or []
            if CATCHUP_ENTRY.search(str(j.get("cmd", "")))]


def catchup_refuses_after_audio(code: Path = CATCHUP_CODE) -> bool:
    """Run the catch-up against a temporary state dir that says today's voice message went out.

    It must re-run nothing in any run state, and never more than twice. Read
    from the code, not from a comment: a catch-up that would send a second
    voice message is a second sender.
    """
    import tempfile
    from datetime import datetime, timezone
    spec = importlib.util.spec_from_file_location("_mem46_catchup", code)
    mr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr)
    now = datetime.now(timezone.utc).replace(hour=5, minute=30)
    today = now.date().isoformat()
    states = [{}, {"date": today, "started_at": f"{today}T04:00:04Z", "pid": 1, "boot_id": "a"},
              {"date": today, "started_at": f"{today}T04:00:04Z", "pid": 1, "boot_id": "old"},
              {"date": today, "started_at": f"{today}T04:00:04Z", "finished_at": f"{today}T04:09:00Z"}]
    after_audio: list = []
    without_audio: list = []
    with tempfile.TemporaryDirectory() as tmp:
        cos = Path(tmp)
        mr.COS, mr._now = cos, (lambda: now)
        mr.alert = lambda msg: None
        mr.boot_id, mr.boot_time, mr.pid_alive = (lambda: "a"), (lambda: None), (lambda pid: False)
        mr.run_morning = lambda: after_audio.append(1) or 0
        for state in states:
            (cos / "morning-run.json").write_text(json.dumps(state))
            (cos / "audio-last-ok").write_text(f"{today}T04:07:00Z\n")
            (cos / "morning-catchup.json").unlink(missing_ok=True)
            mr.catch_up()
        # Without audio, a run that never started is re-run -- at most twice. This also
        # proves the harness reaches the re-run at all, so the pass above is not vacuous.
        mr.run_morning = lambda: without_audio.append(1) or 0
        (cos / "audio-last-ok").unlink()
        (cos / "morning-run.json").write_text("{}")
        (cos / "morning-catchup.json").unlink(missing_ok=True)
        for _ in range(4):
            mr.catch_up()
    return not after_audio and 1 <= len(without_audio) <= 2


def excused_catchups(manifest: dict, refuses: bool) -> set[str]:
    """Names of catch-up jobs that are not a second sender (see the module docstring)."""
    producers = audio_jobs(manifest)
    if len(producers) != 1 or not refuses:
        return set()
    machine = producers[0][1]
    return {name for name, m in catchup_jobs(manifest) if m == machine}


def second_senders(lines: list[str], manifest: dict, refuses: bool) -> list[str]:
    """Scheduled lines that could send a second voice message: all, minus excused catch-ups."""
    ok = excused_catchups(manifest, refuses)
    host = None
    if ok:
        machine = audio_jobs(manifest)[0][1]
        sys.path.insert(0, str(DC / "lib"))
        from jobs.manifest import ssh_alias
        host = ssh_alias(machine) or machine
    out = []
    for line in lines:
        m = MARKER.search(line)
        if (m and m.group(1) in ok and line.startswith(f"{host}:")
                and CATCHUP_ENTRY.search(line) and not PRODUCERS.search(line)):
            continue
        out.append(line)
    return out


def test_exactly_one_scheduled_audio_briefing_and_it_is_winstons():
    m = _manifest()
    jobs = audio_jobs(m)
    assert len(jobs) == 1 and jobs[0][1] == "box", f"audio briefing producers in the manifest: {jobs}"
    extra = [n for n, _ in catchup_jobs(m) if n not in excused_catchups(m, catchup_refuses_after_audio())]
    assert not extra, f"catch-up jobs that could send a second voice message: {extra}"


def test_the_catchup_code_refuses_once_audio_went_out():
    assert catchup_refuses_after_audio()


def test_a_catchup_that_would_rerun_after_audio_is_a_second_sender(tmp_path):
    bad = tmp_path / "cos_morning_run.py"
    bad.write_text(CATCHUP_CODE.read_text(encoding="utf-8").replace(
        "if audio_day == today:", "if False and audio_day == today:"))
    assert not catchup_refuses_after_audio(bad)
    m = _manifest()
    assert excused_catchups(m, refuses=False) == set()


def test_a_catchup_on_another_machine_is_a_second_sender():
    m = _manifest()
    m["jobs"] = list(m["jobs"]) + [{"name": "ns-catchup", "machine": "nightshift",
                                    "cmd": "python3 ~/Data/.datacore/modules/chief-of-staff/server/lib/cos_morning_run.py catch-up"}]
    assert "ns-catchup" not in excused_catchups(m, refuses=True)


def test_scan_excuses_only_the_declared_catchup_line():
    m = _manifest()
    real = ("winston: 30 4-8 * * * /usr/bin/python3 ~/Data/.datacore/modules/chief-of-staff/server/lib/"
            "cos_morning_run.py catch-up >> ~/.datacore/cos/morning-catchup.log 2>&1 "
            "# datacore-job:box-briefing-catchup")
    seeded = [
        "nightshift: 0 5 * * * ~/Data/.datacore/lib/cos_morning.sh",                         # a second sender
        "winston: 0 6 * * * ~/Data/.datacore/lib/cos_morning.sh # datacore-job:box-briefing-catchup",  # marker abuse
        "hermes: 30 4-8 * * * python3 ~/Data/.datacore/modules/chief-of-staff/server/lib/cos_morning_run.py catch-up",
        real.replace("winston:", "nightshift:", 1),                                          # right marker, wrong host
    ]
    assert second_senders([real], m, refuses=True) == []
    assert second_senders([real] + seeded, m, refuses=True) == seeded
    assert second_senders([real], m, refuses=False) == [real]


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
    found = second_senders(found, _manifest(), catchup_refuses_after_audio())
    assert not found, f"audio producers scheduled outside the job manifest: {found}"
