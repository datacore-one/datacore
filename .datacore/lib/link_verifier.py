#!/usr/bin/env python3
"""
URL verification library for communications pipeline.

Verifies all URLs in content before scheduling via Late API.
Rejects posts with non-200 responses or non-user-facing content.
"""

import re
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from typing import List, Dict, Tuple


class LinkVerificationError(Exception):
    """Raised when link verification fails."""
    pass


class LinkVerifier:
    """Verify URLs before scheduling social posts."""

    # Content types that are acceptable for user-facing content
    ALLOWED_CONTENT_TYPES = [
        'text/html',
        'application/pdf',
        'application/xhtml+xml',
        'text/plain',
        'image/',  # Prefix match
        'video/',  # Prefix match
    ]

    # Patterns indicating non-public content
    REJECT_PATTERNS = [
        r'/login',
        r'/signin',
        r'/auth',
        r'/admin',
        r'/dashboard',
        r'/console',
        r'accounts\.google\.com',
        r'login\.microsoftonline\.com',
        r'/wp-admin',
    ]

    # Timeout for requests (seconds)
    TIMEOUT = 10

    def __init__(self, user_agent: str = None):
        """
        Initialize link verifier.

        Args:
            user_agent: Custom User-Agent header (default: generic bot)
        """
        self.user_agent = user_agent or (
            'Mozilla/5.0 (compatible; LinkVerifier/1.0; +https://datacore.ai/bot)'
        )

    def extract_urls(self, text: str) -> List[str]:
        """
        Extract all URLs from text.

        Args:
            text: Content to scan for URLs

        Returns:
            List of URLs found
        """
        # Match http:// and https:// URLs
        url_pattern = r'https?://[^\s\)\]\}]+'
        urls = re.findall(url_pattern, text)

        # Clean up trailing punctuation that's not part of URL
        cleaned = []
        for url in urls:
            # Remove trailing punctuation if not part of path/query
            url = re.sub(r'[.,;:!?\'\"]$', '', url)
            cleaned.append(url)

        return list(set(cleaned))  # Deduplicate

    def verify_url(self, url: str) -> Tuple[bool, Dict]:
        """
        Verify a single URL.

        Args:
            url: URL to verify

        Returns:
            Tuple of (passed: bool, details: dict)

        Details dict contains:
            - status_code: HTTP status code
            - content_type: Response Content-Type header
            - redirect_url: Final URL after redirects (if any)
            - error: Error message (if failed)
            - reason: Rejection reason (if failed)
        """
        details = {
            'url': url,
            'status_code': None,
            'content_type': None,
            'redirect_url': None,
            'error': None,
            'reason': None,
        }

        try:
            # Check for reject patterns BEFORE making request
            for pattern in self.REJECT_PATTERNS:
                if re.search(pattern, url, re.IGNORECASE):
                    details['reason'] = f"URL matches reject pattern: {pattern}"
                    return False, details

            # Make HEAD request first (faster)
            req = Request(url, method='HEAD')
            req.add_header('User-Agent', self.user_agent)

            try:
                with urlopen(req, timeout=self.TIMEOUT) as response:
                    details['status_code'] = response.status
                    details['content_type'] = response.headers.get('Content-Type', '')
                    details['redirect_url'] = response.url if response.url != url else None

            except HTTPError as e:
                # Some servers don't support HEAD, try GET
                if e.code == 405:
                    req = Request(url, method='GET')
                    req.add_header('User-Agent', self.user_agent)
                    with urlopen(req, timeout=self.TIMEOUT) as response:
                        details['status_code'] = response.status
                        details['content_type'] = response.headers.get('Content-Type', '')
                        details['redirect_url'] = response.url if response.url != url else None
                else:
                    raise

            # Check status code
            if details['status_code'] != 200:
                details['reason'] = f"Non-200 status code: {details['status_code']}"
                return False, details

            # Check content type
            content_type = details['content_type'].lower()
            is_allowed = any(
                content_type.startswith(allowed.lower())
                for allowed in self.ALLOWED_CONTENT_TYPES
            )

            if not is_allowed:
                details['reason'] = f"Non-user-facing content type: {content_type}"
                return False, details

            # Check if redirected to auth page
            if details['redirect_url']:
                for pattern in self.REJECT_PATTERNS:
                    if re.search(pattern, details['redirect_url'], re.IGNORECASE):
                        details['reason'] = f"Redirected to auth/admin page: {details['redirect_url']}"
                        return False, details

            # All checks passed
            return True, details

        except HTTPError as e:
            details['status_code'] = e.code
            details['error'] = f"HTTP {e.code}: {e.reason}"
            details['reason'] = f"HTTP error {e.code}"
            return False, details

        except URLError as e:
            details['error'] = f"URL error: {str(e.reason)}"
            details['reason'] = "Network error or invalid URL"
            return False, details

        except TimeoutError:
            details['error'] = f"Request timeout (>{self.TIMEOUT}s)"
            details['reason'] = "Timeout - service may be slow or unavailable"
            return False, details

        except Exception as e:
            details['error'] = f"Unexpected error: {str(e)}"
            details['reason'] = "Verification failed"
            return False, details

    def verify_content(self, text: str, fast_fail: bool = False) -> Tuple[bool, List[Dict]]:
        """
        Verify all URLs in content.

        Args:
            text: Content to verify
            fast_fail: Stop at first failure (default: check all)

        Returns:
            Tuple of (all_passed: bool, results: list)

        Results list contains verification details for each URL.
        """
        urls = self.extract_urls(text)

        if not urls:
            # No URLs found - pass by default
            return True, []

        results = []
        all_passed = True

        for url in urls:
            passed, details = self.verify_url(url)
            results.append(details)

            if not passed:
                all_passed = False
                if fast_fail:
                    break

            # Rate limiting - be nice to servers
            time.sleep(0.5)

        return all_passed, results

    def verify_or_raise(self, text: str) -> List[Dict]:
        """
        Verify all URLs and raise exception if any fail.

        Args:
            text: Content to verify

        Returns:
            List of verification results

        Raises:
            LinkVerificationError: If any URL fails verification
        """
        all_passed, results = self.verify_content(text, fast_fail=False)

        if not all_passed:
            failed = [r for r in results if r.get('reason')]
            error_msg = "Link verification failed:\n"
            for r in failed:
                error_msg += f"  - {r['url']}: {r['reason']}\n"
            raise LinkVerificationError(error_msg)

        return results


