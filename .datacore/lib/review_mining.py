#!/usr/bin/env python3
"""
Review Mining — extract market signal from public business reviews.

A shared Datacore primitive for fetching customer reviews and structuring them
into a rich BusinessReviewProfile usable by multiple consumers:

  - megaphone-websites: cold outreach personalization (pain points → email hooks)
  - forge: product discovery (find unowned ground in adjacent markets)
  - crm: company context enrichment for relationship notes
  - strategy-audit: competitive positioning research

ARCHITECTURE: adapter pattern for fetching, separate LLM analysis layer.
The fetch and analyze stages are decoupled so callers can compose them.

ADAPTERS (in priority order, auto-selected based on env):
  1. PlaywrightScraperAdapter — default. Free, fragile. Breaks when Google updates UI.
  2. SerpAPIAdapter — opt-in via SERPAPI_API_KEY env. Reliable, paid (~$50/mo typical).

NOTE on Google Places API: returns max 5 reviews per place. Useless for review
mining at scale. Not implemented as an adapter — use SerpAPI for paid mode.

USAGE:
    from datacore.lib.review_mining import analyze_business_reviews

    profile = analyze_business_reviews(
        business_name="Joe's Pizza",
        location="New York, NY",
        max_reviews=50,
    )

    for pain in profile.pain_points:
        print(f"[{pain.severity}/5] {pain.theme}: {pain.quote}")

CLI:
    python -m datacore.lib.review_mining "Joe's Pizza" --location "NYC" --output summary
    python -m datacore.lib.review_mining "Joe's Pizza" --output json > profile.json

LEGAL NOTE: scraping Google Maps may violate Google's Terms of Service. Use
SerpAPI (or another compliant provider) for production use. The PlaywrightScraperAdapter
is provided for personal/research use only and may break at any time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

# ============================================================
# Data model
# ============================================================


@dataclass
class BusinessIdentity:
    name: str
    address: Optional[str] = None
    place_id: Optional[str] = None
    category: Optional[str] = None
    price_tier: Optional[str] = None  # $, $$, $$$, $$$$
    website: Optional[str] = None
    phone: Optional[str] = None


@dataclass
class RawReview:
    review_id: str
    rating: int  # 1-5
    text: str
    timestamp: str  # ISO date or relative ("2 weeks ago")
    author_name: Optional[str] = None
    response_text: Optional[str] = None  # business reply if any
    response_timestamp: Optional[str] = None


@dataclass
class ReviewSummary:
    total_count: int
    avg_rating: float
    rating_distribution: dict[int, int]  # {1: 5, 2: 3, ...}
    fetched_at: str  # ISO datetime


@dataclass
class PainPoint:
    quote: str  # exact customer words (verbatim)
    theme: str  # short normalized label e.g. "slow service", "billing errors"
    severity: int  # 1-5
    frequency: int  # how many reviews mention this theme
    recency: str  # "30d" / "90d" / "180d" / "all-time"
    customer_segment: Optional[str] = None
    source_review_ids: list[str] = field(default_factory=list)


@dataclass
class Strength:
    quote: str  # exact customer words
    theme: str
    frequency: int
    source_review_ids: list[str] = field(default_factory=list)


@dataclass
class TrendData:
    period_30d: Optional[float] = None  # avg sentiment 0-1, None if no data
    period_90d: Optional[float] = None
    period_180d: Optional[float] = None
    period_365d: Optional[float] = None


@dataclass
class BusinessReviewProfile:
    business: BusinessIdentity
    review_summary: ReviewSummary
    pain_points: list[PainPoint] = field(default_factory=list)
    strengths: list[Strength] = field(default_factory=list)
    sentiment_trend: TrendData = field(default_factory=TrendData)
    customer_segments: dict[str, int] = field(default_factory=dict)
    response_pattern: dict[str, Any] = field(default_factory=dict)
    competitive_signals: list[str] = field(default_factory=list)
    raw_reviews: list[RawReview] = field(default_factory=list)


# ============================================================
# Adapter protocol
# ============================================================


class ReviewAdapter(Protocol):
    name: str

    def fetch(
        self,
        business_name: str,
        location: Optional[str],
        max_reviews: int,
    ) -> tuple[BusinessIdentity, list[RawReview]]:
        ...


class AdapterError(RuntimeError):
    pass


# ============================================================
# SerpAPI adapter (opt-in, paid, reliable)
# ============================================================


class SerpAPIAdapter:
    """
    Fetches reviews via SerpAPI's google_maps_reviews engine.
    Requires SERPAPI_API_KEY env var. ~$50/month for typical volume.
    Returns up to 50 reviews per call.
    """

    name = "serpapi"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("SERPAPI_API_KEY")
        if not self.api_key:
            raise AdapterError("SERPAPI_API_KEY not set")

    def fetch(
        self,
        business_name: str,
        location: Optional[str],
        max_reviews: int,
    ) -> tuple[BusinessIdentity, list[RawReview]]:
        try:
            import urllib.parse
            import urllib.request
        except ImportError as e:
            raise AdapterError(f"stdlib import failed: {e}")

        # Step 1: search Google Maps for the place to get place_id
        search_query = f"{business_name} {location}" if location else business_name
        search_url = (
            "https://serpapi.com/search.json?"
            + urllib.parse.urlencode(
                {
                    "engine": "google_maps",
                    "q": search_query,
                    "type": "search",
                    "api_key": self.api_key,
                }
            )
        )
        with urllib.request.urlopen(search_url, timeout=30) as r:
            search_data = json.loads(r.read().decode())

        place = None
        if "place_results" in search_data:
            place = search_data["place_results"]
        elif "local_results" in search_data and search_data["local_results"]:
            place = search_data["local_results"][0]

        if not place:
            raise AdapterError(f"No business found matching: {search_query}")

        place_id = place.get("place_id") or place.get("data_id")
        if not place_id:
            raise AdapterError(f"Found business but no place_id: {place}")

        business = BusinessIdentity(
            name=place.get("title", business_name),
            address=place.get("address"),
            place_id=place_id,
            category=(place.get("type") or [None])[0] if isinstance(place.get("type"), list) else place.get("type"),
            price_tier=place.get("price"),
            website=place.get("website"),
            phone=place.get("phone"),
        )

        # Step 2: fetch reviews via google_maps_reviews engine
        raw_reviews: list[RawReview] = []
        next_token: Optional[str] = None

        while len(raw_reviews) < max_reviews:
            params = {
                "engine": "google_maps_reviews",
                "place_id": place_id,
                "api_key": self.api_key,
                "sort_by": "newestFirst",
            }
            if next_token:
                params["next_page_token"] = next_token
            review_url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(review_url, timeout=30) as r:
                review_data = json.loads(r.read().decode())

            page = review_data.get("reviews", [])
            if not page:
                break
            for rv in page:
                raw_reviews.append(
                    RawReview(
                        review_id=rv.get("review_id", f"rev-{len(raw_reviews)}"),
                        rating=int(rv.get("rating", 0)),
                        text=rv.get("snippet") or rv.get("description") or "",
                        timestamp=rv.get("date", ""),
                        author_name=(rv.get("user") or {}).get("name") if isinstance(rv.get("user"), dict) else None,
                        response_text=(rv.get("response") or {}).get("snippet") if isinstance(rv.get("response"), dict) else None,
                        response_timestamp=(rv.get("response") or {}).get("date") if isinstance(rv.get("response"), dict) else None,
                    )
                )
                if len(raw_reviews) >= max_reviews:
                    break

            next_token = (review_data.get("serpapi_pagination") or {}).get("next_page_token")
            if not next_token:
                break

        return business, raw_reviews


# ============================================================
# Playwright scraper adapter (default, free, fragile)
# ============================================================


class PlaywrightScraperAdapter:
    """
    Fetches reviews by scraping Google Maps via Playwright.
    Free but FRAGILE — breaks when Google updates the UI. May violate Google ToS.
    For production use, prefer SerpAPIAdapter.

    Requires: pip install playwright && playwright install chromium
    """

    name = "playwright"

    def fetch(
        self,
        business_name: str,
        location: Optional[str],
        max_reviews: int,
    ) -> tuple[BusinessIdentity, list[RawReview]]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise AdapterError(
                "Playwright not installed. Install with:\n"
                "  pip install playwright && playwright install chromium\n"
                "Or set SERPAPI_API_KEY to use the SerpAPI adapter instead."
            )

        search_query = f"{business_name} {location}" if location else business_name
        encoded = search_query.replace(" ", "+")
        url = f"https://www.google.com/maps/search/{encoded}"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=60_000)

            # Click first result if multiple shown
            try:
                page.wait_for_selector('[role="article"]', timeout=10_000)
                first = page.query_selector('[role="article"]')
                if first:
                    first.click()
                    page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass

            # Extract business identity
            business = BusinessIdentity(
                name=_safe_text(page, 'h1') or business_name,
                address=_safe_text(page, '[data-item-id="address"]'),
                category=_safe_text(page, 'button[jsaction*="pane.rating.category"]'),
            )

            # Click "Reviews" tab
            try:
                reviews_button = page.query_selector('button[jsaction*="reviewChart.moreReviews"]') or \
                                 page.query_selector('button[aria-label*="Reviews"]')
                if reviews_button:
                    reviews_button.click()
                    page.wait_for_timeout(2000)
            except Exception:
                pass

            # Scroll to load reviews
            raw_reviews: list[RawReview] = []
            scrollable = page.query_selector('div[role="main"]')
            seen_ids = set()
            stalls = 0
            while len(raw_reviews) < max_reviews and stalls < 5:
                review_elements = page.query_selector_all('[data-review-id]')
                added_this_round = 0
                for el in review_elements:
                    rid = el.get_attribute("data-review-id") or ""
                    if not rid or rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                    rating_el = el.query_selector('[role="img"][aria-label*="star"]')
                    rating_str = rating_el.get_attribute("aria-label") if rating_el else "0"
                    try:
                        rating = int(float((rating_str or "0").split()[0]))
                    except (ValueError, IndexError):
                        rating = 0
                    text_el = el.query_selector('span[jsname]')
                    text = text_el.inner_text() if text_el else ""
                    date_el = el.query_selector('span[class*="date"]')
                    date_text = date_el.inner_text() if date_el else ""

                    raw_reviews.append(
                        RawReview(
                            review_id=rid,
                            rating=rating,
                            text=text,
                            timestamp=date_text,
                        )
                    )
                    added_this_round += 1
                    if len(raw_reviews) >= max_reviews:
                        break

                if added_this_round == 0:
                    stalls += 1
                else:
                    stalls = 0

                if scrollable:
                    page.evaluate("(el) => el.scrollBy(0, 1000)", scrollable)
                    page.wait_for_timeout(800)

            browser.close()

        if not raw_reviews:
            raise AdapterError(
                "Playwright scraper found no reviews — Google may have updated their "
                "UI or blocked the request. Set SERPAPI_API_KEY to use the SerpAPI adapter."
            )
        return business, raw_reviews


def _safe_text(page, selector: str) -> Optional[str]:
    try:
        el = page.query_selector(selector)
        return el.inner_text().strip() if el else None
    except Exception:
        return None


# ============================================================
# Adapter selection
# ============================================================


def _select_adapter(adapter_name: str = "auto") -> ReviewAdapter:
    """
    Select an adapter. Default ("auto"): scraping first, paid as opt-in only.

    "auto" → PlaywrightScraperAdapter (per user preference: try free first)
    "serpapi" → SerpAPIAdapter (requires SERPAPI_API_KEY)
    "scraper" or "playwright" → PlaywrightScraperAdapter
    """
    if adapter_name == "auto":
        adapter_name = os.environ.get("REVIEW_MINING_ADAPTER", "playwright")

    if adapter_name in ("scraper", "playwright"):
        return PlaywrightScraperAdapter()
    if adapter_name == "serpapi":
        return SerpAPIAdapter()
    raise ValueError(f"Unknown adapter: {adapter_name}")


# ============================================================
# LLM analysis layer
# ============================================================


_ANALYSIS_PROMPT = """You are a customer review analyst. Given raw customer reviews of a business,
extract structured insights for use in cold outreach personalization, product
discovery, competitive positioning, and CRM enrichment.

