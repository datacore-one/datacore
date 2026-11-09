"""KNW-3: If a research item fails, it stays queued with the reason and I'm told in The
Firm group. Nothing is dropped.

Kind: deterministic. The real orchestrator main() runs on a tmp queue of two items;
one cannot be fetched, the other succeeds. Outbound HTTP is captured, never sent.
The Firm group is ALERT_CHAT_ID (see MSG-1); TELEGRAM_CHAT_ID is the 1:1 chat.

Seeded failure: the failed item is dropped from the queue (or marked DONE), or the
failure is only logged / sent to the 1:1 chat. On 2026-09-26 three failed articles
stayed WAITING but the run was reported through Winston's 1:1 message only.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _research_harness as H  # noqa: E402

GROUP = "-100200"
ONE_TO_ONE = "111"

QUEUE = """#+TITLE: Research
* Queue
** TODO [#A] Read: Alpha paywalled
   :PROPERTIES:
   :ID: knw3-alpha
   :CREATED: [{today}]
   :END:
   Link: https://example.test/alpha
** TODO [#B] Read: Beta open
   :PROPERTIES:
   :ID: knw3-beta
   :CREATED: [{today}]
   :END:
   Link: https://example.test/beta
"""


def _section(text, item_id):
    lines = text.split("\n")
    at = next((i for i, l in enumerate(lines) if l.strip() == f":ID: {item_id}"), None)
    if at is None:
        return None
    head = max(i for i in range(at + 1) if re.match(r"^\*+\s", lines[i]))
    end = next((j for j in range(head + 1, len(lines)) if re.match(r"^\*+\s", lines[j])), len(lines))
    return "\n".join(lines[head:end])


def test_a_failed_item_stays_queued_with_its_reason_and_the_group_is_told(tmp_path, monkeypatch):
    R = H.load()
    t = H.point_at(R, tmp_path, monkeypatch)
    t.org.write_text(QUEUE.format(today=date.today().isoformat()), encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "000:eval")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", ONE_TO_ONE)
    monkeypatch.setenv("ALERT_CHAT_ID", GROUP)
    posts = H.capture_http(monkeypatch)
    monkeypatch.setattr(R, "fetch_url", lambda url: None if url.endswith("alpha") else "Body " * 50)
    monkeypatch.setattr(R, "process_item", lambda item, content: H.analysis("Beta open"))

    H.run_main(R, monkeypatch, "--no-podcast")

    sec = _section(t.org.read_text(encoding="utf-8"), "knw3-alpha")
    assert sec is not None, "the failed item was dropped from the queue"
    assert re.match(r"^\*+ (TODO|WAITING) ", sec), f"the failed item left the queue:\n{sec}"
    assert re.search(r":(RESULT|FETCH_ATTEMPTS|ANALYSIS_ATTEMPTS|LAST_ERROR|REASON):\s*\S", sec), (
        f"the failed item carries no reason:\n{sec}")

    to_group = [f.get("text", "") for _, f in posts if f.get("chat_id") == GROUP]
    assert any("Alpha paywalled" in text for text in to_group), (
        "the failure was not reported to The Firm group (ALERT_CHAT_ID); posts were: "
        + repr([(f.get("chat_id"), f.get("text", "")[:80]) for _, f in posts]))
