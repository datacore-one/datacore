# .datacore/modules/comms/tests/test_x_poster.py
"""Tests for multi-account X poster."""
import os
import time
import pytest
from unittest.mock import patch, MagicMock

# Ensure lib is importable
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


class TestAccountSelection:
    """XPoster selects credentials by account name prefix."""

    def test_fds_account_uses_fds_prefix(self):
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'fds_key',
            'FDS_X_API_SECRET': 'fds_secret',
            'FDS_X_ACCESS_TOKEN': 'fds_token',
            'FDS_X_ACCESS_TOKEN_SECRET': 'fds_tsecret',
        }
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds')
            assert poster.api_key == 'fds_key'
            assert poster.api_secret == 'fds_secret'

    def test_unknown_account_raises(self):
        from x_poster import XPoster
        with pytest.raises(ValueError, match="Unknown account"):
            XPoster(account='nonexistent')

    def test_missing_env_var_raises(self):
        from x_poster import XPoster
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(KeyError):
                XPoster(account='fds')


class TestRateLimiting:
    """XPoster enforces daily post limits per account."""

    def test_posts_within_limit_succeed(self, tmp_path):
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds', daily_limit=2, state_dir=str(tmp_path))
            with patch.object(poster, '_oauth_post', return_value={'data': {'id': '1'}}):
                result = poster.post("tweet 1")
                assert result['data']['id'] == '1'

    def test_exceeding_limit_raises(self, tmp_path):
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds', daily_limit=2, state_dir=str(tmp_path))
            with patch.object(poster, '_oauth_post', return_value={'data': {'id': '1'}}):
                poster.post("tweet 1")
                poster.post("tweet 2")
                with pytest.raises(RuntimeError, match="rate limit"):
                    poster.post("tweet 3")

    def test_limit_resets_on_new_day(self, tmp_path):
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds', daily_limit=1, state_dir=str(tmp_path))
            with patch.object(poster, '_oauth_post', return_value={'data': {'id': '1'}}):
                poster.post("tweet 1")
                # Simulate day change by manipulating the DB
                poster._rate_db.execute(
                    "UPDATE rate_limits SET date = '2020-01-01' WHERE account = 'fds'"
                )
                poster._rate_db.commit()
                poster.post("tweet 2")  # should not raise

    def test_rate_limit_persists_across_instances(self, tmp_path):
        """Rate limits survive process restart (systemd oneshot)."""
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        with patch.dict(os.environ, env, clear=False):
            poster1 = XPoster(account='fds', daily_limit=2, state_dir=str(tmp_path))
            with patch.object(poster1, '_oauth_post', return_value={'data': {'id': '1'}}):
                poster1.post("tweet 1")
            # New instance (simulates new systemd oneshot invocation)
            poster2 = XPoster(account='fds', daily_limit=2, state_dir=str(tmp_path))
            with patch.object(poster2, '_oauth_post', return_value={'data': {'id': '2'}}):
                poster2.post("tweet 2")
                with pytest.raises(RuntimeError, match="rate limit"):
                    poster2.post("tweet 3")


class TestPosting:
    """XPoster delegates to OAuth signing for actual posts."""

    def test_post_sends_correct_payload(self):
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds')
            with patch.object(poster, '_oauth_post') as mock:
                mock.return_value = {'data': {'id': '123', 'text': 'hello'}}
                result = poster.post("hello")
                mock.assert_called_once()
                call_args = mock.call_args
                assert call_args[0][1] == {'text': 'hello'}

    def test_reply_includes_reply_field(self):
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds')
            with patch.object(poster, '_oauth_post') as mock:
                mock.return_value = {'data': {'id': '456'}}
                poster.reply("yo", "999")
                payload = mock.call_args[0][1]
                assert payload['reply']['in_reply_to_tweet_id'] == '999'

    def test_quote_rt_includes_quote_field(self):
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds')
            with patch.object(poster, '_oauth_post') as mock:
                mock.return_value = {'data': {'id': '789'}}
                poster.quote_rt("good take", "111")
                payload = mock.call_args[0][1]
                assert payload['quote_tweet_id'] == '111'

    def test_like_calls_like_endpoint(self):
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds', user_id='12345')
            with patch.object(poster, '_oauth_post') as mock:
                mock.return_value = {'data': {'liked': True}}
                poster.like("999")
                url = mock.call_args[0][0]
                assert '/likes' in url


class TestFollowActions:
    """XPoster can follow/unfollow accounts."""

    def test_follow_calls_following_endpoint(self):
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds', user_id='12345')
            with patch.object(poster, '_oauth_post') as mock:
                mock.return_value = {'data': {'following': True}}
                poster.follow('67890')
                url = mock.call_args[0][0]
                assert '/following' in url
                payload = mock.call_args[0][1]
                assert payload['target_user_id'] == '67890'

    def test_unfollow_calls_delete(self):
        from x_poster import XPoster
        env = {
            'FDS_X_API_KEY': 'k', 'FDS_X_API_SECRET': 's',
            'FDS_X_ACCESS_TOKEN': 't', 'FDS_X_ACCESS_TOKEN_SECRET': 'ts',
        }
        with patch.dict(os.environ, env, clear=False):
            poster = XPoster(account='fds', user_id='12345')
            with patch.object(poster, '_oauth_delete') as mock:
                mock.return_value = {'data': {'following': False}}
                poster.unfollow('67890')
                url = mock.call_args[0][0]
                assert '67890' in url