BUSINESS:
{business_json}

REVIEWS ({n_reviews} total):
{reviews_text}

Return STRICT JSON matching this schema. No markdown, no commentary, just JSON:

{{
  "pain_points": [
    {{
      "quote": "exact customer words (verbatim, no edits)",
      "theme": "short normalized label, lowercase, 2-4 words",
      "severity": 1-5,
      "frequency": <number of reviews mentioning this theme>,
      "recency": "30d" | "90d" | "180d" | "all-time",
      "customer_segment": "families" | "professionals" | "tourists" | "regulars" | null,
      "source_review_ids": ["id1", "id2"]
    }}
  ],
  "strengths": [
    {{
      "quote": "exact customer words",
      "theme": "short normalized label",
      "frequency": <number>,
      "source_review_ids": ["id1"]
    }}
  ],
  "customer_segments": {{
    "families": <count>,
    "professionals": <count>,
    "tourists": <count>,
    "regulars": <count>
  }},
  "response_pattern": {{
    "replies_to_reviews": true | false,
    "tone": "defensive" | "apologetic" | "professional" | "warm" | "absent",
    "avg_response_speed_days": <number or null>
  }},
  "competitive_signals": [
    "verbatim mentions of named competitors"
  ]
}}

RULES:
- Quotes MUST be verbatim from the reviews. Do not paraphrase, edit, or invent.
- Group similar complaints under one theme — do NOT list each review separately.
- Return at most 10 pain_points and 10 strengths, ranked by severity*frequency.
- Themes are short labels for filtering, not full sentences.
- If a field has no data, use empty list/dict, not omission.
"""


def analyze_reviews(
    raw_reviews: list[RawReview],
    business: BusinessIdentity,
    llm_provider: str = "auto",
) -> BusinessReviewProfile:
    """
    Use an LLM to structure raw reviews into a rich BusinessReviewProfile.

    llm_provider:
        "auto" → anthropic if ANTHROPIC_API_KEY, else openrouter, else error
        "anthropic" → uses anthropic SDK directly
        "openrouter" → uses OpenRouter via httpx
        "none" → skip LLM analysis, return profile with raw_reviews populated only
    """
    summary = _build_review_summary(raw_reviews)

    profile = BusinessReviewProfile(
        business=business,
        review_summary=summary,
        raw_reviews=raw_reviews,
    )

    if llm_provider == "none" or not raw_reviews:
        return profile

    structured = _llm_structure(raw_reviews, business, llm_provider)
    profile.pain_points = [PainPoint(**p) for p in structured.get("pain_points", [])]
    profile.strengths = [Strength(**s) for s in structured.get("strengths", [])]
    profile.customer_segments = structured.get("customer_segments", {})
    profile.response_pattern = structured.get("response_pattern", {})
    profile.competitive_signals = structured.get("competitive_signals", [])
    profile.sentiment_trend = _compute_sentiment_trend(raw_reviews)
    return profile


def _build_review_summary(raw_reviews: list[RawReview]) -> ReviewSummary:
    if not raw_reviews:
        return ReviewSummary(
            total_count=0,
            avg_rating=0.0,
            rating_distribution={},
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
    distribution: dict[int, int] = {}
    for r in raw_reviews:
        distribution[r.rating] = distribution.get(r.rating, 0) + 1
    avg = sum(r.rating for r in raw_reviews) / len(raw_reviews)
    return ReviewSummary(
        total_count=len(raw_reviews),
        avg_rating=round(avg, 2),
        rating_distribution=distribution,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def _compute_sentiment_trend(raw_reviews: list[RawReview]) -> TrendData:
    """
    Coarse trend from rating averages over time windows. Doesn't use timestamps
    parsed from "2 weeks ago" strings — just splits the (sorted-by-fetch-order)
    review list into rough buckets. SerpAPI returns sorted-by-newest, so the
    first 30 reviews ≈ most recent.
    """
    if not raw_reviews:
        return TrendData()

    def avg_rating(slice_: list[RawReview]) -> Optional[float]:
        if not slice_:
            return None
        return round(sum(r.rating for r in slice_) / len(slice_) / 5.0, 2)

    return TrendData(
        period_30d=avg_rating(raw_reviews[:10]),
        period_90d=avg_rating(raw_reviews[:30]),
        period_180d=avg_rating(raw_reviews[:60]),
        period_365d=avg_rating(raw_reviews),
    )


def _llm_structure(
    raw_reviews: list[RawReview],
    business: BusinessIdentity,
    llm_provider: str,
) -> dict:
    reviews_text = "\n\n".join(
        f"[id: {r.review_id}] [{r.rating}/5] [{r.timestamp}] {r.text}"
        for r in raw_reviews
        if r.text
    )
    prompt = _ANALYSIS_PROMPT.format(
        business_json=json.dumps(asdict(business), indent=2),
        n_reviews=len(raw_reviews),
        reviews_text=reviews_text,
    )

    if llm_provider == "auto":
        if os.environ.get("ANTHROPIC_API_KEY"):
            llm_provider = "anthropic"
        elif os.environ.get("OPENROUTER_API_KEY"):
            llm_provider = "openrouter"
        else:
            raise RuntimeError(
                "No LLM credentials found. Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY, "
                "or call with llm_provider='none' to skip analysis."
            )

    if llm_provider == "anthropic":
        return _call_anthropic(prompt)
    if llm_provider == "openrouter":
        return _call_openrouter(prompt)
    raise ValueError(f"Unknown llm_provider: {llm_provider}")


def _call_anthropic(prompt: str) -> dict:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic SDK not installed: pip install anthropic")
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    return _parse_json_from_text(text)


def _call_openrouter(prompt: str) -> dict:
    import urllib.request
    import urllib.error
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    payload = json.dumps(
        {
            "model": "anthropic/claude-haiku-4.5",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
        }
    ).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenRouter HTTP error: {e.code} {e.read().decode()}")
    text = data["choices"][0]["message"]["content"]
    return _parse_json_from_text(text)


def _parse_json_from_text(text: str) -> dict:
    """LLMs sometimes wrap JSON in markdown fences. Strip and parse."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM returned non-JSON: {e}\nFirst 500 chars: {text[:500]}")


