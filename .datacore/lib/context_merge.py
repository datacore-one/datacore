#!/usr/bin/env python3
"""
Layered Context Merge Utility

Merges context files across permission levels:
- .base.md   (PUBLIC)  - Generic template, PRable to upstream
- .space.md  (SPACE)   - Space-specific, tracked in space repo
- .local.md  (PRIVATE) - Personal customizations, always gitignored

- .team.md   (TEAM)    - Team-specific additions

Output: Composed .md file (gitignored, read at runtime)

Registry injection: <!-- REGISTRY:xxx --> markers in templates are replaced
with auto-generated tables from YAML registries (agents.yaml, commands.yaml,
sources.yaml, module.yaml files, infrastructure.yaml).

See DIP-0002 for full specification.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None

# Layer order (later layers extend/override earlier)
LAYERS = [
    ("base", "PUBLIC"),    # Generic template - validated for private content
    ("space", "SPACE"),    # Space-specific - tracked in space repo
    ("team", "TEAM"),      # Future: team additions
    ("local", "PRIVATE"),  # Personal - always gitignored, never validated
]

# Layers that should be validated for private content (only PUBLIC for now)
VALIDATED_LAYERS = ("PUBLIC",)

# Patterns that should never appear in PUBLIC layers
PRIVATE_PATTERNS = [
    # Email addresses
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', 'email address'),
    # Phone numbers
    (r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', 'phone number'),
    # API keys/secrets
    (r'(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*["\']?[A-Za-z0-9]+', 'potential secret'),
    # Dollar amounts (specific financials)
    (r'\$[\d,]+\.\d{2}', 'dollar amount'),
]


##############################################################################
# Registry Injection
# Replace <!-- REGISTRY:xxx --> markers with auto-generated tables
##############################################################################

REGISTRY_MARKER = re.compile(r'<!-- REGISTRY:(\w+) -->')


def _truncate(text: str, max_len: int = 60) -> str:
    """Truncate text at word boundary, strip newlines."""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated + "..."


def _load_yaml(path: Path) -> dict | list | None:
    """Load YAML file, return None if missing or yaml unavailable."""
    if yaml is None or not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def _generate_modules_table(modules_dir: Path) -> str:
    """Generate installed modules table from module.yaml files."""
    if not modules_dir.exists():
        return "_No modules installed._\n"

    rows = []
    for module_yaml in sorted(modules_dir.glob("*/module.yaml")):
        data = _load_yaml(module_yaml)
        if not data:
            continue
        name = data.get("name", module_yaml.parent.name)
        version = data.get("version", "-")
        desc = _truncate(data.get("description", ""), 50)
        provides = data.get("provides", {})
        tools_count = len(provides.get("tools", []))
        skills_count = len(provides.get("skills", []))
        agents_count = len(provides.get("agents", []))
        commands_count = len(provides.get("commands", []))
        priority = data.get("context", {}).get("priority", "-") if isinstance(data.get("context"), dict) else "-"
        rows.append(f"| {name} | {version} | {desc[:50]} | {tools_count} | {skills_count} | {agents_count} | {commands_count} | {priority} |")

    if not rows:
        return "_No modules installed._\n"

    header = "| Module | Version | Description | Tools | Skills | Agents | Cmds | Context |\n"
    header += "|--------|---------|-------------|-------|--------|--------|------|---------|"
    return header + "\n" + "\n".join(rows) + "\n"


def _generate_agents_table(agents_yaml: Path) -> str:
    """Generate agents table from agents.yaml registry."""
    data = _load_yaml(agents_yaml)
    if not data:
        return "_No agents registered._\n"

    rows = []

    # Core agents (agents: dict keyed by name)
    core_agents = data.get("agents", {})
    if isinstance(core_agents, dict):
        for name, info in core_agents.items():
            if not isinstance(info, dict):
                continue
            if info.get("deprecated"):
                continue
            desc = _truncate(info.get("description", ""))
            rows.append(f"| `{name}` | {desc} | core |")

    # Module agents (module_agents: dict keyed by name)
    module_agents = data.get("module_agents", {})
    if isinstance(module_agents, dict):
        for name, info in module_agents.items():
            if not isinstance(info, dict):
                continue
            if info.get("deprecated"):
                continue
            desc = _truncate(info.get("description", ""))
            module = info.get("module", "-")
            rows.append(f"| `{name}` | {desc} | {module} |")

    if not rows:
        return "_No agents registered._\n"

    header = "| Agent | Description | Module |\n"
    header += "|-------|-------------|--------|"
    return header + "\n" + "\n".join(rows) + "\n"


def _generate_commands_table(commands_yaml: Path) -> str:
    """Generate commands table from commands.yaml registry.

    Shows active commands as slash commands, and lists demoted/habit
    commands separately as conversational triggers.
    """
    data = _load_yaml(commands_yaml)
    if not data or "commands" not in data:
        return "_No commands registered._\n"

    active_rows = []
    conversational_rows = []

    # Process both core and module commands
    all_commands = {}
    commands = data.get("commands", {})
    if isinstance(commands, dict):
        all_commands.update(commands)
    module_commands = data.get("module_commands", {})
    if isinstance(module_commands, dict):
        all_commands.update(module_commands)

    for cmd_key, cmd in all_commands.items():
        if not isinstance(cmd, dict):
            continue
        status = cmd.get("status", "active")
        desc = _truncate(cmd.get("description", ""))
        module = cmd.get("module", "core")

        if status == "active":
            active_rows.append(f"| `/{cmd_key}` | {desc} |")
        elif status in ("demoted", "habit"):
            trigger = cmd.get("trigger", "")
            if trigger:
                # Use first trigger phrase as example
                example = trigger.split("|")[0].strip()
                conversational_rows.append(f"| {desc[:40]} | \"{example}\" | {module} |")

    parts = []

    if active_rows:
        header = "**Slash commands** (multi-phase workflows):\n\n"
        header += "| Command | Description |\n"
        header += "|---------|-------------|"
        parts.append(header + "\n" + "\n".join(active_rows))

    if conversational_rows:
        hint = "\n\n**Conversational** (just say what you need — no slash command required):\n\n"
        hint += "| Action | Example Trigger | Module |\n"
        hint += "|--------|-----------------|--------|"
        parts.append(hint + "\n" + "\n".join(conversational_rows))

    if not parts:
        return "_No commands registered._\n"

    return "\n".join(parts) + "\n"


def _generate_sources_table(sources_yaml: Path) -> str:
    """Generate MCP sources table from sources.yaml registry."""
    data = _load_yaml(sources_yaml)
    if not data or "sources" not in data:
        return "_No sources configured._\n"

    rows = []
    for source_key, source in data["sources"].items():
        if not isinstance(source, dict):
            continue
        stype = source.get("type", "?")
        desc = _truncate(source.get("description", ""), 40)
        api_key = source.get("api_key", "-")
        good_for = ", ".join(source.get("good_for", [])) if source.get("good_for") else "-"
        rows.append(f"| {source_key} | {stype} | {desc} | {api_key} | {good_for} |")

    if not rows:
        return "_No sources configured._\n"

    header = "| Source | Type | Description | API Key Env Var | Good For |\n"
    header += "|--------|------|-------------|-----------------|----------|"
    return header + "\n" + "\n".join(rows) + "\n"


def _generate_infrastructure_table(infra_yaml: Path) -> str:
    """Generate infrastructure table from infrastructure.yaml registry."""
    data = _load_yaml(infra_yaml)
    if not data:
        return "_No infrastructure registry found. Create `.datacore/registry/infrastructure.yaml`._\n"

    parts = []
    servers = data.get("servers", {})
    if servers:
        rows = []
        for name, info in servers.items():
            if not isinstance(info, dict):
                continue
            purpose = info.get("purpose", "")
            ssh_alias = info.get("ssh_alias", "-")
            deploy = info.get("deploy_method", "-")
            rows.append(f"| {name} | {ssh_alias} | {purpose[:50]} | {deploy} |")
        header = "**Servers:**\n\n| Name | SSH Alias | Purpose | Deploy Method |\n"
        header += "|------|-----------|---------|---------------|"
        parts.append(header + "\n" + "\n".join(rows))

    return "\n\n".join(parts) + "\n" if parts else "_No infrastructure configured._\n"


def inject_registries(content: str, datacore_root: Path) -> str:
    """
    Replace <!-- REGISTRY:xxx --> markers with auto-generated tables.

    Only applies to root CLAUDE.md (where registries live).
    """
    if yaml is None:
        # Can't inject without PyYAML - leave markers as-is
        return content

    registry_dir = datacore_root / "registry"
    modules_dir = datacore_root / "modules"

    generators = {
        "modules": lambda: _generate_modules_table(modules_dir),
        "agents": lambda: _generate_agents_table(registry_dir / "agents.yaml"),
        "commands": lambda: _generate_commands_table(registry_dir / "commands.yaml"),
        "sources": lambda: _generate_sources_table(registry_dir / "sources.yaml"),
        "infrastructure": lambda: _generate_infrastructure_table(registry_dir / "infrastructure.yaml"),
    }

    def replace_marker(match):
        marker_type = match.group(1)
        generator = generators.get(marker_type)
        if generator:
            return generator()
        return match.group(0)  # Leave unknown markers unchanged

    return REGISTRY_MARKER.sub(replace_marker, content)


def merge_context(
    component_path: Path,
    name: str = "CLAUDE",
    include_markers: bool = True,
    validate: bool = True
) -> tuple[str, list[str]]:
    """
    Merge layered context files into single output.

    Args:
        component_path: Directory containing the layered files
        name: Base name of the context file (default: CLAUDE)
        include_markers: Include HTML comments marking layer boundaries
        validate: Check for private content in public layers

    Returns:
        Tuple of (merged_content, list of warnings)
    """
    component_path = Path(component_path)
    content_parts = []
    warnings = []

    # Header
    if include_markers:
        content_parts.append(f"<!-- AUTO-GENERATED: Do not edit directly -->\n")
        content_parts.append(f"<!-- Source: {name}.base.md + .space.md + .local.md -->\n")
        content_parts.append(f"<!-- Regenerate: datacore context rebuild -->\n\n")

    for layer_suffix, layer_level in LAYERS:
        layer_file = component_path / f"{name}.{layer_suffix}.md"

        if not layer_file.exists():
            continue

        layer_content = layer_file.read_text()

        # Validate PUBLIC layer for private content
        if validate and layer_level in VALIDATED_LAYERS:
            for pattern, description in PRIVATE_PATTERNS:
                matches = re.findall(pattern, layer_content)
                if matches:
                    warnings.append(
                        f"WARNING: {layer_file.name} contains potential {description}: "
                        f"{matches[:3]}{'...' if len(matches) > 3 else ''}"
                    )

        # Add layer marker
        if include_markers:
            content_parts.append(f"<!-- === Layer: {layer_suffix.upper()} ({layer_level}) === -->\n\n")

        content_parts.append(layer_content.strip())
        content_parts.append("\n\n")

    return "".join(content_parts).strip() + "\n", warnings


def rebuild_context(
    component_path: Path,
    name: str = "CLAUDE",
    dry_run: bool = False,
    include_markers: bool = True
) -> tuple[bool, list[str]]:
    """
    Rebuild a composed context file from its layers.

    Args:
        component_path: Directory containing the layered files
        name: Base name of the context file
        dry_run: If True, don't write file, just validate
        include_markers: Include layer boundary markers

    Returns:
        Tuple of (success, list of warnings/errors)
    """
    component_path = Path(component_path)
    output_file = component_path / f"{name}.md"

    # Check if any layer files exist
    layer_files = [component_path / f"{name}.{suffix}.md" for suffix, _ in LAYERS]
    existing_layers = [f for f in layer_files if f.exists()]

    if not existing_layers:
        return False, [f"No layer files found for {name} in {component_path}"]

    # Merge content
    merged_content, warnings = merge_context(
        component_path, name, include_markers=include_markers
    )

    # Inject registry tables if this is a root-level CLAUDE.md
    # (detected by presence of .datacore/ directory at the same level)
    datacore_dir = component_path / ".datacore"
    if datacore_dir.is_dir() and REGISTRY_MARKER.search(merged_content):
        merged_content = inject_registries(merged_content, datacore_dir)

    if dry_run:
        print(f"Would write to {output_file}:")
        print("-" * 40)
        print(merged_content[:500] + "..." if len(merged_content) > 500 else merged_content)
        return len(warnings) == 0, warnings

    # Write output
    output_file.write_text(merged_content)

    return len(warnings) == 0, warnings


def validate_layers(component_path: Path, name: str = "CLAUDE") -> list[str]:
    """
    Validate that PUBLIC layer doesn't contain private content.

    Returns:
        List of validation errors
    """
    _, warnings = merge_context(
        Path(component_path), name, include_markers=False, validate=True
    )
    return warnings


def find_all_contexts(root_path: Path) -> list[tuple[Path, str]]:
    """
    Find all context files that need rebuilding.

    Returns:
        List of (directory, base_name) tuples
    """
    contexts = []
    root_path = Path(root_path)

    # Find all .base.md files
    for base_file in root_path.rglob("*.base.md"):
        name = base_file.stem.replace(".base", "")
        contexts.append((base_file.parent, name))

    return contexts


def rebuild_all(root_path: Path, dry_run: bool = False) -> tuple[int, int, list[str]]:
    """
    Rebuild all context files under a root path.

    Returns:
        Tuple of (success_count, failure_count, all_warnings)
    """
    contexts = find_all_contexts(root_path)
    success_count = 0
    failure_count = 0
    all_warnings = []

    for component_path, name in contexts:
        success, warnings = rebuild_context(component_path, name, dry_run=dry_run)

        if success:
            success_count += 1
            print(f"OK: {component_path / name}.md")
        else:
            failure_count += 1
            print(f"WARN: {component_path / name}.md")

        all_warnings.extend(warnings)

    return success_count, failure_count, all_warnings


def main():
    parser = argparse.ArgumentParser(
        description="Merge layered context files (DIP-0002)"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # rebuild command
    rebuild_parser = subparsers.add_parser("rebuild", help="Rebuild composed context files")
    rebuild_parser.add_argument(
        "--path", type=Path, default=Path("."),
        help="Path to component directory or root for --all"
    )
    rebuild_parser.add_argument(
        "--name", default="CLAUDE",
        help="Base name of context file (default: CLAUDE)"
    )
    rebuild_parser.add_argument(
        "--all", action="store_true",
        help="Rebuild all context files under path"
    )
    rebuild_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without writing"
    )
    rebuild_parser.add_argument(
        "--no-markers", action="store_true",
        help="Don't include layer boundary markers"
    )

    # validate command
    validate_parser = subparsers.add_parser("validate", help="Validate layers for private content")
    validate_parser.add_argument(
        "--path", type=Path, default=Path("."),
        help="Path to component directory"
    )
    validate_parser.add_argument(
        "--name", default="CLAUDE",
        help="Base name of context file (default: CLAUDE)"
    )

    # trace command
    trace_parser = subparsers.add_parser("trace", help="Show which layer contains a section")
    trace_parser.add_argument("section", help="Section header to find")
    trace_parser.add_argument(
        "--path", type=Path, default=Path("."),
        help="Path to component directory"
    )
    trace_parser.add_argument(
        "--name", default="CLAUDE",
        help="Base name of context file (default: CLAUDE)"
    )

    args = parser.parse_args()

    if args.command == "rebuild":
        if args.all:
            success, failure, warnings = rebuild_all(args.path, dry_run=args.dry_run)
            print(f"\nRebuilt: {success} OK, {failure} with warnings")
        else:
            success, warnings = rebuild_context(
                args.path, args.name,
                dry_run=args.dry_run,
                include_markers=not args.no_markers
            )

        for w in warnings:
            print(f"  {w}", file=sys.stderr)

        sys.exit(0 if not warnings else 1)

    elif args.command == "validate":
        warnings = validate_layers(args.path, args.name)

        if warnings:
            print("Validation failed:")
            for w in warnings:
                print(f"  {w}")
            sys.exit(1)
        else:
            print("Validation passed")
            sys.exit(0)

    elif args.command == "trace":
        component_path = Path(args.path)
        found_in = []

        for layer_suffix, layer_level in LAYERS:
            layer_file = component_path / f"{args.name}.{layer_suffix}.md"
            if layer_file.exists():
                content = layer_file.read_text()
                if args.section.lower() in content.lower():
                    found_in.append(f"{layer_suffix} ({layer_level})")

        if found_in:
            print(f"'{args.section}' found in: {', '.join(found_in)}")
        else:
            print(f"'{args.section}' not found in any layer")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
