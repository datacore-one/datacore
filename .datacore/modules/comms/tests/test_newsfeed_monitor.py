# .datacore/modules/comms/tests/test_newsfeed_monitor.py
"""Tests for newsfeed monitor."""
import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


class TestNewsfeedScoring:
    """Newsfeed monitor scores tweets by relevance."""

    def test_scores_privacy_content_high(self):
        from newsfeed_monitor import NewsfeedMonitor
        m = NewsfeedMonitor.__new__(NewsfeedMonitor)
        m.topic_keywords = {
            'privacy': 3, 'encryption': 3, 'surveillance': 2,
            'decentralized': 2, 'file sharing': 3,
        }
        tweet = {'text': "End-to-end encryption should be the default for all file sharing"}
        score = m._score_tweet(tweet)
        assert score >= 3  # matches encryption + file sharing

    def test_scores_irrelevant_content_low(self):
        from newsfeed_monitor import NewsfeedMonitor
        m = NewsfeedMonitor.__new__(NewsfeedMonitor)
        m.topic_keywords = {'privacy': 3, 'encryption': 3}
        tweet = {'text': "Just had a great lunch at the new restaurant downtown"}
        score = m._score_tweet(tweet)
        assert score == 0


class TestLikeStrategy:
    """Newsfeed monitor likes quality content from followed accounts."""

    def test_likes_high_scored_tweets(self, tmp_path):
        from newsfeed_monitor import NewsfeedMonitor
        poster = MagicMock()
        poster.like.return_value = {'data': {'liked': True}}
        db = MagicMock()

        m = NewsfeedMonitor(poster=poster, follow_db=db,
                           like_threshold=2, daily_like_budget=50,
                           state_dir=str(tmp_path))
        m.topic_keywords = {'privacy': 3}
        m._process_tweet(
            {'id': '1', 'text': 'Privacy is a human right', 'author_id': '99'},
            score=3,
        )
        poster.like.assert_called_once_with('1')
        db.record_engagement.assert_called_once_with('99')

    def test_skips_low_scored_tweets(self, tmp_path):
        from newsfeed_monitor import NewsfeedMonitor
        poster = MagicMock()
        db = MagicMock()

        m = NewsfeedMonitor(poster=poster, follow_db=db,
                           like_threshold=2, daily_like_budget=50,
                           state_dir=str(tmp_path))
        m.topic_keywords = {'privacy': 3}
        m._process_tweet(
            {'id': '1', 'text': 'Good morning everyone', 'author_id': '99'},
            score=0,
        )
        poster.like.assert_not_called()


class TestQuoteRTDrafting:
    """Newsfeed monitor generates quote-RT drafts for exceptional content."""

    def test_drafts_quote_rt_for_high_engagement_tweets(self, tmp_path):
        from newsfeed_monitor import NewsfeedMonitor
        poster = MagicMock()
        db = MagicMock()
        drafter = MagicMock()
        drafter.return_value = "This is exactly right."

        m = NewsfeedMonitor(
            poster=poster, follow_db=db,
            like_threshold=2, daily_like_budget=50,
            quote_rt_drafter=drafter, quote_rt_threshold=5,
            state_dir=str(tmp_path),
        )
        tweet = {
            'id': '1', 'author_id': '99',
            'text': 'Encryption and decentralized file sharing are the future of privacy',
            'public_metrics': {'like_count': 100, 'retweet_count': 30},
        }
        drafts = m._maybe_draft_quote_rt(tweet, score=6)
        assert len(drafts) == 1
        assert drafts[0]['text'] == "This is exactly right."
