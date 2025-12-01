# .datacore/modules/comms/tests/test_follow_list_builder.py
"""Tests for follow list builder — X API scraper."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


class TestFollowListBuilder:
    """Follow list builder scrapes anchor accounts and populates DB."""

    def test_resolve_username(self):
        from follow_list_builder import FollowListBuilder
        from follow_db import FollowDB
        db = MagicMock()
        builder = FollowListBuilder(db=db, bearer_token="test_token")
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"data": {"id": "12345"}}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch('follow_list_builder.urlopen', return_value=mock_response):
            user_id = builder._resolve_username("EFF")
            assert user_id == "12345"

    def test_retry_limited_to_3(self):
        """_api_get retries at most 3 times on 429, then raises."""
        from follow_list_builder import FollowListBuilder
        from urllib.error import HTTPError
        db = MagicMock()
        builder = FollowListBuilder(db=db, bearer_token="test_token")

        error = HTTPError(
            url="https://api.x.com/2/test", code=429, msg="Too Many",
            hdrs=MagicMock(get=MagicMock(return_value="1")), fp=MagicMock()
        )
        error.read = MagicMock(return_value=b"rate limited")

        with patch('follow_list_builder.urlopen', side_effect=error):
            with patch('follow_list_builder.time.sleep'):  # skip actual waits
                import pytest
                with pytest.raises(Exception, match="429"):
                    builder._api_get("https://api.x.com/2/test")
        # Should have been called 4 times total (1 + 3 retries)
        assert builder._request_count == 4

    def test_missing_bearer_token_raises(self):
        from follow_list_builder import FollowListBuilder
        import os
        with patch.dict(os.environ, {}, clear=True):
            import pytest
            with pytest.raises(ValueError, match="X_BEARER_TOKEN"):
                FollowListBuilder(db=MagicMock(), bearer_token=None)
