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
            # SAY WHAT OPENCLAW SAID. This reported the exit code alone, and an
            # exit code is not a diagnosis: three delegated items failed on
            # plur-claw on 2026-09-18 with "openclaw execution failed (exit 2)"
            # and finding out why meant running the binary by hand. The
            # envelope's own `error`/`status`, and the last line of stderr,
            # are what an operator needs and they were being discarded.
            detail = envelope.get("error") or envelope.get("status")
            tail = (result.stderr or "").strip().splitlines()
            self._in_band_error = (
                f"openclaw execution failed (exit {result.returncode})"
                + (f": {str(detail)[:200]}" if detail else "")
                + (f" [stderr: {tail[-1][:160]}]" if tail else ""))
        elif not text.strip():
            self._in_band_error = "openclaw produced no final output"
        cost = envelope.get("costUsd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
            cents = round(cost * 100)
        else:
            cents = estimate_cost_cents(prompt, text)
            self._cost_estimated = True
        return text, cents


@register
class OpenClawGatewayExecutor(OpenClawExecutor):
    """One OpenClaw turn through the Gateway, in a fresh session per run.

    `agent exec` is an embedded run that reads provider credentials from the
    environment, so on plur-claw it used a pay-per-use API key that had run dry
    (2026-09-23) while the Gateway's own agent -- the one Telegram talks to --
    runs on the Codex harness with the owner's stored subscription login. This
    route uses that agent. Its workspace is the Gateway's, not the caller's, so
    the prompt is told to work in the dispatched directory; a turn that writes
    elsewhere leaves no evidence there and fails the run, it cannot pass silently.
    """
    name = "openclaw-gateway"

    def _invoke(self, prompt: str, timeout_s: int) -> tuple[str, int]:
        import uuid
        binary = shutil.which("openclaw")
        if binary is None:
            raise RuntimeError("'openclaw' binary not found on PATH")
        workspace = str(Path(self._cwd or os.getcwd()).resolve())
        message = (f"Work only in `{workspace}`: `cd` there before anything else; every relative path "
                   f"below is relative to it.\n\n{prompt}")
        import tempfile
        # `openclaw agent` reads --message-file from a path only (stdin is an
        # `agent exec` feature): a private file, removed after the turn.
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", prefix="dispatch-") as fh:
            fh.write(message)
            fh.flush()
            command = [binary, "agent", "--agent", "main", "--session-key",
                       f"agent:main:dispatch-{uuid.uuid4().hex[:12]}",
                       "--message-file", fh.name, "--json", "--timeout", str(timeout_s)]
            result = run_process(command, capture_output=True, text=True, timeout=timeout_s + 30,
                                 check=False, cwd=workspace, env=self._execution_env())
        try:
            envelope = json.loads(result.stdout)
        except (ValueError, TypeError):
            tail = (result.stderr or "").strip().splitlines()
            raise RuntimeError(f"openclaw gateway returned no result (exit {result.returncode})"
                               + (f" [stderr: {tail[-1][:160]}]" if tail else "")) from None
        body = envelope.get("result") if isinstance(envelope, dict) else None
        payloads = (body or {}).get("payloads") or []
        text = "\n".join(str(p.get("text") or "") for p in payloads if isinstance(p, dict)).strip()
        meta = ((body or {}).get("meta") or {}).get("agentMeta") or {}
        if isinstance(meta.get("model"), str):
            self._model = meta["model"]
        if result.returncode != 0 or envelope.get("status") != "ok":
            detail = envelope.get("error") or envelope.get("summary") or envelope.get("status")
            self._in_band_error = (f"openclaw gateway turn failed (exit {result.returncode})"
                                   + (f": {str(detail)[:200]}" if detail else ""))
        elif not text:
            self._in_band_error = "openclaw gateway turn produced no reply"
        self._cost_estimated = True  # subscription-billed: no per-run price exists
        return text, estimate_cost_cents(prompt, text)
