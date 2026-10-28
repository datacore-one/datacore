"""MEM-47: The audio briefing opens with a plain "Good morning", uses no
honorifics, and runs about two and a half minutes.

Kind: deterministic + production contract (read-only ssh to the box).
Only Winston sends the audio briefing (owner decision), so its script writer
is the thing tested: winston_speak.py (chief-of-staff server/lib; the box installs it in .datacore/lib).
  * the spoken-script instruction (_SPOKEN_PROMPT, the path used every
    morning) asks for a plain "Good morning" opening, forbids honorifics, and
    targets about 2.5 minutes (a word budget whose midpoint is 300-450 words
    at ~150 spoken words a minute);
  * the deterministic fallback script (spoken_script) opens with "Good
    morning" and carries no honorific;
  * production: the voice file Winston sent in the last 26 hours
    (/tmp/winston_brief.ogg on the box) lasts 2-3 minutes.

Seeded failure: a script target of 130-190 words / 60-90 seconds (today's
prompt), or a "Good morning, sir" opening.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import fleet_reach

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
# Winston's scripts live in the chief-of-staff module (server/lib), the copy git holds;
# the box's setup installs them into .datacore/lib. Judge the source, not an install.
sys.path.insert(0, str(LIB.parent / "modules" / "chief-of-staff" / "server" / "lib"))

HONORIFICS = re.compile(r"\b(sir|ma'?am|madam|mister|boss|your (honou?r|excellency|majesty))\b", re.I)


def _prompt() -> str:
    import winston_speak
    return winston_speak._SPOKEN_PROMPT


def test_script_instruction_targets_about_two_and_a_half_minutes():
    p = _prompt()
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*spoken words", p)
    assert m, "the spoken-script instruction states no word budget"
    mid = (int(m.group(1)) + int(m.group(2))) / 2
    assert 300 <= mid <= 450, f"word budget {m.group(0)!r} is ~{mid / 150:.1f} min, not ~2.5 min"


def test_script_instruction_asks_for_plain_good_morning_and_no_honorifics():
    p = _prompt()
    assert re.search(r'"Good morning"', p), "the instruction does not ask for a plain \"Good morning\" opening"
    assert re.search(r"honorific|no (sir|ma'am)", p, re.I), "the instruction does not forbid honorifics"


def test_fallback_script_opens_plainly():
    import winston_speak
    b = {"observation": "Sleep was short; keep the afternoon light.",
         "sections": [{"key": "the_world", "body": "Markets are calm. Nothing moves your plans."}],
         "focus": [{"title": "Send the grant draft"}], "open_questions": ["Approve the trip budget?"]}
    s = winston_speak.spoken_script(b)
    assert s.startswith("Good morning"), s[:60]
    assert not HONORIFICS.search(s), HONORIFICS.search(s).group(0)


@pytest.mark.production
def test_the_last_audio_briefing_lasts_about_two_and_a_half_minutes():
    cmd = ("f=/tmp/winston_brief.ogg; [ -f $f ] && [ $(( $(date +%s) - $(stat -c %Y $f) )) -lt 93600 ] "
           "&& ffprobe -v error -show_entries format=duration -of csv=p=0 $f")
    try:
        # Winston's morning run happens in his k3s pod's cron container (roster, fleet_reach).
        p = fleet_reach.run("box", cmd, timeout=40)
    except subprocess.TimeoutExpired:
        pytest.fail("could not tell: the box did not answer")
    out = p.stdout.strip()
    assert out, f"could not tell: no audio briefing from the last 26 hours on the box (rc={p.returncode})"
    secs = float(out.splitlines()[0])
    assert 120 <= secs <= 180, f"today's audio briefing ran {secs / 60:.1f} min, not ~2.5"
