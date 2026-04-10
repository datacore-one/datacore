"""
role_loader.py — Load agent role archetypes with DIP-0002 layered merge.

Base archetypes live in `.datacore/templates/roles/{role}.base.md`.
Venture instances live in `[space]/.datacore/roles/{role}.md` (optional).

The merged content = base_content + separator + venture_content, giving
agents full context: shared archetype + venture-specific focus.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


MERGE_SEPARATOR = "\n\n---\n\n"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class RoleContext:
    """Fully resolved role context after layered merge."""

    name: str
    title: str
    category: str
    venture: Optional[str]
    inherits: Optional[str]
    content: str          # full merged content (base + venture)
    base_content: str     # body of the base archetype (no frontmatter)
    venture_content: str  # body of the venture instance (no frontmatter), or ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from markdown text.

    Returns (frontmatter_dict, body_text).  If there is no frontmatter
    block the dict is empty and body_text is the full text.
    """
    text = text.lstrip()
    if not text.startswith("---"):
        return {}, text

    # Find the closing '---'
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")

    meta: dict = {}
    for line in fm_block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    return meta, body


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_role_file(
    role_name: str,
    templates_dir: Path,
    venture_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Return the best role file path for *role_name*.

    Preference order (DIP-0002):
    1. Venture instance  — ``{venture_dir}/{role_name}.md``
    2. Base archetype    — ``{templates_dir}/{role_name}.base.md``

    Returns ``None`` when neither file exists.
    """
    if venture_dir is not None:
        venture_file = Path(venture_dir) / f"{role_name}.md"
        if venture_file.exists():
            return venture_file

    base_file = Path(templates_dir) / f"{role_name}.base.md"
    if base_file.exists():
        return base_file

    return None


def load_role(
    role_name: str,
    templates_dir: Path,
    venture_dir: Optional[Path] = None,
) -> RoleContext:
    """Load a role with DIP-0002 layered merge.

    The base archetype is REQUIRED — raises ``FileNotFoundError`` if missing.
    The venture instance is optional; when present its body is appended after
    a separator.

    Returns a fully populated :class:`RoleContext`.
    """
    templates_dir = Path(templates_dir)

    # --- Base (required) ---------------------------------------------------
    base_file = templates_dir / f"{role_name}.base.md"
    if not base_file.exists():
        raise FileNotFoundError(
            f"Base archetype not found for role '{role_name}': {base_file}"
        )

    base_text = base_file.read_text()
    base_meta, base_body = _parse_frontmatter(base_text)

    # --- Venture instance (optional) ---------------------------------------
    venture_meta: dict = {}
    venture_body: str = ""

    if venture_dir is not None:
        venture_file = Path(venture_dir) / f"{role_name}.md"
        if venture_file.exists():
            venture_text = venture_file.read_text()
            venture_meta, venture_body = _parse_frontmatter(venture_text)

    # --- Merge -------------------------------------------------------------
    if venture_body:
        merged = base_body + MERGE_SEPARATOR + venture_body
    else:
        merged = base_body

    return RoleContext(
        name=role_name,
        title=base_meta.get("title", ""),
        category=base_meta.get("category", ""),
        venture=venture_meta.get("venture") or None,
        inherits=venture_meta.get("inherits") or None,
        content=merged,
        base_content=base_body,
        venture_content=venture_body,
    )
