"""api.py -- Executor adapter calling the Anthropic API directly.

The `anthropic` package is imported inside `_invoke`, never at module load
time -- importing `executors` (and therefore `get_executor()` for ANY
adapter, this one included) never requires the SDK to be installed. A
missing SDK, missing credentials, or any exception the SDK raises surfaces
as `ExecResult.error` via the base class's never-raise `run()` wrapper;
`_invoke` itself is free to raise.

Cost: uses the response's real `usage.input_tokens` / `usage.output_tokens`
counts when present, converted at the shared shadow-accounting rate (see
`base.ESTIMATE_CENTS_PER_MILLION_TOKENS`) -- NOT marked `:est` since the
token counts themselves are real, not guessed (only the flat per-token
rate is a shared placeholder, same as every other adapter's real-usage
path). Falls back to the full chars/4 `estimate_cost_cents` (and the
`:est` ref marker) only when the SDK response carries no usage at all.

Model: defaults to `claude-opus-5` per current Anthropic guidance (the
recommended default absent an explicit user choice); override via
`DATACORE_API_MODEL`.

Cost implication of that default: `claude-opus-5` is Opus-tier pricing --
the most capable currently-recommended model, not the cheapest -- so every
`api` executor call made with the default model incurs real Opus-tier API
spend regardless of what the shadow ledger records for it. The
shadow-accounting `cost_cents` this adapter emits (real usage tokens
priced at the shared flat `ESTIMATE_CENTS_PER_MILLION_TOKENS` rate, or the
chars/4 estimate) does not track actual per-model Anthropic pricing either
way -- it is not a reliable proxy for what this adapter will really cost
to run. Set `DATACORE_API_MODEL` to a cheaper model (e.g. `claude-haiku-4-5`)
if this adapter will be invoked routinely and real spend matters.
"""

from __future__ import annotations

import os

from .base import ESTIMATE_CENTS_PER_MILLION_TOKENS, Executor, estimate_cost_cents, register

DEFAULT_MODEL = "claude-opus-5"


@register
class ApiExecutor(Executor):
    name = "api"

    def _invoke(self, prompt: str, timeout_s: int) -> tuple[str, int]:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic SDK not installed (pip install anthropic)") from exc

        client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY / auth profile from env
        model = os.environ.get("DATACORE_API_MODEL", DEFAULT_MODEL)

        response = client.with_options(timeout=timeout_s).messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        text = "".join(
            block.text for block in getattr(response, "content", []) if getattr(block, "type", None) == "text"
        )

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None

        if isinstance(input_tokens, (int, float)) and isinstance(output_tokens, (int, float)):
            total_tokens = input_tokens + output_tokens
            cost_cents = round(total_tokens * ESTIMATE_CENTS_PER_MILLION_TOKENS / 1_000_000)
        else:
            cost_cents = estimate_cost_cents(prompt, text)
            self._cost_estimated = True

        return text, cost_cents
