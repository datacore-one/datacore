#!/usr/bin/env python3
"""Post approved replies to X via Tweepy (Twitter API v2).

Uses OAuth 1.0a (User Context) to post replies as @FairDataSociety.
Requires: X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
"""

import os
import tweepy


def get_client(
    consumer_key: str = None,
    consumer_secret: str = None,
    access_token: str = None,
    access_token_secret: str = None,
) -> tweepy.Client:
    """Create authenticated Tweepy v2 client."""
    return tweepy.Client(
        consumer_key=consumer_key or os.environ["X_CONSUMER_KEY"],
        consumer_secret=consumer_secret or os.environ["X_CONSUMER_SECRET"],
        access_token=access_token or os.environ["X_ACCESS_TOKEN"],
        access_token_secret=access_token_secret or os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def post_reply(target_tweet_id: str, reply_text: str, client: tweepy.Client = None) -> str:
    """Post a reply to a tweet.

    Args:
        target_tweet_id: The tweet ID to reply to
        reply_text: The reply text
        client: Optional pre-configured client

    Returns:
        Our new tweet ID (str)

    Raises:
        tweepy.TweepyException on API errors
    """
    if client is None:
        client = get_client()

    response = client.create_tweet(
        text=reply_text,
        in_reply_to_tweet_id=str(target_tweet_id),
    )
    return str(response.data["id"])


def post_tweet(text: str, client: tweepy.Client = None) -> str:
    """Post a standalone tweet (not a reply).

    Returns: Our new tweet ID (str)
    """
    if client is None:
        client = get_client()

    response = client.create_tweet(text=text)
    return str(response.data["id"])


def verify_credentials(client: tweepy.Client = None) -> dict:
    """Verify API credentials work. Returns authenticated user info."""
    if client is None:
        client = get_client()

    me = client.get_me()
    return {"id": me.data.id, "username": me.data.username, "name": me.data.name}
