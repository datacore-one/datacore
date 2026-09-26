"""MEM-50: Research never files notes from a page that failed to load. A failed
capture is reported instead of turned into a note.

Kind: deterministic. The real orchestrator main() over a tmp queue, with the
HTTP layer (download_public) returning what a failed load really returns: a
bot-challenge page ("Just a moment... Enable JavaScript and cookies"), an
"Access Denied" 403 body and a paywall ("Subscribe to continue reading") --
each long enough to pass the 500-character floor -- beside one real article.
The analysis step is stubbed as a model that summarises whatever it is given
(which is what a model does with an error page).
  pass: no literature note or zettel is written for the three failed pages;
        each stays queued (TODO/WAITING) carrying a reason, and the run
        reports it (Firm group alert); the real article is filed as a note.

Seeded failure: today's fetch_url accepts any body over 500 characters, so an
error page is analysed and filed as knowledge.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _research_harness as H  # noqa: E402

GROUP = "-100200"
PAD = " Lorem ipsum filler navigation footer links cookie settings privacy terms." * 12
PAGES = {
    "https://example.test/challenge": "<html><title>Just a moment...</title><body>Checking your browser before "
                                      "accessing example.test. Enable JavaScript and cookies to continue." + PAD,
    "https://example.test/denied": "<html><title>403 Forbidden</title><body>Access Denied. You don't have "
                                   "permission to access this resource on this server." + PAD,
    "https://example.test/paywall": "<html><title>Subscribe</title><body>Subscribe to continue reading. This "
                                    "article is for subscribers only. Already a subscriber? Sign in." + PAD,
    "https://example.test/real": "<html><title>Data unions</title><body>Data unions let people pool personal "
                                 "data and bargain collectively. " + ("The model pays members per query. " * 30),
}
TITLES = {"challenge": "Challenge page", "denied": "Denied page", "paywall": "Paywalled article",
          "real": "Data unions"}

ITEM = """** TODO [#B] Read: {title}
   :PROPERTIES:
   :ID: mem50-{key}
   :CREATED: [{today}]
   :END:
   Link: https://example.test/{key}
"""


def _section(text, item_id):
    lines = text.split("\n")
    at = next((i for i, l in enumerate(lines) if l.strip() == f":ID: {item_id}"), None)
    if at is None:
        return None
    head = max(i for i in range(at + 1) if re.match(r"^\*+\s", lines[i]))
    end = next((j for j in range(head + 1, len(lines)) if re.match(r"^\*+\s", lines[j])), len(lines))
    return "\n".join(lines[head:end])


def test_a_page_that_failed_to_load_never_becomes_a_note(tmp_path, monkeypatch):
    R = H.load()
    t = H.point_at(R, tmp_path, monkeypatch)
    today = date.today().isoformat()
    t.org.write_text("#+TITLE: Research\n* Queue\n" + "".join(
        ITEM.format(title=v, key=k, today=today) for k, v in TITLES.items()), encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "000:eval")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("ALERT_CHAT_ID", GROUP)
    posts = H.capture_http(monkeypatch)
    monkeypatch.setattr(R, "download_public", lambda url, **kw: PAGES[url].encode("utf-8"))

    def summarise_anything(item, content):
        key = next(k for k in TITLES if f"example.test/{k}" in str(item))
        return H.analysis(TITLES[key])

    monkeypatch.setattr(R, "process_item", summarise_anything)
    H.run_main(R, monkeypatch, "--no-podcast")

    notes = [p for d in (R.LITERATURE_DIR, R.ZETTEL_DIR) if d.exists() for p in d.rglob("*.md")]
    names = " ".join(p.name for p in notes).lower()
    filed = [k for k in ("challenge", "denied", "paywall") if TITLES[k].lower().replace(" ", "-") in names]
    assert not filed, f"notes were filed from pages that failed to load: {filed} ({[p.name for p in notes]})"
    assert "data-unions" in names, f"the real article was not filed: {[p.name for p in notes]}"

    org = t.org.read_text(encoding="utf-8")
    for k in ("challenge", "denied", "paywall"):
        sec = _section(org, f"mem50-{k}")
        assert sec is not None and re.match(r"^\*+ (TODO|WAITING) ", sec), f"{k}: failed capture left the queue:\n{sec}"
    told = " ".join(f.get("text", "") for _, f in posts if f.get("chat_id") == GROUP)
    missing = [TITLES[k] for k in ("challenge", "denied", "paywall") if TITLES[k] not in told]
    assert not missing, f"failed captures not reported: {missing}"
