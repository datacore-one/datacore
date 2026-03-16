#!/usr/bin/env python3
"""SessionEnd hook — clean up session state (DIP-0024).

Removes active_session.json so next session triggers fresh bootstrap.
Hebbian write-back is handled by MCP session.end, not this hook.
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.expanduser("~/Data"), ".datacore", "lib"))
from session_state import cleanup_session, _debug

def main():
    _debug("session_cleanup: cleaning up")
    cleanup_session()
    sys.exit(0)

if __name__ == "__main__":
    main()
