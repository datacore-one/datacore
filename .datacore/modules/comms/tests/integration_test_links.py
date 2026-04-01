#!/usr/bin/env python3
"""Integration test for link verification with real URLs.

Tests link verifier against actual websites to ensure it works correctly.
"""
import sys
from pathlib import Path

# Add lib to path
DATACORE_LIB = Path(__file__).parent.parent.parent.parent / "lib"
sys.path.insert(0, str(DATACORE_LIB))

from link_verifier import LinkVerifier, LinkVerificationError


def test_valid_urls():
    """Test verification of known-good URLs."""
    verifier = LinkVerifier()

    test_cases = [
        ("https://example.com", "Standard test domain"),
        ("https://www.wikipedia.org", "Wikipedia homepage"),
        ("https://github.com", "GitHub homepage"),
    ]

    print("Testing valid URLs...")
    for url, description in test_cases:
        try:
            passed, details = verifier.verify_url(url)
            if passed:
                print(f"  ✓ {description}")
                print(f"    URL: {url}")
                print(f"    Status: {details['status_code']}")
                print(f"    Type: {details['content_type']}")
            else:
                print(f"  ✗ {description} - UNEXPECTED FAILURE")
                print(f"    URL: {url}")
                print(f"    Reason: {details['reason']}")
        except Exception as e:
            print(f"  ✗ {description} - ERROR: {e}")

    print()


def test_invalid_urls():
    """Test verification of known-bad URLs."""
    verifier = LinkVerifier()

    test_cases = [
        ("https://httpstat.us/404", "404 error page"),
        ("https://httpstat.us/500", "500 error page"),
        ("https://httpbin.org/status/403", "403 forbidden"),
    ]

    print("Testing invalid URLs (should fail)...")
    for url, description in test_cases:
        try:
            passed, details = verifier.verify_url(url)
            if not passed:
                print(f"  ✓ {description} - correctly rejected")
                print(f"    URL: {url}")
                print(f"    Reason: {details['reason']}")
            else:
                print(f"  ✗ {description} - UNEXPECTED PASS")
        except Exception as e:
            print(f"  ? {description} - Exception (may be expected): {e}")

    print()


def test_content_types():
    """Test different content types."""
    verifier = LinkVerifier()

    test_cases = [
        ("https://httpbin.org/html", "HTML content", True),
        ("https://httpbin.org/json", "JSON content", False),
        ("https://httpbin.org/xml", "XML content", False),
    ]

    print("Testing content types...")
    for url, description, should_pass in test_cases:
        try:
            passed, details = verifier.verify_url(url)
            status = "✓" if (passed == should_pass) else "✗"
            result = "passed" if passed else "rejected"
            print(f"  {status} {description} - {result}")
            print(f"    URL: {url}")
            print(f"    Type: {details['content_type']}")
            if not passed:
                print(f"    Reason: {details['reason']}")
        except Exception as e:
            print(f"  ? {description} - Error: {e}")

    print()


def test_post_with_links():
    """Test verifying a full post with multiple links."""
    verifier = LinkVerifier()

    test_posts = [
        (
            "Check out https://example.com and https://wikipedia.org for more info!",
            "Post with 2 valid links",
            True
        ),
        (
            "Broken link: https://httpstat.us/404",
            "Post with 404 link",
            False
        ),
        (
            "No links in this post!",
            "Post without links",
            True
        ),
    ]

    print("Testing full post verification...")
    for content, description, should_pass in test_posts:
        try:
            passed, results = verifier.verify_content(content)
            status = "✓" if (passed == should_pass) else "✗"
            result = "passed" if passed else "failed"
            print(f"  {status} {description} - {result}")
            print(f"    Content: {content[:60]}...")
            print(f"    URLs found: {len(results)}")
            for r in results:
                if r.get('reason'):
                    print(f"      - {r['url']}: {r['reason']}")
        except Exception as e:
            print(f"  ? {description} - Error: {e}")

    print()


def test_edge_cases():
    """Test edge cases and special scenarios."""
    verifier = LinkVerifier()

    test_cases = [
        ("https://httpbin.org/redirect/1", "URL with redirect"),
        ("https://httpbin.org/delay/2", "Slow response"),
    ]

    print("Testing edge cases...")
    for url, description in test_cases:
        try:
            passed, details = verifier.verify_url(url)
            print(f"  ✓ {description} - {('passed' if passed else 'failed')}")
            print(f"    URL: {url}")
            print(f"    Status: {details['status_code']}")
            if details.get('redirect_url'):
                print(f"    Redirected to: {details['redirect_url']}")
            if details.get('reason'):
                print(f"    Reason: {details['reason']}")
        except Exception as e:
            print(f"  ✗ {description} - Error: {e}")

    print()


if __name__ == '__main__':
    print("="*60)
    print("Link Verification Integration Tests")
    print("Testing against real URLs...")
    print("="*60)
    print()

    try:
        test_valid_urls()
        test_invalid_urls()
        test_content_types()
        test_post_with_links()
        test_edge_cases()

        print("="*60)
        print("Integration tests completed")
        print("="*60)

    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