# Standalone CLI usage
def main():
    """CLI interface for link verification."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Verify URLs in text content before scheduling posts'
    )
    parser.add_argument(
        'text',
        nargs='?',
        help='Text to verify (or read from stdin)'
    )
    parser.add_argument(
        '--fast-fail',
        action='store_true',
        help='Stop at first failure'
    )
    parser.add_argument(
        '--url',
        help='Verify a single URL directly'
    )

    args = parser.parse_args()

    verifier = LinkVerifier()

    if args.url:
        # Single URL mode
        print(f"Verifying: {args.url}")
        passed, details = verifier.verify_url(args.url)

        print(f"\nStatus: {'PASS' if passed else 'FAIL'}")
        print(f"HTTP Status: {details['status_code']}")
        print(f"Content-Type: {details['content_type']}")
        if details['redirect_url']:
            print(f"Redirected to: {details['redirect_url']}")
        if details['reason']:
            print(f"Reason: {details['reason']}")
        if details['error']:
            print(f"Error: {details['error']}")

        sys.exit(0 if passed else 1)

    # Content verification mode
    if args.text:
        text = args.text
    else:
        # Read from stdin
        text = sys.stdin.read()

    if not text.strip():
        print("ERROR: No content provided", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning content for URLs...")
    urls = verifier.extract_urls(text)
    print(f"Found {len(urls)} URL(s)\n")

    if not urls:
        print("No URLs found - nothing to verify")
        sys.exit(0)

    all_passed, results = verifier.verify_content(text, fast_fail=args.fast_fail)

    # Print results
    for i, result in enumerate(results, 1):
        status = "✓ PASS" if not result.get('reason') else "✗ FAIL"
        print(f"{i}. {status} - {result['url']}")
        print(f"   Status: {result['status_code']}")
        print(f"   Type: {result['content_type']}")
        if result.get('reason'):
            print(f"   Reason: {result['reason']}")
        if result.get('error'):
            print(f"   Error: {result['error']}")
        print()

    # Summary
    passed_count = len([r for r in results if not r.get('reason')])
    failed_count = len(results) - passed_count

    print("=" * 60)
    print(f"SUMMARY: {passed_count} passed, {failed_count} failed")
    print("=" * 60)

    if all_passed:
        print("\n✓ All links verified - safe to schedule")
        sys.exit(0)
    else:
        print("\n✗ Link verification FAILED - do NOT schedule")
        print("\nFix failed links before scheduling to Late API")
        sys.exit(1)


if __name__ == '__main__':
    main()
