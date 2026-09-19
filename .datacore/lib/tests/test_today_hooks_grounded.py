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
import sys
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
TODAY = ROOT / ".datacore" / "commands" / "today.md"
MODULES = ROOT / ".datacore" / "modules"


def _declared_hooks() -> dict[str, str]:
    """module name -> slot, for every module declaring hooks.today.

    `hooks.today` has FOUR shapes, not the three this function used to know: a
    path string, an inline block of instructions, a dict, and — since
    chief-of-staff began claiming three briefing sections — a LIST of dicts.
    The list arrived without this copy of the parser hearing about it, and it
    took the `else` branch, so every module using it was reported as slot
    "inline". That is the bug this file's own docstring is about, reappearing in
    the test written to catch it.

    So the shapes are read by `today_registry`, the module /today actually uses.
    `slot` is not one of its fields — it is the older axis, inline vs post, that
    only five manifests still set — so it is read here directly and the shape
    handling is not duplicated.
    """
    LIB = pathlib.Path(__file__).resolve().parents[1]
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    import today_registry

    out: dict[str, str] = {}
    for reg in today_registry.load(MODULES):
        raw = yaml.safe_load((MODULES / reg.module / "module.yaml").read_text()) or {}
        h = (raw.get("hooks") or {}).get("today")
        entries = h if isinstance(h, list) else [h]
        slots = {e.get("slot") for e in entries if isinstance(e, dict) and e.get("slot")}
        # A module claiming several sections declares at most one slot; absent
        # means inline, which is what /today assumes for an unmarked hook.
        out[reg.module] = next(iter(slots), "inline")
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
