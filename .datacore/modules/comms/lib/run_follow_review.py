#!/usr/bin/env python3
"""Runner: weekly follow review + follow-back check. Called by systemd timer."""
import os
import sys
from pathlib import Path

LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))

# Load env
env_file = DATA_DIR / ".datacore" / "env" / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

from follow_db import FollowDB
from follow_manager import FollowManager
from x_poster import XPoster

db = FollowDB(DATA_DIR / ".datacore" / "state" / "follow-list.db")
poster = XPoster(account='fds', user_id=os.environ.get('FDS_X_USER_ID'))
fm = FollowManager(db=db, poster=poster,
                   bearer_token=os.environ.get('X_BEARER_TOKEN'))

# Check follow-backs first
our_user_id = os.environ.get('FDS_X_USER_ID')
if our_user_id:
    fb = fm.check_follow_backs(our_user_id)
    print(f"Follow-backs: {fb['updated']} new mutuals from {fb['checked']} checked")

# Then review stale
result = fm.review_stale_follows(stale_days=30)
print(f"Reviewed: {result['reviewed']} | Unfollowed: {result['unfollowed']}")
print(fm.generate_report())
db.close()