# ============================================================
# Top-level convenience function
# ============================================================


def analyze_business_reviews(
    business_name: str,
    location: Optional[str] = None,
    max_reviews: int = 50,
    adapter: str = "auto",
    llm_provider: str = "auto",
) -> BusinessReviewProfile:
    """
    One-stop function: fetch reviews + structure them into a rich profile.

    For sales personalization (megaphone): pain points + strengths-as-pivots
    For product discovery (forge): pain points + customer_segments
    For positioning research (strategy-audit): pain_points + competitive_signals
    For CRM enrichment: full profile attached to company record
    """
    adapter_instance = _select_adapter(adapter)
    business, raw_reviews = adapter_instance.fetch(business_name, location, max_reviews)
    return analyze_reviews(raw_reviews, business, llm_provider)


# ============================================================
# CLI
# ============================================================


def _print_summary(profile: BusinessReviewProfile) -> None:
    b = profile.business
    s = profile.review_summary
    print(f"\n{'=' * 70}")
    print(f"  {b.name}")
    print(f"{'=' * 70}")
    if b.address:
        print(f"  Address:  {b.address}")
    if b.category:
        print(f"  Category: {b.category}  {b.price_tier or ''}")
    if b.website:
        print(f"  Web:      {b.website}")
    print(f"\n  Reviews:  {s.total_count} fetched, avg {s.avg_rating}/5")
    if s.rating_distribution:
        dist = " ".join(f"{k}★:{v}" for k, v in sorted(s.rating_distribution.items(), reverse=True))
        print(f"  Dist:     {dist}")

    if profile.sentiment_trend.period_30d is not None:
        t = profile.sentiment_trend
        print(f"  Trend:    30d={t.period_30d}  90d={t.period_90d}  180d={t.period_180d}  365d={t.period_365d}")

    if profile.pain_points:
        print(f"\n  PAIN POINTS ({len(profile.pain_points)}):")
        for p in profile.pain_points:
            print(f"    [{p.severity}/5 ×{p.frequency}] {p.theme}")
            print(f"      \"{p.quote[:120]}{'...' if len(p.quote) > 120 else ''}\"")

    if profile.strengths:
        print(f"\n  STRENGTHS ({len(profile.strengths)}):")
        for s_ in profile.strengths:
            print(f"    [×{s_.frequency}] {s_.theme}")
            print(f"      \"{s_.quote[:120]}{'...' if len(s_.quote) > 120 else ''}\"")

    if profile.customer_segments:
        segs = ", ".join(f"{k}:{v}" for k, v in profile.customer_segments.items() if v)
        print(f"\n  Segments: {segs}")

    if profile.response_pattern:
        rp = profile.response_pattern
        if rp.get("replies_to_reviews"):
            print(f"  Replies:  yes, tone={rp.get('tone')}, speed={rp.get('avg_response_speed_days')}d")
        else:
            print(f"  Replies:  none")

    if profile.competitive_signals:
        print(f"\n  COMPETITIVE MENTIONS:")
        for sig in profile.competitive_signals:
            print(f"    - {sig}")

    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mine customer reviews for market signal.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python -m datacore.lib.review_mining "Joe's Pizza" --location "NYC"
  python -m datacore.lib.review_mining "Acme Corp" --output json > acme.json
  python -m datacore.lib.review_mining "Local Cafe" --adapter serpapi --max-reviews 100
