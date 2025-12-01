# .datacore/modules/comms/tests/test_follow_manager.py
"""Tests for follow manager."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


class TestFollowManager:
    """Follow manager executes daily follow batch and reviews."""

    @patch('follow_manager.time.sleep')
    def test_daily_follow_respects_budget(self, mock_sleep, tmp_path):
        from follow_db import FollowDB
        from follow_manager import FollowManager
        db = FollowDB(tmp_path / "test.db")
        for i in range(100):
            db.add_account(
                user_id=str(i), username=f"user{i}", cluster="dev",
                source_anchor="@x", overlap_count=1,
                followers=1000, following=500,
            )
        poster = MagicMock()
        poster.follow.return_value = {'data': {'following': True}}

        fm = FollowManager(db=db, poster=poster, daily_budget=10)
        result = fm.execute_daily_follows()
        assert result['followed'] == 10
        assert poster.follow.call_count == 10

    def test_skips_already_followed(self, tmp_path):
        from follow_db import FollowDB
        from follow_manager import FollowManager
        db = FollowDB(tmp_path / "test.db")
        db.add_account(user_id="1", username="a", cluster="dev",
                       source_anchor="@x", overlap_count=1,
                       followers=1000, following=500)
        db.mark_followed("1")
        poster = MagicMock()

        fm = FollowManager(db=db, poster=poster, daily_budget=10)
        result = fm.execute_daily_follows()
        assert result['followed'] == 0
        poster.follow.assert_not_called()

    @patch('follow_manager.time.sleep')
    def test_review_unfollows_stale(self, mock_sleep, tmp_path):
        from follow_db import FollowDB
        from follow_manager import FollowManager
        db = FollowDB(tmp_path / "test.db")
        db.add_account(user_id="1", username="old_acct", cluster="dev",
                       source_anchor="@x", overlap_count=1,
                       followers=100, following=500)
        db.mark_followed("1", followed_at="2020-01-01T00:00:00Z")
        poster = MagicMock()
        poster.unfollow.return_value = {'data': {'following': False}}

        fm = FollowManager(db=db, poster=poster, daily_budget=10)
        result = fm.review_stale_follows(stale_days=30)
        assert result['unfollowed'] == 1
