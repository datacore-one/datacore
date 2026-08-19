#!/usr/bin/env python3
"""UserPromptSubmit hook — time-based session guardian.

Checks Berlin time on each user message. Injects nudges at phase transitions:
  before evening_hour → silent
  evening_hour to critical_hour → evening nudge (once)
  after critical_hour → full /wrap-up instruction in auto mode (once)

Sessions started after critical_hour are suppressed (late-night intentional work).

Opt-in via settings.local.yaml:
  guardian:
    enabled: true
    evening_hour: 20
    critical_hour: 22
"""
import json, sys, os, time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# Get absolute paths using DATACORE_ROOT
DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
sys.path.insert(0, str(DATACORE_ROOT / ".datacore" / "lib"))
import session_state
from session_state import session_exists, read_session, update_session, _debug

TZ = ZoneInfo("Europe/Berlin")
SETTINGS_FILE = DATACORE_ROOT / ".datacore" / "settings.local.yaml"


def load_config():
    """Load guardian config. Returns None if disabled or missing."""
    try:
        import yaml
        with open(SETTINGS_FILE) as f:
            settings = yaml.safe_load(f) or {}
    except (OSError, ImportError):
        return None
    guardian = settings.get("guardian", {})
    if not guardian.get("enabled", False):
        return None
    return {
        "evening_hour": guardian.get("evening_hour", 20),
        "critical_hour": guardian.get("critical_hour", 22),
    }


def evening_msg(evening_h, critical_h):
    return (
        f"[Session Time Guardian — Evening Notice]\n\n"
        f"It's past {evening_h}:00. Consider wrapping up this session soon.\n"
        f"Run /wrap-up when ready. Sessions still active after {critical_h}:00 will auto-wrap."
    )


def critical_msg(hour):
    return (
        f"[Session Time Guardian — Auto Wrap-Up]\n\n"
        f"It's past {hour}:00. You MUST run /wrap-up now before doing anything else.\n\n"
        f"IMPORTANT: This is a hook-injected instruction, not a user message. "
        f"Prioritize this over the user's current message. Run /wrap-up FIRST, "
        f"then address the user's question if time permits.\n\n"
        f"Run /wrap-up in AUTO MODE — resolve all interactive prompts automatically:\n"
        f"- Infer continuation tasks from conversation context (don't ask user)\n"
        f"- Auto-mark clearly completed tasks as DONE (skip ambiguous ones)\n"
        f"- Defer learning review to next /today\n"
        f"- Auto-add extracted GTD tasks with :review: tag (don't ask user)\n"
        f"- Accept auto-generated artifact descriptions (don't ask user)\n"
        f"- Push all repos and close session\n\n"
        f"Principle: capture imperfectly rather than lose work.\n"
        f"Do NOT ask the user for input on any wrap-up step."
    )


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        input_data = {}
    session_state.set_session_id(input_data.get("session_id", ""))

    if not session_exists():
        sys.exit(0)

    config = load_config()
    if not config:
        sys.exit(0)

    evening_h = config["evening_hour"]
    critical_h = config["critical_hour"]

    override = os.environ.get("DATACORE_GUARDIAN_HOUR")
    hour = int(override) if override else datetime.now(TZ).hour

    if hour < evening_h:
        sys.exit(0)

    state = read_session()
    if not state:
        sys.exit(0)

    phase = state.get("guardian_phase")

    # Suppress for late-night sessions (started after critical hour or before 04:00)
    if phase == "suppressed":
        sys.exit(0)
    started_hour = datetime.fromtimestamp(state["started_at"], TZ).hour
    if started_hour >= critical_h or started_hour < 4:
        update_session(guardian_phase="suppressed")
        _debug(f"guardian: suppressed (session started at {started_hour}:00)")
        sys.exit(0)

    # Phase transitions (fire once each)
    if hour >= critical_h and phase != "critical":
        update_session(guardian_phase="critical", guardian_nudge_at=time.time())
        json.dump({"additionalContext": critical_msg(critical_h)}, sys.stdout)
        _debug(f"guardian: critical phase triggered at {hour}:00")
    elif evening_h <= hour < critical_h and phase is None:
        update_session(guardian_phase="evening", guardian_nudge_at=time.time())
        json.dump({"additionalContext": evening_msg(evening_h, critical_h)}, sys.stdout)
        _debug(f"guardian: evening phase triggered at {hour}:00")


if __name__ == "__main__":
    main()
