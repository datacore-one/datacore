"""
test_role_loader.py — Tests for role_loader.py (DIP-0002 layered merge).
"""

import pytest
from pathlib import Path

from ventures.lib.role_loader import (
    RoleContext,
    find_role_file,
    load_role,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_FRONTMATTER = """\
---
title: Product Manager
category: strategy
---
"""

BASE_BODY = """\
## Responsibilities

- Define product vision
- Prioritise backlog
"""

VENTURE_FRONTMATTER = """\
---
venture: megaphone
inherits: product-manager
---
"""

VENTURE_BODY = """\
## Megaphone Focus

- Cold outreach website pipeline
"""


def _write_base(tmp_path: Path, role_name: str = "product-manager") -> Path:
    """Write a base archetype role file."""
    base_dir = tmp_path / "templates" / "roles"
    base_dir.mkdir(parents=True)
    content = BASE_FRONTMATTER + BASE_BODY
    p = base_dir / f"{role_name}.base.md"
    p.write_text(content)
    return base_dir


def _write_venture(tmp_path: Path, role_name: str = "product-manager", venture: str = "megaphone") -> Path:
    """Write a venture instance role file."""
    venture_dir = tmp_path / "ventures" / venture / "roles"
    venture_dir.mkdir(parents=True)
    content = VENTURE_FRONTMATTER + VENTURE_BODY
    p = venture_dir / f"{role_name}.md"
    p.write_text(content)
    return venture_dir


# ---------------------------------------------------------------------------
# Tests: find_role_file
# ---------------------------------------------------------------------------


def test_find_role_file_base(tmp_path):
    """find_role_file returns the .base.md path when no venture dir given."""
    templates_dir = _write_base(tmp_path)
    result = find_role_file("product-manager", templates_dir)
    assert result is not None
    assert result.name == "product-manager.base.md"


def test_find_role_file_venture_preferred(tmp_path):
    """find_role_file returns venture instance when available, not the base."""
    templates_dir = _write_base(tmp_path)
    venture_dir = _write_venture(tmp_path)
    result = find_role_file("product-manager", templates_dir, venture_dir=venture_dir)
    assert result is not None
    assert result.name == "product-manager.md"
    assert "megaphone" in str(result)


# ---------------------------------------------------------------------------
# Tests: load_role
# ---------------------------------------------------------------------------


def test_load_role_from_base(tmp_path):
    """load_role returns a RoleContext populated from base frontmatter."""
    templates_dir = _write_base(tmp_path)
    ctx = load_role("product-manager", templates_dir)
    assert isinstance(ctx, RoleContext)
    assert ctx.name == "product-manager"
    assert ctx.title == "Product Manager"
    assert ctx.category == "strategy"
    assert ctx.venture is None
    assert ctx.inherits is None
    assert "Define product vision" in ctx.content
    assert ctx.base_content != ""
    assert ctx.venture_content == ""


def test_load_role_with_venture_override(tmp_path):
    """load_role merges base + venture content when venture dir is provided."""
    templates_dir = _write_base(tmp_path)
    venture_dir = _write_venture(tmp_path)
    ctx = load_role("product-manager", templates_dir, venture_dir=venture_dir)
    # Frontmatter from base
    assert ctx.title == "Product Manager"
    assert ctx.category == "strategy"
    # Venture metadata
    assert ctx.venture == "megaphone"
    assert ctx.inherits == "product-manager"
    # Both bodies present in merged content
    assert "Define product vision" in ctx.content
    assert "Cold outreach website pipeline" in ctx.content
    assert ctx.base_content != ""
    assert ctx.venture_content != ""


def test_load_role_base_only_when_no_venture(tmp_path):
    """load_role uses only base content when venture_dir has no matching file."""
    templates_dir = _write_base(tmp_path)
    # venture dir exists but no role file inside
    empty_venture_dir = tmp_path / "ventures" / "other" / "roles"
    empty_venture_dir.mkdir(parents=True)
    ctx = load_role("product-manager", templates_dir, venture_dir=empty_venture_dir)
    assert "Define product vision" in ctx.content
    assert ctx.venture_content == ""


def test_load_role_not_found(tmp_path):
    """load_role raises FileNotFoundError when base archetype is missing."""
    templates_dir = tmp_path / "templates" / "roles"
    templates_dir.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        load_role("nonexistent-role", templates_dir)
