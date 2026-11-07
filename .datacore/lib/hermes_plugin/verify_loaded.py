"""Check that Hermes loads the deployed Datacore plugin, and only that copy.

Run with the Hermes interpreter on the Hermes host (or in its pod):

    <hermes python> verify_loaded.py [~/.hermes/plugins]

Prints OK, or one line per problem and exits 1. Added 2026-11-07: a backup
folder left inside ~/.hermes/plugins/ carried the same plugin name, Hermes
loaded it instead of the deployed copy, and the guards it lacked were
reported live for an evening.
"""
from __future__ import annotations

import sys
from pathlib import Path

WANT = ["pre_tool_call", "pre_llm_call", "on_session_start"]


def problems(plugins: Path, loaded_file: str, hooks: dict, want=WANT) -> list[str]:
    out = []
    for d in sorted(p for p in plugins.iterdir() if p.is_dir() and p.name != "datacore"):
        y = d / "plugin.yaml"
        if y.exists() and any(l.strip() == "name: datacore" for l in y.read_text().splitlines()):
            out.append(f"{d} is a second copy of the datacore plugin; move it out of {plugins}")
    expected = (plugins / "datacore" / "__init__.py").resolve()
    if not loaded_file or Path(loaded_file).resolve() != expected:
        out.append(f"datacore loaded from {loaded_file or 'nowhere'}, not {expected}")
    out += [f"hook {h} is not registered" for h in want if not hooks.get(h)]
    return out


def main() -> int:
    plugins = Path(sys.argv[1] if len(sys.argv) > 1 else "~/.hermes/plugins").expanduser()
    from hermes_cli.plugins import discover_plugins, get_plugin_manager  # noqa: PLC0415
    discover_plugins()
    pm = get_plugin_manager()
    mod = sys.modules.get("hermes_plugins.datacore")
    hooks = {k: sum(1 for cb in v if getattr(cb, "__module__", "") == "hermes_plugins.datacore")
             for k, v in pm._hooks.items()}
    found = problems(plugins, getattr(mod, "__file__", ""), hooks)
    print("\n".join(found) or "OK: datacore loaded from the deployed copy with " + ", ".join(WANT))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