""",
    )
    parser.add_argument("business_name", help="Business name to look up")
    parser.add_argument("--location", help="City/region for disambiguation")
    parser.add_argument("--max-reviews", type=int, default=50)
    parser.add_argument(
        "--adapter",
        choices=["auto", "scraper", "playwright", "serpapi"],
        default="auto",
        help="Fetch adapter (default: scraper, set SERPAPI_API_KEY to use serpapi)",
    )
    parser.add_argument(
        "--llm",
        choices=["auto", "anthropic", "openrouter", "none"],
        default="auto",
        help="LLM provider for structuring (default: auto, none = raw reviews only)",
    )
    parser.add_argument(
        "--output",
        choices=["summary", "json", "yaml"],
        default="summary",
    )
    args = parser.parse_args()

    try:
        profile = analyze_business_reviews(
            business_name=args.business_name,
            location=args.location,
            max_reviews=args.max_reviews,
            adapter=args.adapter,
            llm_provider=args.llm,
        )
    except AdapterError as e:
        print(f"Adapter error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.output == "json":
        print(json.dumps(asdict(profile), indent=2, default=str))
    elif args.output == "yaml":
        try:
            import yaml
            print(yaml.safe_dump(asdict(profile), sort_keys=False, allow_unicode=True))
        except ImportError:
            print("yaml not available, falling back to json", file=sys.stderr)
            print(json.dumps(asdict(profile), indent=2, default=str))
    else:
        _print_summary(profile)

    return 0


if __name__ == "__main__":
    sys.exit(main())
