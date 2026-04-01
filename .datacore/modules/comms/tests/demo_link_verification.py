#!/usr/bin/env python3
"""
Demo script showing link verification in action.

This demonstrates the link verification gate without actually posting to X.
"""
import sys
from pathlib import Path

# Add lib to path
DATACORE_LIB = Path(__file__).parent.parent.parent.parent / "lib"
sys.path.insert(0, str(DATACORE_LIB))

from link_verifier import LinkVerifier, LinkVerificationError


def demo_verification():
    """Demonstrate link verification with various examples."""
    verifier = LinkVerifier()

    print("="*70)
    print("LINK VERIFICATION GATE DEMONSTRATION")
    print("="*70)
    print()

    # Example 1: Valid post
    print("Example 1: Valid post with working link")
    print("-" * 70)
    post1 = "Privacy by design: https://fairdatasociety.org"
    print(f"Post: {post1}")
    print()
    try:
        all_passed, results = verifier.verify_content(post1)
        if all_passed:
            print("✓ VERIFICATION PASSED - Post would be sent")
            for r in results:
                print(f"  - {r['url']}: {r['status_code']} {r['content_type']}")
        else:
            print("✗ VERIFICATION FAILED - Post would be rejected")
    except Exception as e:
        print(f"Error: {e}")
    print()
    print()

    # Example 2: Broken link
    print("Example 2: Post with broken link (404)")
    print("-" * 70)
    post2 = "More info: https://httpstat.us/404"
    print(f"Post: {post2}")
    print()
    try:
        verifier.verify_or_raise(post2)
        print("✓ VERIFICATION PASSED - Post would be sent")
    except LinkVerificationError as e:
        print("✗ VERIFICATION FAILED - Post would be rejected")
        print(f"Reason: {e}")
    print()
    print()

    # Example 3: API endpoint (non-user-facing)
    print("Example 3: Post with API endpoint (JSON response)")
    print("-" * 70)
    post3 = "Check the data: https://httpbin.org/json"
    print(f"Post: {post3}")
    print()
    try:
        verifier.verify_or_raise(post3)
        print("✓ VERIFICATION PASSED - Post would be sent")
    except LinkVerificationError as e:
        print("✗ VERIFICATION FAILED - Post would be rejected")
        print(f"Reason: {e}")
    print()
    print()

    # Example 4: Multiple links
    print("Example 4: Post with multiple links")
    print("-" * 70)
    post4 = "Privacy resources: https://fairdatasociety.org and https://github.com"
    print(f"Post: {post4}")
    print()
    try:
        all_passed, results = verifier.verify_content(post4)
        if all_passed:
            print("✓ VERIFICATION PASSED - Post would be sent")
            print(f"  Verified {len(results)} links:")
            for r in results:
                print(f"    - {r['url']}: {r['status_code']} {r['content_type']}")
        else:
            print("✗ VERIFICATION FAILED - Post would be rejected")
    except Exception as e:
        print(f"Error: {e}")
    print()
    print()

    # Example 5: No links
    print("Example 5: Post without links")
    print("-" * 70)
    post5 = "Privacy by architecture, not by promise."
    print(f"Post: {post5}")
    print()
    try:
        all_passed, results = verifier.verify_content(post5)
        print("✓ NO LINKS FOUND - Verification skipped, post would be sent")
    except Exception as e:
        print(f"Error: {e}")
    print()
    print()

    print("="*70)
    print("SUMMARY")
    print("="*70)
    print()
    print("Link verification gate protects against:")
    print("  - Broken links (404, 500, timeouts)")
    print("  - Non-user-facing content (JSON, XML, APIs)")
    print("  - Auth-walled pages (login, admin panels)")
    print("  - Private/internal URLs")
    print()
    print("Posts are rejected BEFORE sending to X/Twitter API.")
    print("This protects brand credibility by ensuring all shared links work.")
    print()


if __name__ == '__main__':
    try:
        demo_verification()
    except KeyboardInterrupt:
        print("\n\nDemo interrupted")
        sys.exit(1)
