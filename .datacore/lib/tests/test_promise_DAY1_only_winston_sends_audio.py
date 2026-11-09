"""DAY-1: every morning Winston sends my briefing (voice plus a short Telegram
opener) and writes it into my journal. It is the only audio briefing I get;
no other machine sends one.

Two producers sent the daily audio (ENG-2026-09-22-010): Winston's morning
pipeline and the voice-terminal module's /today post-hook, which ran
`speak_brief.py --telegram` on whatever machine ran /today. Owner, 2026-09-26:
keep only Winston's.

Seeded failure: put `--telegram` back into the /today hook, or turn the
module's telegram_delivery on.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / ".datacore" / "modules" / "voice-terminal" / "module.yaml"


def test_the_today_hook_never_sends_audio_to_telegram():
    doc = yaml.safe_load(MODULE.read_text(encoding="utf-8"))
    hook = str((doc.get("hooks") or {}).get("today", {}).get("instructions", ""))
    assert "--telegram" not in hook
    assert (doc.get("settings") or {}).get("telegram_delivery") is False
