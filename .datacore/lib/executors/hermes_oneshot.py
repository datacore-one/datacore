#!/usr/bin/env python3
"""Working non-interactive Hermes invocation for the ledger claim worker.

`hermes -z` and `hermes chat -q` both hang in this environment because the
oneshot module redirects stdout/stderr to devnull, which breaks the SSH
terminal backend's file sync subprocess.

This wrapper calls AIAgent.run_conversation() directly, avoiding the
redirect. It installs the Datacore policy gate, refuses auto-approval, and uses os._exit()
to skip cleanup that can also hang.

Usage:
    python3 hermes_oneshot.py 'your prompt here'
    python3 hermes_oneshot.py 'your prompt here' --toolsets file,terminal

Exit code 0 = success, response on stdout.
Exit code 1 = agent failure, incomplete response or empty response. Useful
partial text remains on stdout; its presence does not indicate success.
"""
import sys, os, time


def install_policy_guard():
    """Install at the runtime's common dispatch hook before any conversation."""
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hermes_cli import plugins
    from hermes_plugin import pre_tool_call
    original = plugins.get_pre_tool_call_block_message
    if not callable(original):
        raise RuntimeError("Hermes runtime does not expose the required tool policy hook")

    def guarded(tool_name, args, *positional, **kwargs):
        try:
            decision = pre_tool_call(tool_name=tool_name, args=args)
            if decision and decision.get("action") == "block":
                return decision["message"]
            return original(tool_name, args, *positional, **kwargs)
        except Exception:
            return "Datacore runtime policy unavailable; tool execution refused"

    plugins.get_pre_tool_call_block_message = guarded


def log_offset(path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def provider_error_since(path, offset: int):
    """The last provider error Hermes logged after `offset`, or None.

    Hermes swallows a provider failure (an empty OpenRouter account answers
    HTTP 402) and hands back a partial result; only its errors.log keeps the
    reason. Without it the claim loop saw "incomplete provider result", could
    not tell an empty account from a hard task, and retried until the account
    was empty (2026-10-26)."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            new = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    hits = [ln[ln.index("Error code"):] for ln in new.splitlines() if "Error code" in ln]
    return hits[-1][:300] if hits else None


def _get_json(url: str, key: str) -> dict:
    import json
    import urllib.request
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def key_usage_usd(base_url, api_key):
    """The OpenRouter key's lifetime usage in USD, or None (not OpenRouter, no
    key, or the read failed). Never raises: cost reporting must not fail a run."""
    if not api_key or "openrouter.ai" not in str(base_url or ""):
        return None
    try:
        usage = (_get_json(str(base_url).rstrip("/") + "/key", api_key).get("data") or {}).get("usage")
        return float(usage) if usage is not None else None
    except Exception:  # noqa: BLE001
        return None


def usage_delta_usd(read, run):
    """Provider-charged cost of `run()`: the key's usage after minus before.

    Hermes reports no cost, and the character-count estimate recorded 16 cents
    for about 21.9 USD of runs on 2026-10-26, so the spend guard never tripped."""
    before = read()
    run()
    after = read() if before is not None else None
    if before is None or after is None:
        return None
    return max(0.0, after - before)


def main():
    os.environ["HERMES_YOLO_MODE"] = "0"
    os.environ["HERMES_ACCEPT_HOOKS"] = "0"
    os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes"))
    if len(sys.argv) < 2:
        print("Usage: hermes_oneshot.py <prompt> [--toolsets t1,t2,...]", file=sys.stderr)
        sys.exit(2)

    prompt = sys.stdin.read() if sys.argv[1] == "--stdin" else sys.argv[1]
    toolsets = ["file", "terminal"]  # sensible defaults for ledger tasks

    if "--toolsets" in sys.argv:
        idx = sys.argv.index("--toolsets")
        if idx + 1 < len(sys.argv):
            toolsets = [t.strip() for t in sys.argv[idx + 1].split(",") if t.strip()]

    from run_agent import AIAgent
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_provider

    cfg = load_config()
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, str):
        model = model_cfg
    else:
        model = model_cfg.get("default") or model_cfg.get("model") or ""

    runtime = resolve_runtime_provider(target_model=model)

    install_policy_guard()
    errors_log = os.path.join(os.environ["HERMES_HOME"], "logs", "errors.log")
    errors_from = log_offset(errors_log)
    agent = AIAgent(
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        api_mode=runtime.get("api_mode"),
        model=model,
        enabled_toolsets=toolsets,
        quiet_mode=True,
        platform="cli",
    )

    out = {}
    cost = usage_delta_usd(
        lambda: key_usage_usd(runtime.get("base_url"), runtime.get("api_key")),
        lambda: out.update(result=agent.run_conversation(prompt)))
    result = out.get("result")
    if cost is not None:
        print(f"hermes_oneshot: cost_usd={cost:.6f}", file=sys.stderr)
    response = result.get("final_response") if isinstance(result, dict) else None
    complete = (isinstance(result, dict)
                and result.get("completed", True) is True
                and not any(result.get(flag) for flag in ("failed", "partial", "error")))

    # FLUSH BEFORE os._exit -- it does not.
    #
    # os._exit() terminates immediately: no atexit handlers, no buffer flush.
    # That is exactly why it is used here (the agent leaves threads that make a
    # clean interpreter shutdown hang), but it means anything still sitting in
    # Python's stdout buffer is discarded.
    #
    # Under a terminal stdout is line-buffered, so `print` flushes on the
    # newline and this works. Under `subprocess.run(capture_output=True)`
    # stdout is a PIPE and therefore block-buffered: the response goes into the
    # buffer, os._exit throws it away, and the process reports success.
    #
    # The caller then sees returncode 0 with empty stdout and reports "hermes
    # returned no output" -- which reads as the agent having said nothing,
    # rather than as its answer being dropped on the floor. Every Tris
    # dispatch failed this way, and it only reproduces when captured, never
    # when run by hand.
    cause = "" if complete else provider_error_since(errors_log, errors_from)
    cause = f"; {cause}" if cause else ""
    if isinstance(response, str) and response.strip():
        print(response)
        sys.stdout.flush()
        if not complete:
            print(f"hermes_oneshot: incomplete provider result{cause}", file=sys.stderr)
            sys.stderr.flush()
        os._exit(0 if complete else 1)
    else:
        print(f"hermes_oneshot: no response produced{cause}", file=sys.stderr)
        sys.stderr.flush()
        os._exit(1)

if __name__ == "__main__":
    main()
