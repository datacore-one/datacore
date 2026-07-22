#!/usr/bin/env python3
"""Ask Perplexity and/or Gemini a research question from the CLI.

Fills the gap when the perplexity/gemini MCP servers aren't connected to the
harness: talks to the vendor APIs directly using the keys in
.datacore/env/.env. Perplexity returns live web results with citations; Gemini
returns synthesis (optionally with Google Search grounding).

Usage:
    python3 .datacore/lib/ask_models.py --model perplexity "your question"
    python3 .datacore/lib/ask_models.py --model gemini --grounded "your question"
    python3 .datacore/lib/ask_models.py --model both --json "your question"
    cat prompt.txt | python3 .datacore/lib/ask_models.py --model perplexity -

Models:
    perplexity  -> sonar-pro (override with --pplx-model, e.g. sonar-deep-research)
    gemini      -> gemini-3.1-pro-preview (override with --gemini-model)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / "env" / ".env"

PPLX_URL = "https://api.perplexity.ai/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DEFAULT_PPLX_MODEL = "sonar-pro"
DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"


def load_env() -> None:
    """Load .datacore/env/.env into os.environ without clobbering real env vars."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if value and key not in os.environ:
            os.environ[key] = value


def _post(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:600]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def ask_perplexity(prompt: str, model: str, timeout: int) -> dict:
    key = os.environ.get("PERPLEXITY_API_KEY")
    if not key:
        raise RuntimeError("PERPLEXITY_API_KEY not set")
    data = _post(
        PPLX_URL,
        {"model": model, "messages": [{"role": "user", "content": prompt}]},
        {"Authorization": f"Bearer {key}"},
        timeout,
    )
    return {
        "source": f"perplexity/{model}",
        "text": data["choices"][0]["message"]["content"],
        # Perplexity has moved citation shape around; accept either.
        "citations": data.get("citations")
        or [s.get("url") for s in data.get("search_results", []) if s.get("url")],
    }


def ask_gemini(prompt: str, model: str, grounded: bool, timeout: int) -> dict:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    payload: dict = {"contents": [{"parts": [{"text": prompt}]}]}
    if grounded:
        payload["tools"] = [{"google_search": {}}]
    data = _post(
        GEMINI_URL.format(model=model),
        payload,
        {"x-goog-api-key": key},
        timeout,
    )
    candidate = data["candidates"][0]
    text = "".join(
        part["text"] for part in candidate["content"]["parts"] if "text" in part
    )
    chunks = candidate.get("groundingMetadata", {}).get("groundingChunks", [])
    return {
        "source": f"gemini/{model}",
        "text": text,
        "citations": [
            c["web"]["uri"] for c in chunks if c.get("web", {}).get("uri")
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="Question to ask, or '-' to read stdin")
    parser.add_argument(
        "--model", choices=["perplexity", "gemini", "both"], default="both"
    )
    parser.add_argument("--pplx-model", default=DEFAULT_PPLX_MODEL)
    parser.add_argument("--gemini-model", default=DEFAULT_GEMINI_MODEL)
    parser.add_argument(
        "--grounded",
        action="store_true",
        help="Enable Google Search grounding for Gemini",
    )
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--json", action="store_true", help="Emit raw JSON")
    args = parser.parse_args()

    load_env()
    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt

    jobs = []
    if args.model in ("perplexity", "both"):
        jobs.append(lambda: ask_perplexity(prompt, args.pplx_model, args.timeout))
    if args.model in ("gemini", "both"):
        jobs.append(
            lambda: ask_gemini(prompt, args.gemini_model, args.grounded, args.timeout)
        )

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(job) for job in jobs]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 — surface, don't kill the sibling
                results.append({"source": "error", "text": str(exc), "citations": []})

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    for result in results:
        print(f"\n{'=' * 70}\n## {result['source']}\n{'=' * 70}\n")
        print(result["text"])
        if result["citations"]:
            print("\n### Citations")
            for i, url in enumerate(result["citations"], 1):
                print(f"[{i}] {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
