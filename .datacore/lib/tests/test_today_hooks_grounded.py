"""The /today command's hook tables must match the module manifests.

Bug class 1, in the file that documents the class. `/today` already warns "the
glob above is authoritative — do not maintain a list here", added after its
inline-hooks table was found listing ONE module while nineteen registered. The
fix was applied to that table and not to the slot-mapping table below it,
which had drifted to 16 rows against 20 registered hooks.

That is the shape of the class: the correction lands on the copy someone was
looking at, and the other copies keep their own version of the truth.
"""
from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
TODAY = ROOT / ".datacore" / "commands" / "today.md"
MODULES = ROOT / ".datacore" / "modules"


def _declared_hooks() -> dict[str, str]:
    """module name -> slot, for every module declaring hooks.today.

    `hooks.today` has three shapes — a path string, a dict with file/slot, or
    a block of inline instructions — and assuming the first raises
    "OSError: File name too long" on the third. The /today file records that
    lesson; this honours it.
    """
    out = {}
    for my in sorted(MODULES.glob("*/module.yaml")):
        m = yaml.safe_load(my.read_text()) or {}
        h = (m.get("hooks") or {}).get("today")
        if not h:
            continue
        slot = h.get("slot", "inline") if isinstance(h, dict) else "inline"
        out[m.get("name") or my.parent.name] = slot
    return out


def _table_modules() -> set[str]:
    """Module names in the 'Module Hook System' slot-mapping table."""
    text = TODAY.read_text()
    start = text.index("## Module Hook System")
    section = text[start:start + 4000]
    # `[a-z0-9-]+` also matches a markdown separator row (|--------|), which
    # then reports as a module named "--------" that declares no hook. Require
    # at least one letter.
    rows = re.findall(r"^\|\s*([a-z0-9][a-z0-9-]*)\s*\|", section, re.M)
    return {r for r in rows if re.search(r"[a-z]", r)}


def test_slot_table_lists_every_module_that_registers_a_today_hook():
    declared = set(_declared_hooks())
    listed = _table_modules()
    missing = sorted(declared - listed)
    assert not missing, (
        "modules registering hooks.today but absent from the slot table in "
        f"{TODAY.relative_to(ROOT)} — their contribution is undocumented and a "
        f"reader cannot know it runs:\n  " + "\n  ".join(missing))


def test_slot_table_lists_no_module_that_does_not_register_a_hook():
    declared = set(_declared_hooks())
    listed = _table_modules()
    phantom = sorted(listed - declared)
    assert not phantom, (
        "slot table names modules that declare no hooks.today — the table "
        f"promises briefing content nothing produces:\n  " + "\n  ".join(phantom))


def test_post_hook_table_matches_the_modules_declaring_slot_post():
    """The post-hooks table drives step 18; a module missing from it silently
    never runs, which is how audio briefings could stop without an error."""
    text = TODAY.read_text()
    start = text.index("**Currently registered post-hooks:**")
    rows = {r for r in re.findall(
        r"^\|\s*([a-z0-9][a-z0-9-]*)\s*\|", text[start:start + 700], re.M)
        if re.search(r"[a-z]", r)}
    declared = {n for n, slot in _declared_hooks().items() if slot == "post"}
    assert rows == declared, (
        f"post-hook table {sorted(rows)} != modules declaring slot: post "
        f"{sorted(declared)}")
