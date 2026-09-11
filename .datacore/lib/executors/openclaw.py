"""One isolated OpenClaw headless turn scoped to the dispatched workspace.

Requires a runtime with `agent exec`. The gateway's persistent main session
cannot implement the executor contract: its workspace is configured on the
server and does not follow the client's process cwd. No gateway fallback is
used. The runtime's own sandbox and tool policies remain deployment controls.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from process_run import run as run_process

from .base import Executor, estimate_cost_cents, register


@register
class OpenClawExecutor(Executor):
    name = "openclaw"

    def _invoke(self, prompt: str, timeout_s: int) -> tuple[str, int]:
        binary = shutil.which("openclaw")
        if binary is None:
            raise RuntimeError("'openclaw' binary not found on PATH")
        workspace = str(Path(self._cwd or os.getcwd()).resolve())
        command = [binary, "agent", "exec", "--message-file", "-", "--cwd", workspace,
                   "--timeout", str(timeout_s), "--json"]
        config = os.environ.get("DATACORE_OPENCLAW_CONFIG")
        if config:
            command.extend(["--config", config])
        result = run_process(command, input=prompt, capture_output=True, text=True,
                                timeout=timeout_s + 15, check=False, cwd=workspace,
                                env=self._execution_env())
        try:
            envelope = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise RuntimeError(f"openclaw returned an invalid result (exit {result.returncode}); agent exec is required") from None
        if not isinstance(envelope, dict) or not isinstance(envelope.get("final"), str):
            raise RuntimeError("openclaw returned an invalid result envelope")
        if isinstance(envelope.get("model"), str):
            self._model = envelope["model"]
        text = envelope["final"]
        if result.returncode != 0 or envelope.get("ok") is not True or envelope.get("status") != "ok":
            self._in_band_error = f"openclaw execution failed (exit {result.returncode})"
        elif not text.strip():
            self._in_band_error = "openclaw produced no final output"
        cost = envelope.get("costUsd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
            cents = round(cost * 100)
        else:
            cents = estimate_cost_cents(prompt, text)
            self._cost_estimated = True
        return text, cents
