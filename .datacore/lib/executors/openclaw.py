"""openclaw.py -- Executor adapter for OpenClaw (the runtime Data runs on).

The registry had adapters for `claude-code`, `hermes` and `api`, but not for
OpenClaw, so the one machine in the fleet running it -- plur-claw, actor
`data`, model codex/gpt-5.5 -- had no way to be addressed through the same
interface as everyone else. That gap is why a dispatcher written against
`claude -p` reported Data as having no agent runtime, when in fact it has a
perfectly good one that is simply not Claude.

`openclaw agent` runs a single turn via the already-running Gateway. Two
details are load-bearing:

  --agent main is MANDATORY. Without a target session openclaw exits with
  "No target session selected. Use --agent <id>, --session-key <key>, ..."
  and does no work at all. `main` is the default agent's id on plur-claw
  (identity "Data", workspace ~/.openclaw/workspace); override with
  $OPENCLAW_AGENT where an installation names it differently.

  The CLI decorates stdout with box-drawing rules and doctor warnings. Those
  lines are stripped here rather than by every caller, because a caller that
  forgets leaves the banner in the model's answer -- and a downstream schema
  parse then fails on text the agent never wrote.

Like `hermes`, this CLI emits no machine-readable usage envelope, so cost is
always estimated and the spend ref always carries the `:est` suffix.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from .base import Executor, estimate_cost_cents, register

# Box-drawing and status glyphs the CLI prints around real output.
_CHROME = ("│", "◇", "├", "╮", "╯", "─", "┌", "└", "┐", "┘")


def _configured_model() -> str | None:
    """Primary model from openclaw.json, labelled as configured rather than observed."""
    import json
    from pathlib import Path as _P
    try:
        cfg = json.loads((_P.home() / ".openclaw" / "openclaw.json").read_text())
        primary = cfg["agents"]["defaults"]["model"]["primary"]
        return f"{primary} (configured)" if primary else None
    except Exception:  # noqa: BLE001 -- never fail a run over provenance metadata
        return None


@register
class OpenClawExecutor(Executor):
    """One agent turn through the OpenClaw Gateway."""

    name = "openclaw"

    def _invoke(self, prompt: str, timeout_s: int) -> tuple[str, int]:
        binary = shutil.which("openclaw")
        if binary is None:
            raise RuntimeError("'openclaw' binary not found on PATH")

        agent = os.environ.get("OPENCLAW_AGENT", "main")
        # OpenClaw prints only the agent's reply -- no envelope, so there is no
        # served-model to read. The configured primary is the best available
        # answer and is marked as such IN THE VALUE, because a fallback would
        # make it silently wrong and an unlabelled guess in an audit trail is
        # worse than an absent field.
        self._model = _configured_model()
        result = subprocess.run(
            [binary, "agent", "--agent", agent, "--message", prompt],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"openclaw exited {result.returncode}: {result.stderr.strip()[:300]}")

        text = "\n".join(
            line for line in (result.stdout or "").splitlines()
            if not any(g in line for g in _CHROME)
        ).strip()

        # A turn that produced only chrome produced no answer. Say so, rather
        # than returning "" for the base class to treat as a successful empty
        # response -- that is how a failure becomes a green result.
        if not text:
            raise RuntimeError("openclaw produced no output beyond CLI chrome")

        self._cost_estimated = True
        return text, estimate_cost_cents(prompt, text)
