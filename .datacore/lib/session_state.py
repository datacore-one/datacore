#!/usr/bin/env python3
"""Session state management for reactive hooks (DIP-0024)."""
import json, os, sys, tempfile, time

STATE_DIR = os.path.expanduser("~/Data/.datacore/state")
STATE_FILE = os.path.join(STATE_DIR, "active_session.json")
DEBUG = os.environ.get("DATACORE_HOOKS_DEBUG") == "1"

def _debug(msg):
    if DEBUG:
        print(f"[hooks] {msg}", file=sys.stderr)

def session_exists():
    """Check if active session state file exists (~1ms)."""
    return os.path.exists(STATE_FILE)

def create_session(prompt=""):
    """Create session state file (atomic write)."""
    os.makedirs(STATE_DIR, exist_ok=True)
    state = {
        "started_at": time.time(),
        "first_prompt": prompt[:200],
        "last_inject_domain": None,
    }
    # Atomic write: temp file + rename prevents partial reads
    fd, tmppath = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    try:
        os.write(fd, json.dumps(state).encode())
        os.close(fd)
        os.rename(tmppath, STATE_FILE)
    except Exception:
        os.close(fd)
        if os.path.exists(tmppath):
            os.remove(tmppath)
        raise
    _debug(f"session created: {prompt[:50]}")
    return state

def read_session():
    """Read current session state."""
    if not session_exists():
        return None
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

def update_session(**kwargs):
    """Update session state fields (atomic write)."""
    state = read_session() or {}
    state.update(kwargs)
    fd, tmppath = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    try:
        os.write(fd, json.dumps(state).encode())
        os.close(fd)
        os.rename(tmppath, STATE_FILE)
    except Exception:
        os.close(fd)
        if os.path.exists(tmppath):
            os.remove(tmppath)
        raise

def cleanup_session():
    """Remove session state file."""
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        _debug("session cleaned up")
