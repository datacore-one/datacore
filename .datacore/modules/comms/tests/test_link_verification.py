#!/usr/bin/env python3
"""Tests for link verification in XPoster."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add lib to path
TEST_DIR = Path(__file__).parent
LIB_DIR = TEST_DIR.parent / "lib"
DATACORE_LIB = TEST_DIR.parent.parent.parent / "lib"
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(DATACORE_LIB))


def test_link_verifier_import():
    """Test that LinkVerifier can be imported."""
    from link_verifier import LinkVerifier, LinkVerificationError
    assert LinkVerifier is not None
    assert LinkVerificationError is not None


def test_xposter_with_link_verification():
    """Test XPoster initializes with link verification."""
    import os
    os.environ['FDS_X_API_KEY'] = 'test'
    os.environ['FDS_X_API_SECRET'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN_SECRET'] = 'test'

    from x_poster import XPoster

    with tempfile.TemporaryDirectory() as tmpdir:
        poster = XPoster(account='fds', state_dir=tmpdir, verify_links=True)
        assert poster.verify_links is True
        assert poster.verifier is not None


def test_xposter_without_link_verification():
    """Test XPoster can disable link verification."""
    import os
    os.environ['FDS_X_API_KEY'] = 'test'
    os.environ['FDS_X_API_SECRET'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN_SECRET'] = 'test'

    from x_poster import XPoster

    with tempfile.TemporaryDirectory() as tmpdir:
        poster = XPoster(account='fds', state_dir=tmpdir, verify_links=False)
        assert poster.verify_links is False
        assert poster.verifier is None


def test_post_with_valid_link():
    """Test posting with a valid link passes verification."""
    import os
    os.environ['FDS_X_API_KEY'] = 'test'
    os.environ['FDS_X_API_SECRET'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN_SECRET'] = 'test'

    from x_poster import XPoster
    from link_verifier import LinkVerifier

    with tempfile.TemporaryDirectory() as tmpdir:
        poster = XPoster(account='fds', state_dir=tmpdir, verify_links=True)

        # Mock the verifier to return success
        poster.verifier = Mock(spec=LinkVerifier)
        poster.verifier.verify_or_raise = Mock(return_value=[{
            'url': 'https://example.com',
            'status_code': 200,
            'content_type': 'text/html',
        }])

        # Mock _check_rate_limit and _oauth_post
        poster._check_rate_limit = Mock()
        poster._oauth_post = Mock(return_value={'data': {'id': '123'}})
        poster._increment_rate_count = Mock()

        # Should succeed
        text = "Check this out: https://example.com"
        result = poster.post(text)

        # Verify verifier was called
        poster.verifier.verify_or_raise.assert_called_once_with(text)
        poster._oauth_post.assert_called_once()


def test_post_with_invalid_link():
    """Test posting with invalid link raises LinkVerificationError."""
    import os
    os.environ['FDS_X_API_KEY'] = 'test'
    os.environ['FDS_X_API_SECRET'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN_SECRET'] = 'test'

    from x_poster import XPoster
    from link_verifier import LinkVerifier, LinkVerificationError

    with tempfile.TemporaryDirectory() as tmpdir:
        poster = XPoster(account='fds', state_dir=tmpdir, verify_links=True)

        # Mock the verifier to raise error
        poster.verifier = Mock(spec=LinkVerifier)
        poster.verifier.verify_or_raise = Mock(
            side_effect=LinkVerificationError("Link verification failed")
        )

        # Mock rate limit check
        poster._check_rate_limit = Mock()
        poster._oauth_post = Mock()

        # Should raise LinkVerificationError
        text = "Broken link: https://example.com/404"
        try:
            poster.post(text)
            assert False, "Should have raised LinkVerificationError"
        except LinkVerificationError as e:
            assert "Link verification failed" in str(e)
            # Verify post was NOT called
            poster._oauth_post.assert_not_called()


def test_reply_with_invalid_link():
    """Test replying with invalid link raises LinkVerificationError."""
    import os
    os.environ['FDS_X_API_KEY'] = 'test'
    os.environ['FDS_X_API_SECRET'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN_SECRET'] = 'test'

    from x_poster import XPoster
    from link_verifier import LinkVerifier, LinkVerificationError

    with tempfile.TemporaryDirectory() as tmpdir:
        poster = XPoster(account='fds', state_dir=tmpdir, verify_links=True)

        # Mock the verifier to raise error
        poster.verifier = Mock(spec=LinkVerifier)
        poster.verifier.verify_or_raise = Mock(
            side_effect=LinkVerificationError("Non-200 status code: 404")
        )

        poster._check_rate_limit = Mock()
        poster._oauth_post = Mock()

        # Should raise LinkVerificationError
        text = "More info: https://broken-link.example.com"
        try:
            poster.reply(text, reply_to_id="123456")
            assert False, "Should have raised LinkVerificationError"
        except LinkVerificationError as e:
            assert "Non-200 status code" in str(e)
            poster._oauth_post.assert_not_called()


def test_post_without_links_skips_verification():
    """Test posts without links skip verification."""
    import os
    os.environ['FDS_X_API_KEY'] = 'test'
    os.environ['FDS_X_API_SECRET'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN_SECRET'] = 'test'

    from x_poster import XPoster
    from link_verifier import LinkVerifier

    with tempfile.TemporaryDirectory() as tmpdir:
        poster = XPoster(account='fds', state_dir=tmpdir, verify_links=True)

        # Mock the verifier
        poster.verifier = Mock(spec=LinkVerifier)
        poster.verifier.verify_or_raise = Mock()

        poster._check_rate_limit = Mock()
        poster._oauth_post = Mock(return_value={'data': {'id': '123'}})
        poster._increment_rate_count = Mock()

        # Post without links
        text = "Privacy by architecture, not by promise."
        poster.post(text)

        # Verifier should NOT be called (no URLs in text)
        poster.verifier.verify_or_raise.assert_not_called()
        poster._oauth_post.assert_called_once()


def test_skip_verification_flag():
    """Test skip_verification flag bypasses link verification."""
    import os
    os.environ['FDS_X_API_KEY'] = 'test'
    os.environ['FDS_X_API_SECRET'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN'] = 'test'
    os.environ['FDS_X_ACCESS_TOKEN_SECRET'] = 'test'

    from x_poster import XPoster
    from link_verifier import LinkVerifier

    with tempfile.TemporaryDirectory() as tmpdir:
        poster = XPoster(account='fds', state_dir=tmpdir, verify_links=True)

        poster.verifier = Mock(spec=LinkVerifier)
        poster.verifier.verify_or_raise = Mock()

        poster._check_rate_limit = Mock()
        poster._oauth_post = Mock(return_value={'data': {'id': '123'}})
        poster._increment_rate_count = Mock()

        # Post with skip_verification=True
        text = "Broken link: https://example.com/404"
        poster.post(text, skip_verification=True)

        # Verifier should NOT be called
        poster.verifier.verify_or_raise.assert_not_called()
        poster._oauth_post.assert_called_once()


if __name__ == '__main__':
    # Run tests
    import pytest
    pytest.main([__file__, '-v'])
