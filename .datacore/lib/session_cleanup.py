#!/usr/bin/env python3
"""SessionEnd hook — clean up session state (DIP-0024).

Removes this session's state file so next session triggers fresh bootstrap.
If guardian is enabled and session ended late without wrap-up,
logs a breadcrumb for next /today to surface.

Hebbian write-back is handled by MCP session.end, not this hook.
"""
import sys, os
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.expanduser("~/Data"), ".datacore", "lib"))
from session_state import read_session, cleanup_session, _debug

TZ = ZoneInfo("Europe/Berlin")
UNWRAPPED_LOG = os.path.expanduser("~/Data/.datacore/state/unwrapped_sessions.log")


def _log_unwrapped_session(state):
    """Log session that closed without wrap-up after critical hour."""
    try:
        import yaml
        with open(os.path.expanduser("~/Data/.datacore/settings.local.yaml")) as f:
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
        os.makedirs(os.path.dirname(UNWRAPPED_LOG), exist_ok=True)
        with open(UNWRAPPED_LOG, "a") as f:
            f.write(f"{ts} | {prompt}\n")
        _debug(f"session_cleanup: logged unwrapped session")


def main():
    state = read_session()
    if state:
        _log_unwrapped_session(state)

    _debug("session_cleanup: cleaning up")
    cleanup_session()
    sys.exit(0)

if __name__ == "__main__":
    main()
