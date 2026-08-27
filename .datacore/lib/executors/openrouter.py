"""openrouter.py -- Executor adapter for the OpenRouter API.

OpenRouter is a unified gateway to many model providers. This adapter
calls the OpenRouter chat completions endpoint (OpenAI-compatible) using
only the Python standard library -- no SDK required. Missing or invalid
API keys, network failures, and HTTP errors all surface as ExecResult.error
via the base class's never-raise run() wrapper; _invoke itself is free to
raise.

Cost: uses the response's real usage.prompt_tokens / usage.completion_tokens
when the API returns them. OpenRouter echoes back the model string it
actually served, so ExecResult.model is always the served model, not the
configured one -- this matters because OpenRouter can reroute to fallback
providers.

Configuration:
  OPENROUTER_API_KEY       -- required; the Bearer token
  OPENROUTER_MODEL         -- optional; defaults to DEFAULT_MODEL below
  OPENROUTER_REFERER       -- optional; HTTP-Referer header value, forwarded
                             to OpenRouter for attribution (shown in their
                             dashboard). Defaults to DEFAULT_REFERER.
  OPENROUTER_MAX_TOKENS    -- optional int; defaults to DEFAULT_MAX_TOKENS
  OPENROUTER_TEMPERATURE   -- optional float; defaults to DEFAULT_TEMPERATURE

Shadow accounting: uses real token counts from the usage envelope when
present, at the shared ESTIMATE_CENTS_PER_MILLION_TOKENS rate (not marked
:est). Falls back to estimate_cost_cents (chars/4) and marks :est only
when the API returns no usage at all.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import ESTIMATE_CENTS_PER_MILLION_TOKENS, Executor, estimate_cost_cents, register

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODEL = "anthropic/claude-sonnet-4"
DEFAULT_REFERER = "https://fairdatasociety.org"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7


@register
class OpenRouterExecutor(Executor):
    """Single-turn chat completion via the OpenRouter API."""

    name = "openrouter"

    def _invoke(self, prompt: str, timeout_s: int) -> tuple[str, int]:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set; "
                "export it before using the openrouter executor"
            )

        model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
        referer = os.environ.get("OPENROUTER_REFERER", DEFAULT_REFERER)

        try:
            max_tokens = int(os.environ.get("OPENROUTER_MAX_TOKENS", DEFAULT_MAX_TOKENS))
        except (ValueError, TypeError):
            max_tokens = DEFAULT_MAX_TOKENS

        try:
            temperature = float(os.environ.get("OPENROUTER_TEMPERATURE", DEFAULT_TEMPERATURE))
        except (ValueError, TypeError):
            temperature = DEFAULT_TEMPERATURE

        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode("utf-8")

        req = urllib.request.Request(
            OPENROUTER_API_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": referer,
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8")[:200]
            except Exception:
                err_body = str(exc)
            raise RuntimeError(f"OpenRouter HTTP {exc.code}: {err_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenRouter network error: {exc.reason}") from exc

        # Extract the served model (may differ from configured if OpenRouter
        # applied a fallback). Record what actually ran, not what was asked for.
        served_model = data.get("model") or model
        self._model = served_model

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenRouter returned no choices in response")

        text = (choices[0].get("message") or {}).get("content") or ""

        # Real usage from the API envelope -- preferred over estimation.
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")

        if isinstance(prompt_tokens, (int, float)) and isinstance(completion_tokens, (int, float)):
            total_tokens = prompt_tokens + completion_tokens
            cost_cents = round(total_tokens * ESTIMATE_CENTS_PER_MILLION_TOKENS / 1_000_000)
            # NOT marking _cost_estimated -- real token counts were present, only
            # the flat per-token rate is a shared shadow-accounting placeholder.
        else:
            cost_cents = estimate_cost_cents(prompt, text)
            self._cost_estimated = True

        return text, cost_cents
