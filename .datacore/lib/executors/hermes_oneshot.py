#!/usr/bin/env python3
"""Working non-interactive Hermes invocation for the ledger dispatcher.

`hermes -z` and `hermes chat -q` both hang in this environment because the
oneshot module redirects stdout/stderr to devnull, which breaks the SSH
terminal backend's file sync subprocess.

This wrapper calls AIAgent.run_conversation() directly, avoiding the
redirect. It sets HERMES_YOLO_MODE=1 for auto-approval and uses os._exit()
to skip cleanup that can also hang.

Usage:
    python3 hermes_oneshot.py 'your prompt here'
    python3 hermes_oneshot.py 'your prompt here' --toolsets file,terminal

Exit code 0 = success, response on stdout.
Exit code 1 = agent failure or empty response.
"""
import sys, os, time

os.environ.setdefault("HERMES_YOLO_MODE", "1")
os.environ.setdefault("HERMES_ACCEPT_HOOKS", "1")
os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes"))

def main():
    if len(sys.argv) < 2:
        print("Usage: hermes_oneshot.py <prompt> [--toolsets t1,t2,...]", file=sys.stderr)
        sys.exit(2)

    prompt = sys.argv[1]
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

    result = agent.run_conversation(prompt)
    response = result.get("final_response") or ""

    if response:
        print(response)
        os._exit(0)
    else:
        print("hermes_oneshot: no response produced", file=sys.stderr)
        os._exit(1)

if __name__ == "__main__":
    main()
