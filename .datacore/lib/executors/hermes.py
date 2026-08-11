"""hermes.py -- Executor adapter for the `hermes` CLI (Hermes Agent).

Invokes the Hermes agent via a direct AIAgent.run_conversation() call,
bypassing the CLI's oneshot/chat -q paths which hang in SSH-terminal
environments (the stdout/stderr redirect to devnull breaks the SSH
file-sync subprocess).

Uses the hermes_oneshot.py wrapper script which calls AIAgent directly
with HERMES_YOLO_MODE=1 and os._exit(0) for clean termination.

Cost is always estimated via `estimate_cost_cents` (chars/4 tokens at
the documented shadow-accounting rate in `base.py`) -- `self._cost_estimated`
is always set here, so the emitted spend ref always carries the `:est`
suffix for this adapter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .base import Executor, estimate_cost_cents, register


@register
class HermesExecutor(Executor):
    name = "hermes"

    def _invoke(self, prompt: str, timeout_s: int) -> tuple[str, int]:
        # Try the wrapper script first (works in SSH-terminal environments)
        wrapper = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "executors", "hermes_oneshot.py"
        )
        python = os.path.join(
            os.path.expanduser("~/.hermes/hermes-agent/venv/bin"),
            "python3"
        )

        if os.path.isfile(wrapper) and os.path.isfile(python):
            result = subprocess.run(
                [python, wrapper, prompt],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        else:
            # Fallback: use the hermes CLI directly
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
