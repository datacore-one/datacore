#!/usr/bin/env python3
"""Session state management for reactive hooks (DIP-0024).

Per-session state files keyed by CLAUDE_CODE_SESSION_ID to prevent
cross-session interference (the ralph-loop class of bugs).

State files: {DATACORE_ROOT}/.datacore/state/sessions/{session_id}.json
"""
import json, glob, os, sys, time
from pathlib import Path
from file_utils import atomic_write_json, file_lock, locked_read_modify_write_json

# Get absolute paths using DATACORE_ROOT
DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
STATE_DIR = str(DATACORE_ROOT / ".datacore" / "state" / "sessions")
DEBUG = os.environ.get("DATACORE_HOOKS_DEBUG") == "1"


def _debug(msg):
    if DEBUG:
        print(f"[hooks] {msg}", file=sys.stderr)


_session_id_override = None


def set_session_id(session_id):
    """Cache session ID for this process — call after reading stdin JSON in hook scripts."""
    global _session_id_override
    _session_id_override = session_id.strip() if session_id else None


def _get_session_id():
    """Get current session ID. Tries env var first, then process-level cache."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if sid:
        return sid
    return _session_id_override


def _state_file(session_id=None):
    """Return state file path for a session. Uses env var if session_id not given."""
    sid = session_id or _get_session_id()
    if not sid:
        return None
    if not isinstance(sid, str) or sid in (".", "..") or any(c in sid for c in ("/", "\\", "\x00")):
        raise ValueError("session ID must be a single filename component")
    return os.path.join(STATE_DIR, f"{sid}.json")


def _atomic_write(path, data):
    """Use the shared complete-write and durability invariant."""
    atomic_write_json(Path(path), data)


def session_exists(session_id=None):
    """Check if session state file exists (~1ms)."""
    path = _state_file(session_id)
    return path is not None and os.path.exists(path)


def create_session(prompt="", session_id=None):
    """Create session state file (atomic write)."""
    path = _state_file(session_id)
    if not path:
        _debug("create_session: no session_id available, skipping")
        return {}
    state = {
        "session_id": session_id or _get_session_id(),
        "started_at": time.time(),
        "first_prompt": prompt[:200],
        "last_inject_domain": None,
    }
    with file_lock(Path(path)):
        try:
            with open(path, encoding="utf-8") as source:
                existing = json.load(source)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not isinstance(existing, dict):
                raise ValueError("session state must be an object")
            return existing
        _atomic_write(path, state)
    _debug(f"session created: {state['session_id'][:12]}... prompt={prompt[:50]}")
    return state


def read_session(session_id=None):
    """Read current session state."""
    if not session_exists(session_id):
        return None
    path = _state_file(session_id)
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def update_session(session_id=None, **kwargs):
    """Update session state fields (atomic write)."""
    path = _state_file(session_id)
    if not path:
        return
    def modify(state):
        if state is None:
            state = {}
        if not isinstance(state, dict):
            raise ValueError("session state must be an object")
        state.update(kwargs)
        return state
    locked_read_modify_write_json(Path(path), modify)


def cleanup_session(session_id=None):
    """Remove session state file."""
    path = _state_file(session_id)
    if path:
        with file_lock(Path(path)):
            Path(path).unlink(missing_ok=True)
        _debug(f"session cleaned up: {os.path.basename(path)}")


def cleanup_all_sessions():
    """Remove all session state files."""
    if os.path.isdir(STATE_DIR):
        for f in glob.glob(os.path.join(STATE_DIR, "*.json")):
            with file_lock(Path(f)):
                Path(f).unlink(missing_ok=True)
            _debug(f"stale session cleaned: {os.path.basename(f)}")


def cleanup_stale_sessions(max_age_hours=24):
    """Remove session state files older than max_age_hours (crash recovery)."""
    if not os.path.isdir(STATE_DIR):
        return
    cutoff = time.time() - (max_age_hours * 3600)
    for f in glob.glob(os.path.join(STATE_DIR, "*.json")):
        try:
            with file_lock(Path(f)):
                # Recheck after acquiring the same lock as create/update: an
                # active session may have refreshed while cleanup waited.
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
                    _debug(f"stale session cleaned: {os.path.basename(f)}")
        except FileNotFoundError:
            pass


def list_sessions():
    """List all active session state files. Returns list of state dicts."""
    sessions = []
    if not os.path.isdir(STATE_DIR):
        return sessions
    for f in glob.glob(os.path.join(STATE_DIR, "*.json")):
        try:
            with open(f) as fh:
                sessions.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            pass
    return sessions
