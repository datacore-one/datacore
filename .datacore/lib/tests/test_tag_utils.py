"""Tag spelling across the org/markdown boundary.

Org-mode tags may only contain [a-zA-Z0-9_@#%]. One invalid tag voids the
ENTIRE trailing tag string on a heading — siblings included — so a heading
tagged `:privacy-tech:ops:` has NO tags at all as far as any query is
concerned, while still looking correctly tagged in the file.

Canonical/markdown tags stay kebab-case per DIP-0014; the org spelling
substitutes underscores. These tests pin that boundary, because the converter
used to emit hyphens straight into org files and manufactured 343 broken
headings across the installation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tag_utils import from_org_tag, inline_to_org, org_to_inline, to_org_tag  # noqa: E402


def test_to_org_tag_replaces_hyphens():
    assert to_org_tag("privacy-tech") == "privacy_tech"
    assert to_org_tag("fds-H005") == "fds_H005"
    assert to_org_tag("ops") == "ops"
    assert to_org_tag("") == ""


def test_from_org_tag_restores_hyphens():
    assert from_org_tag("privacy_tech") == "privacy-tech"
    assert from_org_tag("ops") == "ops"


def test_inline_to_org_never_emits_a_hyphen():
    """The regression: this is what put invalid tags into org files."""
    out = inline_to_org("#project-alpha, #ops, #privacy-tech")
    assert out == ":project_alpha:ops:privacy_tech:"
    assert "-" not in out


def test_org_to_inline_returns_canonical_spelling():
    assert org_to_inline(":project_alpha:ops:legal:") == "#project-alpha, #ops, #legal"


def test_round_trip_is_stable():
    original = "#privacy-tech, #data-economy, #ops"
    assert org_to_inline(inline_to_org(original)) == original


def test_org_charset_holds_for_every_registry_tag():
    """Every tag the registry knows must survive conversion into a legal org tag."""
    import re
    import yaml

    registry = Path.home() / "Data" / ".datacore" / "tags.yaml"
    if not registry.exists():
        return  # registry is optional for this check
    raw = yaml.safe_load(registry.read_text()) or {}

    # Tag names are the second-level keys (top level is the section, e.g.
    # "domains" / "ai_delegation"). Structural keys elsewhere in the file —
    # sync_label_mapping entries like ":AI:research:", priority cookies like
    # "[#A]" — are not tag names and are deliberately skipped.
    names = {
        name
        for section, entries in raw.items()
        if isinstance(entries, dict) and section not in ("sync_label_mapping", "validation")
        for name in entries
        if isinstance(name, str) and not any(c in name for c in ":[] ")
    }
    valid = re.compile(r"^[A-Za-z0-9_@#%]+$")
    offenders = [n for n in names if not valid.match(to_org_tag(n))]
    assert not offenders, f"tags that cannot be spelled in org even after conversion: {sorted(offenders)[:10]}"
