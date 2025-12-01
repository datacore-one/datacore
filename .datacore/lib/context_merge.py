#!/usr/bin/env python3
"""
Layered Context Merge Utility

Merges context files across permission levels:
- .base.md   (PUBLIC)  - Generic template, PRable to upstream
- .org.md    (ORG)     - Organization customizations, tracked in fork
- .team.md   (TEAM)    - Team-specific additions, optionally tracked
- .local.md  (PRIVATE) - Personal notes, always gitignored

Output: Composed .md file (gitignored, read at runtime)

See DIP-0002 for full specification.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

# Layer order (later layers extend/override earlier)
LAYERS = [
    ("base", "PUBLIC"),
    ("org", "ORG"),
    ("team", "TEAM"),
    ("local", "PRIVATE"),
]

# Patterns that should never appear in public/org layers
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
        content_parts.append(f"<!-- Source: {name}.base.md + .org.md + .team.md + .local.md -->\n")
        content_parts.append(f"<!-- Regenerate: datacore context rebuild -->\n\n")

    for layer_suffix, layer_level in LAYERS:
        layer_file = component_path / f"{name}.{layer_suffix}.md"

        if not layer_file.exists():
            continue

        layer_content = layer_file.read_text()

        # Validate public/org layers for private content
        if validate and layer_level in ("PUBLIC", "ORG"):
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
    Validate that public/org layers don't contain private content.

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
