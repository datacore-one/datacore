#!/usr/bin/env python3
"""SessionEnd hook — clean up session state (DIP-0024).

Removes this session's state file so next session triggers fresh bootstrap.
If guardian is enabled and session ended late without wrap-up,
logs a breadcrumb for next /today to surface.

Hebbian write-back is handled by MCP session.end, not this hook.

ORDER IS LOAD-BEARING: `session_archive.py` MUST run before this hook in the
SessionEnd array. It reads the state file this one deletes, and the archive is
what the nightly learning sweep works from. Reordering them silently drops
`first_prompt` and `cwd` from every archived session.
"""
import json, sys, os
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# Get absolute paths using DATACORE_ROOT
DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
sys.path.insert(0, str(DATACORE_ROOT / ".datacore" / "lib"))
import session_state
from session_state import read_session, cleanup_session, _debug

TZ = ZoneInfo("Europe/Berlin")
UNWRAPPED_LOG = DATACORE_ROOT / ".datacore" / "state" / "unwrapped_sessions.log"


def _log_unwrapped_session(state):
    """Log session that closed without wrap-up after critical hour."""
    try:
        import yaml
        settings_file = DATACORE_ROOT / ".datacore" / "settings.local.yaml"
        with open(settings_file) as f:
            settings = yaml.safe_load(f) or {}
        guardian = settings.get("guardian", {})
        if not guardian.get("enabled", False):
            return
        critical_h = guardian.get("critical_hour", 22)
    except (OSError, ImportError):
        return

    hour = datetime.now(TZ).hour
    started_hour = datetime.fromtimestamp(state.get("started_at", 0), TZ).hour
    wrapped = state.get("auto_wrapped", False)
    suppressed = state.get("guardian_phase") == "suppressed"

    if hour >= critical_h and not wrapped and not suppressed and not (started_hour >= critical_h or started_hour < 4):
        prompt = state.get("first_prompt", "unknown")[:100]
        ts = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        UNWRAPPED_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(UNWRAPPED_LOG, "a") as f:
            f.write(f"{ts} | {prompt}\n")
        _debug(f"session_cleanup: logged unwrapped session")


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        input_data = {}
    session_state.set_session_id(input_data.get("session_id", ""))

    state = read_session()
    if state:
        _log_unwrapped_session(state)

    _debug("session_cleanup: cleaning up")
    cleanup_session()
    sys.exit(0)

if __name__ == "__main__":
    main()
