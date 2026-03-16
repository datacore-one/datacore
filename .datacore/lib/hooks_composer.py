#!/usr/bin/env python3
"""Hooks composer — reads registry + modules, outputs .claude/settings.json.

Usage:
    hooks_composer.py rebuild [--dry-run]   Build .claude/settings.json from registry
    hooks_composer.py validate              Check for missing scripts, conflicts

Per DIP-0024: Reactive Hooks Infrastructure.
"""
import argparse, glob, json, os, sys

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

DATACORE_ROOT = os.path.expanduser("~/Data")
REGISTRY_PATH = os.path.join(DATACORE_ROOT, ".datacore", "registry", "hooks.yaml")
MODULES_GLOB = os.path.join(DATACORE_ROOT, ".datacore", "modules", "*", "module.yaml")
OUTPUT_PATH = os.path.join(DATACORE_ROOT, ".claude", "settings.json")


def load_registry():
    """Load core hooks from registry/hooks.yaml."""
    if not os.path.exists(REGISTRY_PATH):
        print(f"Warning: registry not found at {REGISTRY_PATH}", file=sys.stderr)
        return {}
    with open(REGISTRY_PATH) as f:
        data = yaml.safe_load(f) or {}
    return data.get("claude_code", {})


def load_module_hooks():
    """Scan all module.yaml files for hooks: sections."""
    module_hooks = {}
    for mpath in glob.glob(MODULES_GLOB):
        try:
            with open(mpath) as f:
                mod = yaml.safe_load(f) or {}
            hooks = mod.get("hooks", {}).get("claude_code", [])
            if hooks:
                mod_name = mod.get("name", os.path.basename(os.path.dirname(mpath)))
                module_hooks[mod_name] = hooks
        except (yaml.YAMLError, OSError):
            continue
    return module_hooks


def merge_hooks(registry, module_hooks):
    """Merge registry + module hooks, grouped by event, sorted by priority."""
    # Registry is already grouped by event
    merged = {}
    for event, hook_list in registry.items():
        merged[event] = list(hook_list)

    # Add module hooks (flat list with event field)
    for mod_name, hooks in module_hooks.items():
        for hook in hooks:
            event = hook.get("event")
            if not event:
                continue
            hook_copy = dict(hook)
            hook_copy.setdefault("declared_by", mod_name)
            merged.setdefault(event, []).append(hook_copy)

    # Sort each event's hooks by priority
    for event in merged:
        merged[event].sort(key=lambda h: h.get("priority", 500))

    return merged


def compose_claude_settings(merged):
    """Transform merged hooks into Claude Code .claude/settings.json format.

    Claude Code format:
    {
      "hooks": {
        "EventName": [
          {
            "matcher": "pattern",        // optional
            "hooks": [
              {"type": "command", "command": "...", "timeout": N}
            ]
          }
        ]
      }
    }

    Multiple hooks with the same event+matcher get grouped under one entry.
    """
    settings = {"hooks": {}}

    for event, hook_list in merged.items():
        # Group by matcher
        matcher_groups = {}
        for hook in hook_list:
            matcher = hook.get("matcher", "__none__")
            matcher_groups.setdefault(matcher, []).append(hook)

        event_entries = []
        for matcher, hooks in matcher_groups.items():
            entry = {}
            if matcher != "__none__":
                entry["matcher"] = matcher
            entry["hooks"] = []
            for hook in hooks:
                hook_def = {"type": hook.get("type", "command")}
                if hook.get("command"):
                    hook_def["command"] = hook["command"]
                if hook.get("prompt"):
                    hook_def["prompt"] = hook["prompt"]
                if hook.get("timeout"):
                    hook_def["timeout"] = hook["timeout"]
                entry["hooks"].append(hook_def)
            event_entries.append(entry)

        settings["hooks"][event] = event_entries

    return settings


def validate(merged):
    """Check for missing scripts and conflicts."""
    errors = []
    warnings = []

    for event, hook_list in merged.items():
        seen_priorities = {}
        for hook in hook_list:
            # Check script exists
            cmd = hook.get("command", "")
            if cmd:
                # Extract script path from command (e.g., "python3 .datacore/lib/foo.py --bar")
                parts = cmd.split()
                script = None
                for part in parts:
                    if part.endswith(".py") or part.endswith(".sh"):
                        script = part
                        break
                if script:
                    full_path = os.path.join(DATACORE_ROOT, script)
                    if not os.path.exists(full_path):
                        errors.append(f"[{event}] Script not found: {full_path} (declared by {hook.get('declared_by', '?')})")

            # Check priority conflicts
            matcher = hook.get("matcher", "__none__")
            priority = hook.get("priority", 500)
            key = f"{event}:{matcher}:{priority}"
            if key in seen_priorities:
                warnings.append(f"[{event}] Priority conflict at {priority} for matcher '{matcher}': {hook.get('purpose', '?')} vs {seen_priorities[key]}")
            seen_priorities[key] = hook.get("purpose", "?")

    return errors, warnings


def rebuild(dry_run=False):
    """Full rebuild: load → merge → validate → write."""
    registry = load_registry()
    module_hooks = load_module_hooks()
    merged = merge_hooks(registry, module_hooks)

    errors, warnings = validate(merged)
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        print(f"\n{len(errors)} error(s) found. Fix before rebuilding.", file=sys.stderr)
        sys.exit(1)

    settings = compose_claude_settings(merged)

    if dry_run:
        print(json.dumps(settings, indent=2))
        return

    # Read existing settings to preserve non-hook config
    existing = {}
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Merge: replace hooks, keep everything else
    existing["hooks"] = settings["hooks"]

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(existing, f, indent=2)
        f.write("\n")

    hook_count = sum(len(hooks) for hooks in merged.values())
    event_count = len(merged)
    module_count = len(module_hooks)
    print(f"Composed {hook_count} hooks across {event_count} events ({module_count} module(s)) → {OUTPUT_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Datacore hooks composer (DIP-0024)")
    sub = parser.add_subparsers(dest="cmd")

    rb = sub.add_parser("rebuild", help="Build .claude/settings.json from registry")
    rb.add_argument("--dry-run", action="store_true", help="Print output instead of writing")

    sub.add_parser("validate", help="Check for missing scripts, conflicts")

    args = parser.parse_args()

    if args.cmd == "rebuild":
        rebuild(dry_run=args.dry_run)
    elif args.cmd == "validate":
        registry = load_registry()
        module_hooks = load_module_hooks()
        merged = merge_hooks(registry, module_hooks)
        errors, warnings = validate(merged)
        for w in warnings:
            print(f"WARNING: {w}")
        for e in errors:
            print(f"ERROR: {e}")
        if not errors and not warnings:
            hook_count = sum(len(hooks) for hooks in merged.values())
            print(f"All {hook_count} hooks valid. No conflicts.")
        sys.exit(1 if errors else 0)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
