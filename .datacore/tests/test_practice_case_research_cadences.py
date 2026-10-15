"""The Practice's research streams run as Tris's script cadences (DIP-0050).

Installation-level: it reads this install's 9-practice venture, the cadence
templates and the fleet controls. Owner decisions 2026-10-15 (task bad4dc19):
agent tris, files under 9-practice/1-tracks/case-research/, the full stream set,
a contact email set, tris's ceiling 6. Reads no personal records.
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPACE = ROOT / "9-practice"
CASE = SPACE / "1-tracks" / "case-research"
TEMPLATES = ROOT / ".datacore" / "templates" / "cadences"
VENTURES_LIB = ROOT / ".datacore" / "modules" / "ventures" / "lib"
sys.path.insert(0, str(VENTURES_LIB))

# Another installation has no Practice venture: nothing here applies to it.
pytestmark = pytest.mark.skipif(not (SPACE / "venture.yaml").exists(), reason="installation-specific: no 9-practice venture")

# stream -> frequency; S8 is on demand and never scheduled
STREAMS = {"S6": "daily", "S1": "weekly", "S2": "weekly", "S3": "weekly",
           "S4": "weekly", "S5": "weekly", "S7": "monthly"}


def _template(stream):
    text = (TEMPLATES / f"case-research-{stream.lower()}.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head, _, body = text[4:].partition("\n---\n")
    return yaml.safe_load(head), body


def _venture():
    return yaml.safe_load((SPACE / "venture.yaml").read_text(encoding="utf-8"))


def test_the_practice_declares_every_stream_as_a_cadence_of_tris():
    doc = _venture()
    assert doc["name"] == "practice"
    assert (doc.get("nightshift") or {}).get("enabled") is True, "cadence_schedule only plans enabled ventures"
    role = doc["roles"]["research"]
    assert role["agent"] == "tris"
    declared = {name: freq for freq, names in role["cadences"].items() for name in names}
    assert declared == {f"case-research-{s.lower()}": f for s, f in STREAMS.items()}


@pytest.mark.parametrize("stream", sorted(STREAMS))
def test_each_stream_template_is_a_script_cadence_judged_by_its_digest(stream):
    meta, body = _template(stream)
    assert meta["cadence"] == f"case-research-{stream.lower()}"
    assert meta["role"] == "research"
    assert meta["frequency"] == STREAMS[stream]
    ev = meta["evidence"]
    assert ev["path"] == f"1-tracks/case-research/reports/{stream}/{{date}}.md"
    # a list: cadence_run iterates `require`, and a bare string is iterated per character
    assert ev["require"] == [f"^# {stream}"]
    assert ev["min_bytes"] == 500
    assert "space" not in ev, "the digest lives in the venture's own space"
    script = SPACE / meta["script"]
    assert script.is_file(), f"{meta['script']} must exist in 9-practice"
    assert script.stat().st_mode & stat.S_IXUSR, "cadence_run executes the script directly"
    assert f"--stream {stream}" in script.read_text() or f" {stream}" in script.read_text()
    assert int(meta["timeout_minutes"]) <= 60
    assert "0-personal" not in body and "0-personal" not in str(meta)


def test_the_streams_file_lives_in_the_practice_and_points_nowhere_private():
    cfg_path = CASE / "config" / "research-streams.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    assert isinstance(cfg.get("contact_email"), str) and "@" in cfg["contact_email"], "Unpaywall needs one"
    assert set(cfg["streams"]) == set(STREAMS) | {"S8"}
    assert "0-personal" not in text, "the streams file travels; nothing in it may point into the personal space"


def test_dry_run_on_tris_would_run_the_script(monkeypatch, capsys):
    import cadence_run
    from cadence_schedule import slug
    monkeypatch.setenv("DATACORE_ACTOR", "tris")
    monkeypatch.setenv("DATACORE_ROOT", str(ROOT))
    monkeypatch.setattr(cadence_run, "key_ok", lambda actor: None)  # the Mac holds no tris key
    monkeypatch.setattr(cadence_run, "control", lambda root: {"ceilings": {"tris": 6}, "executors": {"tris": "hermes"}})
    sl = slug("practice", "research", "case-research-s6")
    monkeypatch.setattr(sys, "argv", ["cadence_run.py", sl, "--root", str(ROOT), "--dry-run"])
    assert cadence_run.main() == 0
    out = capsys.readouterr().out
    assert "would run script" in out, out


def test_tris_ceiling_covers_her_busiest_day():
    from cadence_schedule import control, plan
    ctl = control(ROOT)
    jobs = plan(ROOT, "tris", ctl)
    assert any(j.venture == "practice" for j in jobs)
    daily = sum(1 for j in jobs if j.frequency == "daily")
    weekly = [int(j.cron.split()[4]) for j in jobs if j.frequency == "weekly"]
    rarer = sum(1 for j in jobs if j.frequency in ("monthly", "quarterly", "last-day"))
    busiest = daily + max((weekly.count(d) for d in range(7)), default=0) + rarer
    assert ctl["ceilings"]["tris"] >= busiest, f"tris runs up to {busiest} a day; ceiling {ctl['ceilings']['tris']}"


def test_venture_doctor_passes_for_the_practice():
    r = subprocess.run([sys.executable, str(VENTURES_LIB / "venture_doctor.py"), "--space", "9-practice", "--strict"],
                       capture_output=True, text=True, env={**os.environ, "DATACORE_ROOT": str(ROOT)})
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_registry_no_longer_calls_the_practice_dormant():
    text = (ROOT / ".datacore" / "registry" / "ventures.yaml").read_text(encoding="utf-8")
    dormant = [l for l in text.splitlines() if l.lstrip().startswith("#") and "dormant" in l.lower()
               or (l.lstrip().startswith("# ") and "9-practice" in l)]
    assert not any("9-practice" in l for l in dormant), dormant
