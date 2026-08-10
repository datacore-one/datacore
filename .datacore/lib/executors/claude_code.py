"""claude_code.py -- Executor adapter for the `claude` CLI (Claude Code).

Invokes `claude -p <prompt> --output-format json` via subprocess, using
whatever `claude` binary is on PATH (independent of `sys.executable` --
this is a separate CLI, not a Python module). Cost is parsed from the JSON
envelope's cost/usage fields when present (field names have varied across
`claude` CLI versions -- parsed defensively, checked in priority order);
when absent entirely, cost falls back to `estimate_cost_cents` (chars/4
tokens at the documented rate in `base.py`) and `self._cost_estimated` is
set so `run()` marks the emitted spend ref with the `:est` suffix.

A missing `claude` binary raises `RuntimeError` inside `_invoke` -- caught
by the base class's never-raise `run()` wrapper and surfaced as
`ExecResult.error`, never propagated to the caller.

In-band content errors: a real envelope (returncode 0, valid JSON) can
still report that the turn itself failed, via `is_error: true` and/or a
`subtype` other than `"success"` (both fields confirmed present together
on a real envelope via a live call). That is NOT a transport failure --
the CLI ran and tokens were consumed -- so it is signaled via
`self._in_band_error` (per `base.py`'s in-band-error contract) rather than
raised: `run()` surfaces it as `ExecResult.error` while STILL emitting the
spend event (ref marked `:err`). Envelopes with no `subtype` key at all
(older/simpler shapes) are treated as success, not as an error signal --
backward tolerance for envelope versions predating this field.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from .base import ESTIMATE_CENTS_PER_MILLION_TOKENS, Executor, estimate_cost_cents, register


def _extract_text(envelope: dict) -> str:
    for key in ("result", "text", "response"):
        value = envelope.get(key)
        if isinstance(value, str):
            return value
    return ""


def _extract_cost_cents(envelope: dict) -> int | None:
    """Best-effort extraction of a REAL cost figure from a `claude -p
    --output-format json` envelope. Checked in order, first match wins;
    returns None (never raises) when nothing recognizable is present, so
    the caller falls back to `estimate_cost_cents`.

    Two tiers: a direct dollar figure (`cost_usd` / `total_cost_usd`, at
    top level or nested under `usage`) is the most authoritative -- the CLI
    already computed real spend, so it's used as-is. Failing that, a
    `usage` object with real `input_tokens`/`output_tokens` counts is
    converted at the shared shadow-accounting rate -- the token COUNT is
    real even though the per-token price is our own placeholder, so this
    is still the "not estimated" branch (no `:est` suffix)."""
    for key in ("total_cost_usd", "cost_usd"):
        value = envelope.get(key)
        if isinstance(value, (int, float)):
            return round(value * 100)

    usage = envelope.get("usage")
    if isinstance(usage, dict):
        for key in ("total_cost_usd", "cost_usd"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                return round(value * 100)

        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if isinstance(input_tokens, (int, float)) and isinstance(output_tokens, (int, float)):
            # Speculative fallback -- NOT confirmed against a real envelope.
            # The live validation that confirmed `total_cost_usd` parsing
            # did not exercise this branch (that envelope already carried a
            # computed dollar total). Kept defensively in case some claude
            # CLI version reports token counts without a computed cost; if
            # real envelopes never populate `usage.input_tokens` alongside
            # a missing cost figure, this branch is simply dead and
            # harmless.
            total_tokens = input_tokens + output_tokens
            return round(total_tokens * ESTIMATE_CENTS_PER_MILLION_TOKENS / 1_000_000)

    return None


@register
class ClaudeCodeExecutor(Executor):
    name = "claude-code"

    def _invoke(self, prompt: str, timeout_s: int) -> tuple[str, int]:
        binary = shutil.which("claude")
        if binary is None:
            raise RuntimeError("'claude' binary not found on PATH")

        result = subprocess.run(
            [binary, "-p", prompt, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )

        envelope: dict | None = None
        try:
            parsed = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            envelope = parsed

        if envelope is None and result.returncode != 0:
            raise RuntimeError(f"claude exited {result.returncode}: {result.stderr.strip()}")

        if envelope is not None:
            text = _extract_text(envelope)
            cost_cents = _extract_cost_cents(envelope)
            self._check_in_band_error(envelope)
        else:
            text = result.stdout
            cost_cents = None

        if cost_cents is None:
            cost_cents = estimate_cost_cents(prompt, text)
            self._cost_estimated = True

        return text, cost_cents

    def _check_in_band_error(self, envelope: dict) -> None:
        """Flag an in-band content error via `self._in_band_error` (never
        raised -- transport succeeded and tokens were consumed, so `run()`
        must still emit spend; see `base.py`'s in-band-error contract).

        Real `claude -p --output-format json` envelopes carry `is_error`
        and `subtype` together (confirmed live). Treated backward-
        tolerantly when either is absent: a MISSING `subtype` key is NOT
        an error signal on its own -- older/simpler envelopes lack the
        field entirely and must still read as success.
        """
        is_error_flag = envelope.get("is_error") is True
        subtype_present = "subtype" in envelope
        subtype = envelope.get("subtype")
        if is_error_flag or (subtype_present and subtype != "success"):
            self._in_band_error = f"claude reported error: {subtype if subtype_present else 'unknown'}"
