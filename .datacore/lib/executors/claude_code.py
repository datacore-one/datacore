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
import os
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

        # THREE THINGS THE REGISTRY REFACTOR DROPPED, each restored here with
        # the reason it exists, because losing them was silent and cost days.
        #
        # --permission-mode acceptEdits: without a permission mode the agent is
        #   READ-ONLY. It declines every Write, then reports fluently and exits
        #   0, so the caller sees success and the check fails for a reason that
        #   looks like refusal. Verified on nightshift 2026-08-14: identical
        #   prompt, `acceptEdits` writes the file, no flag does not.
        #   NOT --dangerously-skip-permissions: that is refused outright under
        #   root, which is exactly winston's situation, and it is a blanket
        #   grant where this is the narrow one.
        #
        # cwd: the agent must work in the space it was dispatched for.
        #   `acceptEdits` is scoped to the working directory, so a wrong cwd
        #   silently makes every write a denied out-of-scope write.
        #
        # env with DATACORE_HEADLESS: the PreToolUse PLUR guard demands
        #   plur_session_start, which is unsatisfiable where the MCP server is
        #   not connected -- it then refuses every tool call and exits 0.
        #   Passed explicitly rather than relying on the parent process having
        #   mutated os.environ, which is how it came to be a coincidence.
        env = {**os.environ, "DATACORE_HEADLESS": "1"}
        result = subprocess.run(
            [binary, "-p", prompt, "--permission-mode", "acceptEdits",
             "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            stdin=subprocess.DEVNULL,
            cwd=str(self._cwd) if self._cwd else None,
            env=env,
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
            # The model is the KEY of `modelUsage`, e.g.
            # {"claude-opus-4-8[1m]": {...}} -- confirmed against a live
            # envelope. More than one key means a fallback fired mid-turn, so
            # all of them are recorded: naming only the first would hide
            # exactly the event worth knowing about.
            usage = envelope.get("modelUsage")
            if isinstance(usage, dict) and usage:
                self._model = ",".join(sorted(usage))
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
        if not (is_error_flag or (subtype_present and subtype != "success")):
            return

        # SAY WHAT WENT WRONG, NOT WHICH FLAG NOTICED.
        #
        # This used to report `claude reported error: {subtype}`. When
        # `is_error: true` arrives alongside `subtype: "success"` -- which real
        # envelopes do -- that renders as "claude reported error: success": a
        # contradiction that also THREW AWAY the diagnosis. Every failing item
        # in a 15-item suite carried that string, and it cost three
        # investigation rounds and a wrong root cause (a supposed OAuth outage
        # that did not exist) before anyone read the envelope directly.
        #
        # The envelope already carries the answer in `api_error_status`,
        # `terminal_reason`, `permission_denials` and `result`. Preferred in
        # that order, most specific first, with subtype kept only as a last
        # resort -- and never alone when something better exists.
        # Subtype is INCLUDED whenever it is informative, not used as a
        # fallback. A first attempt at this fix preferred the `result` body and
        # dropped the subtype, which lost `error_max_turns` -- trading one lost
        # diagnosis for another. It is skipped only when it says "success",
        # which is the single case where printing it produced nonsense.
        parts: list[str] = []
        if subtype_present and subtype != "success":
            parts.append(f"subtype={subtype}")
        status = envelope.get("api_error_status")
        if status:
            parts.append(f"api_error_status={status}")
        terminal = envelope.get("terminal_reason")
        if terminal and terminal != "success":
            parts.append(f"terminal_reason={terminal}")
        denials = envelope.get("permission_denials")
        if denials:
            parts.append(f"permission_denials={len(denials)}")
        body = envelope.get("result")
        if isinstance(body, str) and body.strip():
            parts.append(body.strip()[:200])
        if not parts:
            parts.append("is_error set, envelope carried no detail")

        self._in_band_error = "claude reported error: " + "; ".join(parts)
