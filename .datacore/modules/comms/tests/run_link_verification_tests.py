#!/usr/bin/env python3
"""Manual test runner for link verification tests."""
import sys
from pathlib import Path

# Add lib to path
TEST_DIR = Path(__file__).parent
sys.path.insert(0, str(TEST_DIR))

from test_link_verification import (
    test_link_verifier_import,
    test_xposter_with_link_verification,
    test_xposter_without_link_verification,
    test_post_with_valid_link,
    test_post_with_invalid_link,
    test_reply_with_invalid_link,
    test_post_without_links_skips_verification,
    test_skip_verification_flag,
)


def run_test(test_func):
    """Run a single test function."""
    try:
        test_func()
        print(f"✓ {test_func.__name__}")
        return True
    except AssertionError as e:
        print(f"✗ {test_func.__name__}: {e}")
        return False
    except Exception as e:
        print(f"✗ {test_func.__name__}: Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    tests = [
        test_link_verifier_import,
        test_xposter_with_link_verification,
        test_xposter_without_link_verification,
        test_post_with_valid_link,
        test_post_with_invalid_link,
        test_reply_with_invalid_link,
        test_post_without_links_skips_verification,
        test_skip_verification_flag,
    ]

    print("Running link verification tests...\n")
    passed = 0
    failed = 0

    for test in tests:
        if run_test(test):
            passed += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    print('='*60)

    sys.exit(0 if failed == 0 else 1)
