"""hermes.py -- Executor adapter for the `hermes` CLI (Hermes Agent).

Invokes `hermes chat -q <prompt>` via subprocess. The CLI's `chat -q` mode
prints plain text, with no machine-readable cost/usage envelope, so cost is
always estimated via `estimate_cost_cents` (chars/4 tokens at the
documented shadow-accounting rate in `base.py`) -- `self._cost_estimated`
is always set here, so the emitted spend ref always carries the `:est`
suffix for this adapter.

A missing `hermes` binary raises `RuntimeError` inside `_invoke` -- caught
by the base class's never-raise `run()` wrapper and surfaced as
`ExecResult.error`, never propagated to the caller.
"""

from __future__ import annotations

import shutil
import subprocess

from .base import Executor, estimate_cost_cents, register


@register
class HermesExecutor(Executor):
    name = "hermes"

    def _invoke(self, prompt: str, timeout_s: int) -> tuple[str, int]:
        binary = shutil.which("hermes")
        if binary is None:
            raise RuntimeError("'hermes' binary not found on PATH")

        result = subprocess.run(
            [binary, "chat", "-q", prompt],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(f"hermes exited {result.returncode}: {result.stderr.strip()}")

        text = result.stdout.strip()
        cost_cents = estimate_cost_cents(prompt, text)
        self._cost_estimated = True
        return text, cost_cents
