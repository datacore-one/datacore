"""Promise MEM-44:

    The briefing never shows yesterday's health readings as today's. If
    today's haven't synced, it says they are not available.

Kind: deterministic. The spoken morning briefing (lib/speak_brief.py, run as
`speak_brief.py` with no date, the way the morning job runs it) on a morning
whose journal has not been written yet: only yesterday's journal and its
pre-generated spoken text exist, and they carry yesterday's readiness and sleep.

What the owner hears must not present those readings as this morning's: either
nothing is spoken (the script stops), or what is spoken says the readings are
from yesterday / today's are not available.

The briefing prose itself, where the health fragment is turned into sentences,
is written by the app daemon (datacored adapters/cos_reasoning.py, not in this
checkout); the publisher of the health fragment (the health module's
publish_briefing.py) is not here either. Both need their own eval for the
"today's haven't synced" wording.

Seen red (2026-10-28): find_journal() falls back to yesterday's journal and the
script speaks yesterday's "Good morning ... readiness is seventy-two" as today's.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "lib" / "speak_brief.py"
YESTERDAY = date.today() - timedelta(days=1)
SPOKEN = ("Good morning. You slept seven hours forty, and readiness is moderate at "
          "seventy-two, so today is full but measured.")
JOURNAL = f"""# {YESTERDAY.isoformat()}

## Daily Briefing

### Good Morning
Sleep 7h40m, readiness 72, HRV 48 ms. A full but measured day.
"""


@pytest.fixture()
def sb(monkeypatch, tmp_path):
    for name in ("kokoro_onnx", "soundfile", "numpy"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    spec = importlib.util.spec_from_file_location("speak_brief_mem44", LIB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    journals = tmp_path / "journals"
    journals.mkdir()
    (journals / f"{YESTERDAY.isoformat()}.md").write_text(JOURNAL)
    (journals / f"{YESTERDAY.isoformat()}_spoken.txt").write_text(SPOKEN)
    monkeypatch.setattr(mod, "JOURNAL_DIR", journals)
    # Never call a model from an eval: the fallback summariser just echoes.
    monkeypatch.setattr(mod, "summarize_with_llm", lambda text: text, raising=False)
    return mod


def _says_not_today(text: str) -> bool:
    return bool(re.search(rf"yesterday|{YESTERDAY.isoformat()}|not (yet )?available|"
                          r"haven'?t synced|has not synced|no reading", text, re.I))


def test_the_morning_audio_never_speaks_yesterdays_readings_as_today(sb):
    try:
        journal = sb.find_journal()
    except SystemExit:
        return                                   # nothing spoken: honest
    text = sb.get_spoken_text(journal)
    if not re.search(r"readiness|slept|sleep|seventy", text, re.I):
        return                                   # no health readings spoken
    assert _says_not_today(text), (
        f"today's journal does not exist yet, and the morning audio speaks "
        f"{journal.name} as this morning's, readings included: {text[:120]!r}")


def test_todays_journal_is_still_spoken(sb):
    """Guard against a vacuous pass: with today's journal present it is the one used."""
    today = sb.JOURNAL_DIR / f"{date.today().isoformat()}.md"
    today.write_text("# today\n")
    assert sb.find_journal() == today
