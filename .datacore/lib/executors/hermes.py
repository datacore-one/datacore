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


def _hermes_env(cwd=None) -> dict:
    """PATH that includes the Hermes install's own binaries.

    The wrapper shells out to the PLUR CLI, and Hermes ships its own node
    runtime -- `plur` lives at `~/.hermes/node/bin/plur`, `hermes` at
    `~/.local/bin/hermes`. Neither is on the PATH of a non-interactive shell
    or a systemd unit, only of the login shell the box was set up from.

    So the wrapper printed `PLUR CLI not found. Install: npm install -g
    @plur-ai/cli` to stderr, wrote nothing to stdout, and EXITED 0. The
    adapter saw returncode 0 with empty output and reported "hermes returned
    no output" -- a PATH problem wearing the costume of a missing package.
    Anyone acting on that message would have installed a CLI that was already
    there.

    Set here rather than in the systemd unit because this adapter already
    hardcodes the same install's venv interpreter: one place knows the Hermes
    layout, and the fix then works from a timer, an SSH command, or a test
    equally.
    """
    home = os.path.expanduser("~")
    extra = [os.path.join(home, ".hermes", "node", "bin"),
             os.path.join(home, ".local", "bin"),
             os.path.join(home, ".hermes", "hermes-agent")]
    env = dict(os.environ)
    present = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join(
        [p for p in extra if p not in present] + present)

    # TERMINAL_CWD IS THE ONLY THING THAT MOVES THE AGENT.
    #
    # Passing `cwd=` to the subprocess does nothing here, and that is not a bug
    # in the wrapper. Hermes resolves the agent's working directory from its
    # TERMINAL BACKEND, never from the host process: `terminal.backend: ssh` in
    # ~/.hermes/config.yaml means `default_cwd = "~"`, so a relative proof path
    # landed in $HOME/Data instead of the dispatched space, one directory above
    # where the check looks. The wrapper passes platform="cli" -- for which the
    # docs promise os.getcwd() -- but the backend setting overrides the
    # platform, so CLI semantics never applied.
    #
    # `TERMINAL_CWD` is read before backend dispatch (terminal_tool.py) and so
    # works for every backend. Hermes' own gateway sets exactly this variable
    # for its child tools; we are a child-tool spawner doing the same thing.
    #
    # Set PER DISPATCH rather than pinned in config.yaml: the canonical
    # `terminal.cwd` key would hard-code one space, and a dispatcher that later
    # targets a second space would silently write into the first -- today's bug
    # with a different cause.
    if cwd:
        env["TERMINAL_CWD"] = str(cwd)
    return env


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
            # `cwd` is set for the wrapper process itself, but it is NOT what
            # places the agent -- see _hermes_env(). Hermes takes the agent's
            # working directory from its terminal backend, so TERMINAL_CWD in
            # the env is the load-bearing part and this is merely tidy.
            result = subprocess.run(
                [python, wrapper, prompt],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                cwd=str(self._cwd) if self._cwd else None,
                env=_hermes_env(self._cwd),
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
                cwd=str(self._cwd) if self._cwd else None,
            )

        if result.returncode != 0:
            raise RuntimeError(f"hermes exited {result.returncode}: {result.stderr.strip()}")

        # Hermes reports no model: the wrapper prints the agent's reply and
        # nothing else. Left as None deliberately -- see ExecResult.model. A
        # value invented from config here would be indistinguishable in the
        # ledger from one the transport actually confirmed.
        text = result.stdout.strip()
        cost_cents = estimate_cost_cents(prompt, text)
        self._cost_estimated = True
        return text, cost_cents
