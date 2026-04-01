"""Integration tests — verify components wire together correctly."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


class TestEndToEndAutonomous:
    """Full pipeline: discover → draft → guardrails → post → state update."""

    def test_full_autonomous_pipeline(self, tmp_path):
        """Simulates one full cycle of autonomous engagement."""
        import engagement_state as state_mod
        from autonomous_poster import AutonomousPoster
        from guardrails import ContentGuardrails
        from x_poster import XPoster

        state_file = tmp_path / "state.json"
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }

        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds', state_dir=str(tmp_path))

            guardrails = ContentGuardrails(
                anti_patterns=["WAGMI", "moon"],
                max_length=280,
                max_exclamations=0,
            )

            notifier = MagicMock()
            ap = AutonomousPoster(
                poster=poster, notifier=notifier,
                voice_yaml_path=None, guardrails=guardrails,
                state_mod=state_mod, state_file=state_file,
                state_dir=str(tmp_path),
            )

            # Clean content — should auto-post
            with patch.object(poster, '_oauth_post',
                            return_value={'data': {'id': 'tweet_001'}}):
                result = ap.process_draft(
                    "Privacy by architecture, not by promise.",
                    "target_1", "@someone",
                )
            assert result['action'] == 'posted'

            # Dirty content — should escalate
            result = ap.process_draft(
                "WAGMI friends!", "target_2", "@other",
            )
            assert result['action'] == 'escalated'

            # Verify state has 1 posted, 0 escalated (escalated doesn't write state)
            st, _ = state_mod.load(state_file)
            assert len(st.get('posted', [])) == 1

    def test_rate_limit_blocks_excess_posts(self, tmp_path):
        """Rate limiter prevents more posts than daily_limit."""
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        import pytest
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds', daily_limit=2,
                           state_dir=str(tmp_path))
            with patch.object(poster, '_oauth_post',
                            return_value={'data': {'id': '1'}}):
                poster.post("first")
                poster.post("second")
                with pytest.raises(RuntimeError, match="rate limit"):
                    poster.post("third")

    def test_follow_pipeline(self, tmp_path):
        """Follow DB → Manager → Follow API call chain."""
        from follow_db import FollowDB
        from follow_manager import FollowManager

        db = FollowDB(tmp_path / "test.db")
        for i in range(5):
            db.add_account(
                user_id=str(i), username=f"user{i}",
                cluster="dev" if i < 3 else "privacy",
                source_anchor="@EFF", overlap_count=1,
                followers=1000, following=500,
            )

        poster = MagicMock()
        poster.follow.return_value = {'data': {'following': True}}

        fm = FollowManager(db=db, poster=poster, daily_budget=3)
        result = fm.execute_daily_follows()
        assert result['followed'] == 3
        assert poster.follow.call_count == 3

        # Verify DB updated
        stats = db.stats()
        assert stats['by_status'].get('followed', 0) == 3
