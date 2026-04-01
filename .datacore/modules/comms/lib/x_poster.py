# .datacore/modules/comms/lib/x_poster.py
"""Multi-account X poster with OAuth 1.0a signing and persistent rate limiting.

Supports: post, reply, quote RT, like, follow, unfollow.
Reusable across campaigns: FDS, Datacore (@datacore, @mr_data_dc).

**Rate limits persist to SQLite** — survives systemd oneshot restarts.
**Kill switch** — checks for .datacore/state/campaign-kill-switch file.

Account credentials use env var prefix convention:
  {PREFIX}_X_API_KEY, {PREFIX}_X_API_SECRET,
  {PREFIX}_X_ACCESS_TOKEN, {PREFIX}_X_ACCESS_TOKEN_SECRET

Usage:
    poster = XPoster(account='fds')
    poster.post("Privacy by architecture, not by promise.")
    poster.reply("Exactly.", tweet_id="123456")
    poster.like("123456")
    poster.follow("67890")
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

TWEET_URL = "https://api.x.com/2/tweets"

# Map account names to env var prefixes
ACCOUNT_PREFIXES = {
    'fds': 'FDS_X_',
    'jssr': 'JSSR_X_',
    'datacore': 'DATACORE_X_',
    'mr_data_dc': 'MRDATA_X_',
    'plur': 'PLUR_X_',
}

# Kill switch — if this file exists, all posting is halted
KILL_SWITCH_PATH = Path(os.environ.get(
    "DATACORE_ROOT", os.path.expanduser("~/Data")
)) / ".datacore" / "state" / "campaign-kill-switch"


class KillSwitchActive(RuntimeError):
    """Raised when kill switch file exists."""
    pass


class XPoster:
    def __init__(self, account: str, daily_limit: int = 50,
                 user_id: str = None, state_dir: str = None,
                 verify_links: bool = True):
        prefix = ACCOUNT_PREFIXES.get(account)
        if not prefix:
            raise ValueError(
                f"Unknown account: {account}. "
                f"Known: {list(ACCOUNT_PREFIXES)}"
            )
        self.account = account
        self.api_key = os.environ[f'{prefix}API_KEY']
        self.api_secret = os.environ[f'{prefix}API_SECRET']
        self.access_token = os.environ[f'{prefix}ACCESS_TOKEN']
        self.access_token_secret = os.environ[f'{prefix}ACCESS_TOKEN_SECRET']
        self.user_id = user_id  # needed for like/follow endpoints
        self.daily_limit = daily_limit

        # Persistent rate limit DB
        state_path = state_dir or str(
            Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
            / ".datacore" / "state"
        )
        Path(state_path).mkdir(parents=True, exist_ok=True)
        db_file = Path(state_path) / "rate-limits.db"
        self._rate_db = sqlite3.connect(str(db_file))
        self._rate_db.execute("PRAGMA journal_mode=WAL")
        self._rate_db.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                account TEXT, date TEXT, post_count INTEGER DEFAULT 0,
                PRIMARY KEY (account, date)
            )
        """)
        self._rate_db.commit()

        # Link verification gate
        self.verify_links = verify_links
        self._verifier = None
        if verify_links:
            try:
                import sys
                sys.path.insert(0, str(Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data"))) / ".datacore" / "lib"))
                from link_verifier import LinkVerifier
                self._verifier = LinkVerifier()
            except ImportError:
                import sys
                print("Warning: link_verifier not found, link verification disabled", file=sys.stderr)
                self.verify_links = False

    # --- Public API ---

    def _verify_text_links(self, text: str, skip_verification: bool = False):
        """Verify all links in text before posting. Raises LinkVerificationError on failure."""
        if skip_verification or not self.verify_links or not self._verifier:
            return
        self._verifier.verify_or_raise(text)

    def post(self, text: str, skip_verification: bool = False) -> dict:
        """Post a standalone tweet."""
        self._verify_text_links(text, skip_verification)
        self._check_rate_limit()
        result = self._oauth_post(TWEET_URL, {'text': text})
        self._increment_rate_count()
        return result

    def reply(self, text: str, reply_to_id: str, skip_verification: bool = False) -> dict:
        """Reply to a tweet."""
        self._verify_text_links(text, skip_verification)
        self._check_rate_limit()
        result = self._oauth_post(TWEET_URL, {
            'text': text,
            'reply': {'in_reply_to_tweet_id': str(reply_to_id)},
        })
        self._increment_rate_count()
        return result

    def quote_rt(self, text: str, tweet_id: str, skip_verification: bool = False) -> dict:
        """Quote retweet."""
        self._verify_text_links(text, skip_verification)
        self._check_rate_limit()
        result = self._oauth_post(TWEET_URL, {
            'text': text,
            'quote_tweet_id': tweet_id,
        })
        self._increment_rate_count()
        return result

    def like(self, tweet_id: str) -> dict:
        """Like a tweet. Requires user_id."""
        self._check_kill_switch()
        url = f"https://api.x.com/2/users/{self.user_id}/likes"
        return self._oauth_post(url, {'tweet_id': tweet_id})

    def follow(self, target_user_id: str) -> dict:
        """Follow a user. Requires user_id."""
        self._check_kill_switch()
        url = f"https://api.x.com/2/users/{self.user_id}/following"
        return self._oauth_post(url, {'target_user_id': target_user_id})

    def unfollow(self, target_user_id: str) -> dict:
        """Unfollow a user. Requires user_id."""
        self._check_kill_switch()
        url = f"https://api.x.com/2/users/{self.user_id}/following/{target_user_id}"
        return self._oauth_delete(url)

    def can_reply(self, tweet_id: str) -> bool:
        """Check if we can reply to a tweet (reply_settings allows it).

        Returns True if replies are open to everyone, False if restricted.
        Returns True on API errors (fail open — let the reply attempt reveal 403).
        """
        try:
            url = f"https://api.x.com/2/tweets/{tweet_id}?tweet.fields=reply_settings"
            result = self._oauth_get(url)
            settings = result.get("data", {}).get("reply_settings", "everyone")
            return settings == "everyone"
        except Exception:
            return True  # Fail open — 403 will be caught at reply time

    def upload_media(self, image_path: str) -> str:
        """Upload an image and return media_id_string. Uses v1.1 media/upload."""
        upload_url = "https://upload.twitter.com/1.1/media/upload.json"
        image_data = Path(image_path).read_bytes()
        boundary = secrets.token_hex(16)
        filename = Path(image_path).name
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="media_data"\r\n\r\n'
            f"{base64.b64encode(image_data).decode()}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        oauth_params = {
            "oauth_consumer_key": self.api_key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": self.access_token,
            "oauth_version": "1.0",
        }
        sig = self._oauth_signature("POST", upload_url, oauth_params)
        oauth_params["oauth_signature"] = sig
        header = "OAuth " + ", ".join(
            f'{self._percent_encode(k)}="{self._percent_encode(v)}"'
            for k, v in sorted(oauth_params.items())
        )
        req = Request(upload_url, data=body, method="POST")
        req.add_header("Authorization", header)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
                return result["media_id_string"]
        except HTTPError as e:
            error_body = e.read().decode()
            raise Exception(f"Media upload error {e.code}: {error_body}")

    def post_with_media(self, text: str, media_ids: list) -> dict:
        """Post a tweet with media attachments."""
        self._check_rate_limit()
        payload = {'text': text, 'media': {'media_ids': media_ids}}
        result = self._oauth_post(TWEET_URL, payload)
        self._increment_rate_count()
        return result

    def reply_with_media(self, text: str, reply_to_id: str, media_ids: list) -> dict:
        """Reply to a tweet with media attachments."""
        self._check_rate_limit()
        payload = {
            'text': text,
            'reply': {'in_reply_to_tweet_id': str(reply_to_id)},
            'media': {'media_ids': media_ids},
        }
        result = self._oauth_post(TWEET_URL, payload)
        self._increment_rate_count()
        return result

    def get_tweet(self, tweet_id: str, fields: str = "reply_settings,author_id,public_metrics") -> dict:
        """Fetch tweet details via X API v2."""
        url = f"https://api.x.com/2/tweets/{tweet_id}?tweet.fields={fields}"
        return self._oauth_get(url)

    # --- Kill switch ---

    def _check_kill_switch(self):
        if KILL_SWITCH_PATH.exists():
            reason = KILL_SWITCH_PATH.read_text().strip() or "No reason given"
            raise KillSwitchActive(
                f"Kill switch active: {reason}. "
                f"Remove {KILL_SWITCH_PATH} to resume."
            )

    # --- Persistent rate limiting (SQLite) ---

    def _check_rate_limit(self):
        self._check_kill_switch()
        today = time.strftime('%Y-%m-%d')
        row = self._rate_db.execute(
            "SELECT post_count FROM rate_limits WHERE account = ? AND date = ?",
            (self.account, today),
        ).fetchone()
        count = row[0] if row else 0
        if count >= self.daily_limit:
            raise RuntimeError(
                f"Daily rate limit ({self.daily_limit}) reached for @{self.account}"
            )

    def _increment_rate_count(self):
        today = time.strftime('%Y-%m-%d')
        self._rate_db.execute(
            """INSERT INTO rate_limits (account, date, post_count)
               VALUES (?, ?, 1)
               ON CONFLICT(account, date) DO UPDATE SET post_count = post_count + 1""",
            (self.account, today),
        )
        self._rate_db.commit()

    # --- OAuth 1.0a signing (from x_api.py) ---

    def _percent_encode(self, s: str) -> str:
        return urllib.parse.quote(str(s), safe="")

    def _oauth_signature(self, method: str, url: str, params: dict) -> str:
        sorted_params = "&".join(
            f"{self._percent_encode(k)}={self._percent_encode(v)}"
            for k, v in sorted(params.items())
        )
        base_string = (
            f"{method}&{self._percent_encode(url)}"
            f"&{self._percent_encode(sorted_params)}"
        )
        signing_key = (
            f"{self._percent_encode(self.api_secret)}"
            f"&{self._percent_encode(self.access_token_secret)}"
        )
        sig = hmac.new(
            signing_key.encode(), base_string.encode(), hashlib.sha1
        ).digest()
        return base64.b64encode(sig).decode()

    def _oauth_header(self, method: str, url: str) -> str:
        oauth_params = {
            "oauth_consumer_key": self.api_key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": self.access_token,
            "oauth_version": "1.0",
        }
        sig = self._oauth_signature(method, url, oauth_params)
        oauth_params["oauth_signature"] = sig
        header = "OAuth " + ", ".join(
            f'{self._percent_encode(k)}="{self._percent_encode(v)}"'
            for k, v in sorted(oauth_params.items())
        )
        return header

    def _oauth_post(self, url: str, payload: dict, _retries: int = 0) -> dict:
        body = json.dumps(payload).encode()
        auth = self._oauth_header("POST", url)
        req = Request(url, data=body, method="POST")
        req.add_header("Authorization", auth)
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            error_body = e.read().decode()
            if e.code == 429 and _retries < 3:
                retry_after = int(e.headers.get('Retry-After', 60))
                time.sleep(min(retry_after + 5, 900))  # Cap at 15 min
                return self._oauth_post(url, payload, _retries=_retries + 1)
            raise Exception(f"X API error {e.code}: {error_body}")

    def _oauth_get(self, url: str, _retries: int = 0) -> dict:
        """GET request with OAuth 1.0a signing.

        Query params are included in the signature base string per OAuth spec.
        """
        # Parse URL and query params
        if '?' in url:
            base_url, qs = url.split('?', 1)
            query_params = dict(urllib.parse.parse_qsl(qs))
        else:
            base_url = url
            query_params = {}

        # Build OAuth params + query params for signature
        oauth_params = {
            "oauth_consumer_key": self.api_key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": self.access_token,
            "oauth_version": "1.0",
        }
        all_params = {**oauth_params, **query_params}
        sig = self._oauth_signature("GET", base_url, all_params)
        oauth_params["oauth_signature"] = sig
        header = "OAuth " + ", ".join(
            f'{self._percent_encode(k)}="{self._percent_encode(v)}"'
            for k, v in sorted(oauth_params.items())
        )

        req = Request(url, method="GET")
        req.add_header("Authorization", header)
        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            error_body = e.read().decode()
            if e.code == 429 and _retries < 2:
                time.sleep(5)
                return self._oauth_get(url, _retries=_retries + 1)
            raise Exception(f"X API GET error {e.code}: {error_body}")

    def _oauth_delete(self, url: str, _retries: int = 0) -> dict:
        auth = self._oauth_header("DELETE", url)
        req = Request(url, method="DELETE")
        req.add_header("Authorization", auth)
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            error_body = e.read().decode()
            if e.code == 429 and _retries < 3:
                retry_after = int(e.headers.get('Retry-After', 60))
                time.sleep(min(retry_after + 5, 900))
                return self._oauth_delete(url, _retries=_retries + 1)
            raise Exception(f"X API error {e.code}: {error_body}")
