#!/usr/bin/env python3
"""Post tweets via headless Chrome (Playwright) to bypass X API reply restrictions.

X API blocks cold-reply to arbitrary tweets. This module uses a real browser
session with saved auth state to post replies the same way a user would.

Setup (run once):
    python3 chrome_poster.py --setup

Usage:
    python3 chrome_poster.py --reply TWEET_URL "Reply text here"

Programmatic:
    from chrome_poster import post_reply
    our_tweet_id = post_reply(tweet_url, reply_text)
"""

import json
import os
import sys
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
AUTH_STATE_FILE = DATA_DIR / ".datacore" / "state" / "x-auth-state.json"
SCREENSHOT_DIR = DATA_DIR / ".datacore" / "state" / "chrome-screenshots"


def _get_browser(playwright, headless: bool = True):
    """Launch Chromium with persistent-like settings."""
    return playwright.chromium.launch(
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )


def setup_auth():
    """Interactive one-time setup: log in to X and save auth state."""
    from playwright.sync_api import sync_playwright

    print("Opening browser for X login. Log in as @FairDataSociety, then press Enter here.")
    print(f"Auth will be saved to: {AUTH_STATE_FILE}")

    AUTH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _get_browser(p, headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://x.com/login")
        input("Press Enter after you've logged in to X...")
        context.storage_state(path=str(AUTH_STATE_FILE))
        browser.close()

    print(f"Auth saved to {AUTH_STATE_FILE}")


def post_reply(tweet_url: str, reply_text: str, timeout_ms: int = 30000) -> str:
    """Post a reply to a tweet via headless Chrome.

    Args:
        tweet_url: Full URL of the tweet to reply to
        reply_text: Text of the reply (max 280 chars)
        timeout_ms: Navigation timeout in ms

    Returns:
        Our tweet ID (str) or 'unknown' if we couldn't capture it

    Raises:
        FileNotFoundError: Auth state not set up yet (run --setup)
        RuntimeError: Posting failed
    """
    if not AUTH_STATE_FILE.exists():
        raise FileNotFoundError(
            f"X auth state not found at {AUTH_STATE_FILE}. Run: python3 chrome_poster.py --setup"
        )

    from playwright.sync_api import sync_playwright

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _get_browser(p, headless=True)
        context = browser.new_context(
            storage_state=str(AUTH_STATE_FILE),
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        try:
            # Navigate to the tweet
            print(f"  Navigating to {tweet_url}")
            page.goto(tweet_url, timeout=timeout_ms, wait_until="domcontentloaded")
            time.sleep(3)

            # Check we're logged in
            if "login" in page.url.lower():
                raise RuntimeError("X redirected to login — auth state may be expired. Run --setup again.")

            # Click reply button on the tweet
            # The reply icon is an svg with data-testid="reply"
            reply_btn = page.locator('[data-testid="reply"]').first
            reply_btn.wait_for(timeout=10000)
            reply_btn.click()
            time.sleep(1)

            # Find the reply text area
            reply_box = page.locator('[data-testid="tweetTextarea_0"]').first
            reply_box.wait_for(timeout=10000)
            reply_box.click()
            reply_box.fill(reply_text)
            time.sleep(1)

            # Screenshot before posting
            page.screenshot(path=str(SCREENSHOT_DIR / "before_post.png"))

            # Click the Post/Reply button
            post_btn = page.locator('[data-testid="tweetButton"]').first
            post_btn.wait_for(timeout=10000)
            post_btn.click()
            time.sleep(3)

            # Screenshot after posting
            page.screenshot(path=str(SCREENSHOT_DIR / "after_post.png"))

            # Try to capture our tweet ID from the URL or page
            our_tweet_id = "unknown"
            current_url = page.url
            if "/status/" in current_url:
                our_tweet_id = current_url.split("/status/")[-1].split("?")[0]

            # If we're still on the original tweet, look for our reply in the thread
            if our_tweet_id == "unknown" or our_tweet_id == tweet_url.split("/status/")[-1]:
                # Look for our reply link in the thread
                try:
                    time.sleep(2)
                    # Find the most recently posted tweet by @FairDataSociety in the thread
                    tweets = page.locator('[data-testid="tweet"]').all()
                    for tweet in reversed(tweets):
                        try:
                            link = tweet.locator('a[href*="/status/"]').last
                            href = link.get_attribute("href")
                            if href and "/FairDataSociety/" in href:
                                our_tweet_id = href.split("/status/")[-1].split("?")[0]
                                break
                        except Exception:
                            continue
                except Exception:
                    pass

            print(f"  Posted. Our tweet ID: {our_tweet_id}")
            return our_tweet_id

        except Exception as e:
            page.screenshot(path=str(SCREENSHOT_DIR / "error.png"))
            raise RuntimeError(f"Post failed: {e}")
        finally:
            browser.close()


if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup_auth()
    elif "--reply" in sys.argv:
        idx = sys.argv.index("--reply")
        if len(sys.argv) < idx + 3:
            print("Usage: chrome_poster.py --reply TWEET_URL 'Reply text'")
            sys.exit(1)
        url = sys.argv[idx + 1]
        text = sys.argv[idx + 2]
        try:
            tid = post_reply(url, text)
            print(f"Success: {tid}")
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
    else:
        print(__doc__)
