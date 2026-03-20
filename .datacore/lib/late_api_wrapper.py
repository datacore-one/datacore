#!/usr/bin/env python3
"""
Late API wrapper with link verification gate.

Prevents scheduling posts with broken or non-public links.
"""

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from typing import Dict, Optional, List

# Import link verifier
from link_verifier import LinkVerifier, LinkVerificationError


class LateAPIClient:
    """Late API client with built-in link verification."""

    def __init__(self, api_key: str, verify_links: bool = True):
        """
        Initialize Late API client.

        Args:
            api_key: Late API key
            verify_links: Enable link verification gate (default: True)
        """
        self.api_key = api_key
        self.api_base = "https://getlate.dev/api/v1"
        self.verify_links = verify_links
        self.verifier = LinkVerifier() if verify_links else None

        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _api_request(self, method: str, path: str, data: Optional[Dict] = None) -> Dict:
        """Make API request to Late."""
        url = f"{self.api_base}{path}"
        body = json.dumps(data).encode() if data else None
        req = Request(url, data=body, headers=self.headers, method=method)

        try:
            with urlopen(req) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            error_body = e.read().decode()
            raise Exception(f"Late API error {e.code}: {error_body}")

    def create_post(
        self,
        content: str,
        platforms: List[str] = None,
        scheduled_time: str = None,
        media_urls: List[str] = None,
        skip_verification: bool = False
    ) -> Dict:
        """
        Create post with link verification gate.

        Args:
            content: Post content
            platforms: List of platform names (default: ['twitter'])
            scheduled_time: ISO 8601 timestamp (default: post now)
            media_urls: List of media URLs to attach
            skip_verification: Skip link verification (use with caution)

        Returns:
            Late API response with post details

        Raises:
            LinkVerificationError: If link verification fails
            Exception: If API request fails
        """
        # GATE: Verify links before scheduling
        if self.verify_links and not skip_verification:
            print("🔍 Verifying links in post content...")
            try:
                results = self.verifier.verify_or_raise(content)
                if results:
                    print(f"✓ {len(results)} link(s) verified")
            except LinkVerificationError as e:
                print(f"\n✗ LINK VERIFICATION FAILED")
                print(str(e))
                print("\nPost NOT scheduled. Fix links and retry.")
                raise

        # Build request
        platforms = platforms or ['twitter']
        # Late API requires platform + accountId per platform entry
        # Account IDs are looked up from LATE_ACCOUNT_IDS env var (JSON)
        # or fall back to known accounts
        account_ids = {
            "twitter": "6978cf6d77637c5c857c867d",  # @FairDataSociety
        }
        import os as _os
        try:
            custom = json.loads(_os.environ.get("LATE_ACCOUNT_IDS", "{}"))
            account_ids.update(custom)
        except Exception:
            pass

        data = {
            "content": content,
            "platforms": [
                {"platform": p, "accountId": account_ids.get(p, "")}
                for p in platforms
            ],
        }

        if scheduled_time:
            data["scheduledTime"] = scheduled_time

        if media_urls:
            data["mediaItems"] = [{"type": "image", "url": url} for url in media_urls]

        # Create post
        print("📤 Scheduling post to Late API...")
        response = self._api_request("POST", "/posts", data)

        post_id = response.get("post", {}).get("_id")
        status = response.get("post", {}).get("status")

        print(f"✓ Post created: {post_id} (status: {status})")
        return response

    def update_post(self, post_id: str, content: str = None, media_urls: List[str] = None) -> Dict:
        """
        Update existing post with link verification.

        Args:
            post_id: Post ID to update
            content: New content (optional)
            media_urls: New media URLs (optional)

        Returns:
            Late API response

        Raises:
            LinkVerificationError: If link verification fails
        """
        # Verify links if content is being updated
        if content and self.verify_links:
            print("🔍 Verifying links in updated content...")
            self.verifier.verify_or_raise(content)

        data = {}
        if content:
            data["content"] = content
        if media_urls:
            data["mediaItems"] = [{"type": "image", "url": url} for url in media_urls]

        response = self._api_request("PUT", f"/posts/{post_id}", data)
        print(f"✓ Post {post_id} updated")
        return response

    def get_posts(self, page: int = 1) -> Dict:
        """Get posts from Late API."""
        return self._api_request("GET", f"/posts?page={page}")

    def delete_post(self, post_id: str) -> Dict:
        """Delete a post."""
        return self._api_request("DELETE", f"/posts/{post_id}")


# CLI usage
def main():
    """CLI for creating posts with link verification."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Create Late API post with link verification'
    )
    parser.add_argument('content', help='Post content')
    parser.add_argument('--api-key', required=True, help='Late API key')
    parser.add_argument('--platform', default='twitter', help='Platform (default: twitter)')
    parser.add_argument('--schedule', help='Schedule time (ISO 8601)')
    parser.add_argument('--skip-verify', action='store_true', help='Skip link verification')
    parser.add_argument('--media', nargs='+', help='Media URLs to attach')

    args = parser.parse_args()

    client = LateAPIClient(args.api_key)

    try:
        result = client.create_post(
            content=args.content,
            platforms=[args.platform],
            scheduled_time=args.schedule,
            media_urls=args.media,
            skip_verification=args.skip_verify
        )
        print(f"\n✓ Success!")
        print(json.dumps(result, indent=2))

    except LinkVerificationError as e:
        print(f"\n✗ POST REJECTED: {e}", file=sys.stderr)
        sys.exit(1)

    except Exception as e:
        print(f"\n✗ ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
