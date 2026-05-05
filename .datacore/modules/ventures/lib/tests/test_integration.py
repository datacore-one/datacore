"""
test_integration.py — Integration tests for the Autonomous Venture Framework.

Proves the full pipeline: venture.yaml → cadence engine → budget → org tasks → role loading.
"""

import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from venture_loader import load_venture, load_venture_file
from cadence_engine import (
    find_overdue_cadences,
    generate_cadence_task,
    load_cadence_log,
    save_cadence_log,
)
from budget_tracker import (
    check_can_spend,
    load_ledger,
    record_spend,
    save_ledger,
)
from role_loader import load_role


# Fixed test date
TODAY = date(2026, 4, 9)


# ---------------------------------------------------------------------------
# Test 1: Full cadence cycle
# ---------------------------------------------------------------------------


def test_full_cadence_cycle(tmp_path):
    """Simulate a complete nightshift venture cycle end-to-end.

    Steps:
    1. Create venture.yaml in tmp_path
    2. Load venture via load_venture_file
    3. Find overdue cadences (all should be overdue — never run)
    4. Generate org tasks for overdue cadences
    5. Record budget spend (simulate execution)
    6. Check budget with check_can_spend
    7. Update cadence log, save it
    8. Re-check overdue — completed cadence should no longer appear
    """
    # Step 1: Create venture.yaml
    venture_data = {
        "name": "test-venture",
        "description": "Integration test venture",
        "stage": "validation",
        "space": "4-forge",
        "autonomy": 1,
        "budget": {
            "ceiling": 200,
            "ai_tokens": 0,
            "real_spend": 0,
        },
        # Roles use the dict format documented in venture.yaml schema.
        # The previous list-of-dicts shape was silently dropped by the
        # pre-Pydantic loader; Pydantic v2 enforces the correct shape.
        "roles": {
            "operator": {
                "description": "Does everything",
                "cadences": {"daily": ["check-email"]},
                "budget_authority": 10,
            }
        },
        "nightshift": {
            "enabled": True,
        },
    }
    venture_file = tmp_path / "venture.yaml"
    with open(venture_file, "w") as f:
        yaml.safe_dump(venture_data, f)

    # Step 2: Load venture via load_venture_file
    venture = load_venture_file(venture_file)
    assert venture.name == "test-venture"
    assert venture.stage == "validation"

    # Step 3: Find overdue cadences — none have ever run
    # Cadence engine uses the roles dict from venture.yaml format directly.
    # We construct the roles dict matching that format.
    roles_dict = {
        "operator": {
            "description": "Does everything",
            "cadences": {
                "daily": ["check-email", "check-metrics"],
                "weekly": ["strategy-review"],
            },
            "budget_authority": 10,
        }
    }
    cadence_log_path = tmp_path / "cadence-log.yaml"
    cadence_log = load_cadence_log(cadence_log_path)  # empty — file doesn't exist
    overdue = find_overdue_cadences(roles_dict, cadence_log, today=TODAY)

    # All 3 cadences should be overdue (never run)
    overdue_names = [e.cadence_name for e in overdue]
    assert "check-email" in overdue_names
    assert "check-metrics" in overdue_names
    assert "strategy-review" in overdue_names
    assert len(overdue) == 3

    # Step 4: Generate org tasks for all overdue cadences
    tasks = [generate_cadence_task(e, venture_name=venture.name) for e in overdue]
    assert len(tasks) == 3
    for task in tasks:
        assert task["state"] == "TODO"
        assert ":AI:" in task["tags_str"]
        assert venture.name in task["heading"]

    # Step 5: Record budget spend (simulate execution of check-email)
    ledger_path = tmp_path / "budget-ledger.yaml"
    ledger = load_ledger(ledger_path, current_month="2026-04")
    ledger = record_spend(
        ledger,
        amount=5.0,
        category="ai_tokens",
        description="check-email execution",
        date_str=str(TODAY),
    )
    assert ledger.total_ai == 5.0

    # Step 6: Check budget allows further spend
    ok, reason = check_can_spend(
        ledger,
        amount=10.0,
        category="ai_tokens",
        monthly_ceiling=200,
        ai_ceiling=150,
        real_ceiling=50,
        approval_threshold=25,
    )
    assert ok is True, f"Expected spend to be allowed, got: {reason}"

    # Step 7: Update cadence log for check-email, save it
    cadence_log.setdefault("operator", {}).setdefault("daily", {})
    cadence_log["operator"]["daily"]["check-email"] = str(TODAY)
    save_ledger(ledger, ledger_path)
    save_cadence_log(cadence_log, cadence_log_path)

    # Verify file was saved
    assert cadence_log_path.exists()
    assert ledger_path.exists()

    # Step 8: Re-check overdue — check-email should no longer appear
    reloaded_log = load_cadence_log(cadence_log_path)
    overdue_after = find_overdue_cadences(roles_dict, reloaded_log, today=TODAY)
    overdue_after_names = [e.cadence_name for e in overdue_after]

    assert "check-email" not in overdue_after_names  # completed today
    assert "check-metrics" in overdue_after_names    # still overdue
    assert "strategy-review" in overdue_after_names  # still overdue


# ---------------------------------------------------------------------------
# Test 2: Role loading with venture override
# ---------------------------------------------------------------------------


def test_role_loading_with_venture(tmp_path):
    """Load role archetype + venture override via DIP-0002 layered merge.

    Steps:
    1. Create base template dir with operator.base.md
    2. Create venture role dir with operator.md (has venture-specific context)
    3. Load role — verify both base and venture content present
    4. Verify venture field set correctly
    """
    # Step 1: Create base template with frontmatter and body
    templates_dir = tmp_path / "templates" / "roles"
    templates_dir.mkdir(parents=True)
    base_content = """\
---
title: Operator
category: operations
---

You are the operator archetype. Handle daily operations and metrics.
"""
    (templates_dir / "operator.base.md").write_text(base_content)

    # Step 2: Create venture-specific role override
    venture_roles_dir = tmp_path / "venture-roles"
    venture_roles_dir.mkdir(parents=True)
    venture_content = """\
---
venture: test-venture
inherits: operator
---

Focus on Etsy product research and digital goods revenue for test-venture.
"""
    (venture_roles_dir / "operator.md").write_text(venture_content)

    # Step 3: Load role with layered merge
    role = load_role(
        role_name="operator",
        templates_dir=templates_dir,
        venture_dir=venture_roles_dir,
    )

    # Verify both base and venture content are present in merged content
    assert "operator archetype" in role.base_content
    assert "Etsy product research" in role.venture_content
    assert "operator archetype" in role.content
    assert "Etsy product research" in role.content

    # The separator should appear between the two sections
    assert "---" in role.content

    # Step 4: Verify venture field set correctly
    assert role.venture == "test-venture"
    assert role.name == "operator"
    assert role.title == "Operator"
    assert role.category == "operations"
    assert role.inherits == "operator"
