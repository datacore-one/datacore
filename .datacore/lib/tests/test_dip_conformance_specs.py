"""Executable conformance for the DIPs whose substance is mechanically checkable.

WHY THIS FILE EXISTS. dip_conformance.py derives a DIP's status instead of
reading the typed one, and one of its four signals is "some test names this
DIP". That signal was failing on 32 of 45 — not because the DIPs were wrong,
but because nobody had ever bound them to an executable assertion. A spec that
nothing checks is indistinguishable from a spec nothing follows; that is the
whole finding of 2026-09-02.

WHAT COUNTS AS A CONFORMANCE TEST. Naming a DIP in a docstring is not one. The
temptation here is real and worth stating plainly: adding `# DIP-00NN` to 32
files would move the derived-status counter to zero while changing nothing,
and would be precisely the unverified-claim defect this work exists to close.
So every test below asserts a normative requirement written in its DIP, and
would fail if the system stopped meeting it.

DIPs whose conformance is NOT mechanically checkable — process DIPs, ones
gated on external signals, ones describing work deliberately not built — are
absent on purpose. They should keep deriving as Draft.
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
DIPS = ROOT / ".datacore" / "dips"
MODULES = ROOT / ".datacore" / "modules"


# --- DIP-0000: DIP Template -------------------------------------------------
# Normative: every DIP carries the template's metadata block. The template is
# the contract; a DIP missing Status or Created cannot be governed at all --
# dip_conformance.py reads exactly these fields.

DIP0000_REQUIRED = ("DIP", "Title", "Status", "Created")


@pytest.mark.parametrize(
    "dip_file", sorted(DIPS.glob("DIP-*.md")), ids=lambda p: p.stem[:12])
def test_dip_0000_every_dip_carries_the_template_metadata(dip_file):
    text = dip_file.read_text(errors="replace")
    fields = set(re.findall(r"^\|\s*\*\*([^*]+)\*\*\s*\|", text, re.M))
    missing = [f for f in DIP0000_REQUIRED if f not in fields]
    assert not missing, (
        f"{dip_file.name} omits {missing} from the DIP-0000 metadata block. "
        f"Governance reads these fields; a DIP without them cannot be tracked.")


def test_dip_0000_status_is_a_recognised_value():
    """An unrecognised status silently ranks as unknown in dip_conformance and
    would let a DIP claim anything at all."""
    known = {"draft", "proposed", "accepted", "implemented", "superseded",
             "deprecated", "rejected", "withdrawn"}
    bad = {}
    for f in sorted(DIPS.glob("DIP-*.md")):
        m = re.search(r"^\|\s*\*\*Status\*\*\s*\|\s*(\S+)", f.read_text(errors="replace"), re.M)
        if m and m.group(1).strip().lower().rstrip("—-") not in known:
            bad[f.name] = m.group(1)
    assert not bad, f"unrecognised Status values: {bad}"


# --- DIP-0022: Module Specification -----------------------------------------
# Normative: a module manifest declares name, version and description. These
# are what the module table, `datacore.modules.list` and the loader all read;
# a module missing one is invisible or unversioned.

MODULE_MANIFESTS = sorted(MODULES.glob("*/module.yaml"))


@pytest.mark.parametrize(
    "manifest", MODULE_MANIFESTS, ids=lambda p: p.parent.name)
def test_dip_0022_module_manifest_declares_its_identity(manifest):
    m = yaml.safe_load(manifest.read_text()) or {}
    for field in ("name", "version", "description"):
        assert m.get(field), (
            f"{manifest.parent.name}/module.yaml has no `{field}`. DIP-0022 "
            f"makes it required; the loader and module table both read it.")


@pytest.mark.parametrize(
    "manifest", MODULE_MANIFESTS, ids=lambda p: p.parent.name)
def test_dip_0022_declared_name_matches_its_directory(manifest):
    """A manifest whose name disagrees with its directory is addressable by two
    different strings, which is how a registry entry and a loader end up
    pointing at different things."""
    m = yaml.safe_load(manifest.read_text()) or {}
    assert m.get("name") == manifest.parent.name, (
        f"module.yaml says name={m.get('name')!r} but lives in "
        f"{manifest.parent.name}/")


@pytest.mark.parametrize(
    "manifest", MODULE_MANIFESTS, ids=lambda p: p.parent.name)
def test_dip_0022_declared_hook_files_exist(manifest):
    """A hook naming a file that is not there contributes nothing and says so
    nowhere — the /today briefing silently loses that module's section."""
    m = yaml.safe_load(manifest.read_text()) or {}
    hooks = m.get("hooks") or {}
    missing = []
    for hook_name, h in hooks.items():
        path = h.get("path") or h.get("file") if isinstance(h, dict) else (
            h if isinstance(h, str) and h.endswith(".md") else None)
        if path and not (manifest.parent / path).exists():
            missing.append(f"{hook_name} -> {path}")
    assert not missing, (
        f"{manifest.parent.name} declares hook files that do not exist: {missing}")


# --- DIP-0002: Layered Context Pattern --------------------------------------
# Normative: context composes .base.md -> .space.md -> .local.md, and the
# composed .md is generated, never hand-edited. The generated file says so in
# its own header; if it were tracked, an edit to it would be silently lost on
# the next rebuild.

def test_dip_0002_composed_context_is_generated_and_untracked():
    import subprocess
    composed = ROOT / "CLAUDE.md"
    assert composed.exists(), "CLAUDE.md has not been composed"
    head = composed.read_text(errors="replace")[:300]
    assert "AUTO-GENERATED" in head, (
        "CLAUDE.md lacks the generated-file header DIP-0002 requires; without "
        "it a reader cannot tell an edit here will be lost")
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--error-unmatch",
                        "CLAUDE.md"], capture_output=True)
    assert r.returncode != 0, (
        "CLAUDE.md is tracked. It is generated from CLAUDE.base.md; tracking it "
        "means two copies of one fact, and the edit to the wrong one is lost.")


def test_dip_0002_base_layer_exists_for_every_composed_context():
    """A composed file with no base layer cannot be regenerated — the source of
    truth would be the output."""
    for composed in [ROOT / "CLAUDE.md"] + list(ROOT.glob("[0-9]-*/CLAUDE.md")):
        if not composed.exists():
            continue
        # Only files that ARE composed need a base layer. A hand-written
        # CLAUDE.md (0-personal's, for one) is a source, not an output, and
        # demanding a base for it would be a false alarm — the failure mode
        # this suite exists to prevent.
        if "AUTO-GENERATED" not in composed.read_text(errors="replace")[:300]:
            continue
        base = composed.with_name("CLAUDE.base.md")
        assert base.exists(), (
            f"{composed.relative_to(ROOT)} declares itself generated but has no "
            f"CLAUDE.base.md — it cannot be regenerated, so the output is the "
            f"only source")
