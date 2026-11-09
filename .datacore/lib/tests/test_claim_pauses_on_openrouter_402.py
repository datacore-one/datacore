"""An empty OpenRouter account pauses claiming instead of feeding a retry loop.

2026-10-26 18:20-22:51: Winston's claim jobs ran self-repair tasks through
Hermes on OpenRouter, failed, retried, and spent about 21.9 USD until the
account hit zero. From then on every run got HTTP 402, but Hermes swallows the
provider error and the wrapper reported only "incomplete provider result", so
the claim loop never saw a credit failure and kept going. The 402 text is in
Hermes' own errors.log; the wrapper now passes it on.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "executors"))

import claim_backoff as B  # noqa: E402
import hermes_oneshot as H  # noqa: E402

LINE_402 = ("2026-10-27 10:50:39 ERROR run_agent: Error code: 402 - 'This request requires more "
            "credits, or fewer max_tokens. You requested up to 131072 tokens, but can only afford 4535.'")


def test_the_openrouter_402_reply_is_a_credit_failure():
    assert B.is_access_failure(f"hermes exited 1: hermes_oneshot: incomplete provider result; {LINE_402}")


def test_the_wrapper_reports_only_errors_logged_during_its_run(tmp_path):
    log = tmp_path / "errors.log"
    log.write_text("2026-10-26 09:00:00 ERROR run_agent: Error code: 429 - old, not this run\n")
    offset = H.log_offset(log)
    with log.open("a") as f:
        f.write("2026-10-27 10:50:30 WARNING run_agent: retrying\n")
        f.write(LINE_402 + "\n")
    assert H.provider_error_since(log, offset) == LINE_402[LINE_402.index("Error code"):]


def test_no_new_error_means_nothing_to_report(tmp_path):
    log = tmp_path / "errors.log"
    log.write_text("2026-10-26 09:00:00 ERROR run_agent: Error code: 402 - from yesterday\n")
    assert H.provider_error_since(log, H.log_offset(log)) is None
    assert H.provider_error_since(tmp_path / "missing.log", 0) is None
