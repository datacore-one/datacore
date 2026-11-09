"""A cadence's evidence path must be a place the space's pre-commit hook accepts.

2026-10-19..23: the weekly strategy pipeline report was written to
`reports/` at the space root because its template said so. The space hook
refuses new files in an unexpected root folder (DIP-0015; that space moved
`reports/` into `1-tracks/trading/reports/` on 2026-10-18), so the report
never left the host: the fleet sync failed on it, the nightly preflight sat
the space out, and the cadence showed as days late.

The allowed root folders are read from the hook itself, so this test follows
the hook rather than a copy of its list.
"""
import re
from pathlib import Path

import yaml

CORE = Path(__file__).resolve().parents[2]
HOOK = CORE / "hooks" / "space-pre-commit"
TEMPLATES = CORE / "templates" / "cadences"


def _hook_list(name: str) -> set[str]:
    text = HOOK.read_text()
    m = re.search(rf'^{name}="([^"]*)"', text, re.M)
    assert m, f"{name} not found in {HOOK}"
    return set(m.group(1).split())


def _evidence_paths():
    for path in sorted(TEMPLATES.glob("*.md")):
        text = path.read_text()
        if not text.startswith("---\n"):
            continue
        meta = yaml.safe_load(text.split("---\n", 2)[1]) or {}
        ev = (meta.get("evidence") or {}).get("path")
        if ev:
            yield path.name, str(ev)


def test_templates_have_evidence_paths():
    assert list(_evidence_paths()), "no cadence template declares an evidence path"


def test_every_evidence_path_starts_in_an_allowed_root_folder():
    allowed = _hook_list("ALLOWED_DIRS") | _hook_list("TOOL_DIRS") | _hook_list("PERSONAL_DIRS")
    files = _hook_list("ALLOWED_FILES")
    bad = []
    for name, ev in _evidence_paths():
        top = ev.split("/", 1)[0]
        if "/" in ev and top not in allowed:
            bad.append(f"{name}: {ev} (root folder {top!r} is refused by the space hook)")
        elif "/" not in ev and top not in files:
            bad.append(f"{name}: {ev} (a root file the space hook refuses)")
    assert not bad, "\n".join(bad)
