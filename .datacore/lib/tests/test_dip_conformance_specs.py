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


# --- DIP-0004, DIP-0006, DIP-0007: Superseded ------------------------------
# A superseded DIP's conformance is not that its own design was built — it is
# that the thing replacing it exists and is named. Without that, "Superseded"
# is a way to retire a DIP without saying what took over, and the trail ends.

SUPERSEDED = [f for f in sorted(DIPS.glob("DIP-*.md"))
              if re.search(r"^\|\s*\*\*Status\*\*\s*\|\s*Superseded",
                           f.read_text(errors="replace"), re.M | re.I)]


@pytest.mark.parametrize("dip_file", SUPERSEDED, ids=lambda p: p.stem[:12])
def test_superseded_dip_names_its_successor(dip_file):
    text = dip_file.read_text(errors="replace")
    m = re.search(r"^\|\s*\*\*Superseded By\*\*\s*\|\s*(.+?)\s*\|", text, re.M)
    assert m and m.group(1).strip(), (
        f"{dip_file.name} is Superseded but names no successor — the design it "
        f"replaced is gone and nothing says what replaced it")


@pytest.mark.parametrize("dip_file", SUPERSEDED, ids=lambda p: p.stem[:12])
def test_superseded_successor_dips_exist(dip_file):
    """If the successor is named as a DIP number, that DIP must be present."""
    text = dip_file.read_text(errors="replace")
    m = re.search(r"^\|\s*\*\*Superseded By\*\*\s*\|\s*(.+?)\s*\|", text, re.M)
    cited = re.findall(r"DIP-(\d{4})", m.group(1)) if m else []
    missing = [n for n in cited if not list(DIPS.glob(f"DIP-{n}-*.md"))]
    assert not missing, (
        f"{dip_file.name} points at DIP(s) {missing}, which do not exist — a "
        f"dangling supersession trail")


# --- Module DIPs: DIP-0011 nightshift, DIP-0012 crm, DIP-0013 meetings,
#     DIP-0020 whatsapp ------------------------------------------------------
# A DIP that specifies a module is conformant when that module is installed
# and identifies itself as the DIP says. These are the four whose subject is a
# single named module.

MODULE_DIPS = {"0011": "nightshift", "0012": "crm",
               "0013": "meetings", "0020": "whatsapp"}


@pytest.mark.parametrize("dip,module", sorted(MODULE_DIPS.items()))
def test_module_dip_subject_is_installed_and_identifies_itself(dip, module):
    manifest = MODULES / module / "module.yaml"
    assert manifest.exists(), (
        f"DIP-{dip} specifies the `{module}` module, which is not installed. "
        f"The DIP describes something that is not there.")
    m = yaml.safe_load(manifest.read_text()) or {}
    assert m.get("name") == module, (
        f"DIP-{dip}'s module declares name={m.get('name')!r}, not {module!r}")
    assert m.get("version"), f"{module} declares no version"


# --- DIP-0019: Learning Architecture ---------------------------------------
# Normative: each space keeps its learning in patterns.md, corrections.md and
# preferences.md under .datacore/learning/. The engram pipeline reads these by
# name; a space missing one silently contributes nothing from that category.

LEARNING_FILES = ("patterns.md", "corrections.md", "preferences.md")
# A space is a numbered directory with its OWN context file. `0-inbox` is a
# numbered drop directory that also happens to carry a .datacore/, and treating
# it as a space demanded learning files from a folder that holds one transcript.
SPACES = sorted(d for d in ROOT.glob("[0-9]-*")
                if d.is_dir() and (d / ".datacore").is_dir()
                and (d / "CLAUDE.md").is_file())


@pytest.mark.parametrize("space", SPACES, ids=lambda p: p.name)
def test_dip_0019_space_keeps_the_three_learning_files(space):
    learning = space / ".datacore" / "learning"
    assert learning.is_dir(), f"{space.name} has no .datacore/learning/"
    missing = [f for f in LEARNING_FILES if not (learning / f).exists()]
    assert not missing, (
        f"{space.name}/.datacore/learning/ is missing {missing}. The engram "
        f"pipeline reads these by name — an absent file contributes nothing "
        f"and reports nothing.")


# --- DIP-0001: Contribution Model ------------------------------------------
# Normative: the fork-and-overlay model needs an installable entry point and a
# catalogue of what can be installed. Without the catalogue a contributed
# module is unfindable, which defeats the model.

def test_dip_0001_install_entrypoint_and_catalogue_exist():
    for rel in ("INSTALL.md", ".datacore/CATALOG.md"):
        assert (ROOT / rel).exists(), (
            f"DIP-0001's contribution model requires {rel}; without it a "
            f"contributed module cannot be found or installed")


# --- DIP-0005: Installation & Upgrade --------------------------------------
# Normative: an install manifest and a lockfile. The lock records what is
# actually installed; a lockfile that will not parse cannot be reconciled
# against install.yaml, and upgrade has nothing to compare to.

def test_dip_0005_install_manifest_and_lockfile_parse():
    for rel in ("install.yaml", "datacore.lock.yaml"):
        p = ROOT / rel
        assert p.exists(), f"DIP-0005 requires {rel}"
        parsed = yaml.safe_load(p.read_text())
        assert isinstance(parsed, dict) and parsed, (
            f"{rel} does not parse to a mapping — upgrade cannot reconcile "
            f"against it")


# --- DIP-0017: Outbox & Archive Pattern ------------------------------------
# Normative: every space routes content out through 4-outbox/. A space without
# one has no exit path, so content either stays or is moved by hand.

@pytest.mark.parametrize("space", SPACES, ids=lambda p: p.name)
def test_dip_0017_every_space_has_an_outbox(space):
    outbox = space / "4-outbox"
    assert outbox.is_dir(), (
        f"{space.name} has no 4-outbox/. DIP-0017 makes it the single exit "
        f"path; without it content leaves by hand or not at all.")


# --- DIP-0029: Command-Scoped Engram Recall --------------------------------
# Normative: a module declares the scope its engrams are recalled under. A
# module with no declared scope gets no scoped recall — and the lookup returns
# nothing rather than failing, so the absence is silent.

@pytest.mark.parametrize(
    "manifest", MODULE_MANIFESTS, ids=lambda p: p.parent.name)
def test_dip_0029_module_declares_its_recall_scope(manifest):
    m = yaml.safe_load(manifest.read_text()) or {}
    recall = m.get("recall")
    assert recall, (
        f"{manifest.parent.name} declares no `recall:` scope. Its engrams are "
        f"never recalled for its own commands, and the lookup returns empty "
        f"rather than erroring — the failure is silent.")
    assert recall.get("scopes"), f"{manifest.parent.name} recall declares no scopes"


# --- DIP-0031: Agent Error Classification & Recovery -----------------------
# Normative: nightshift classifies why an execution failed, via failure-analyzer
# and the execution recorder. Without the recorder there is no per-execution
# record to classify, and a failed run is indistinguishable from one that did
# nothing.

def test_dip_0031_failure_classification_surface_exists():
    lib = MODULES / "nightshift" / "lib"
    for f in ("execute.py", "run.py", "execution_recorder.py"):
        assert (lib / f).exists(), (
            f"DIP-0031 names nightshift/lib/{f}; without it there is no "
            f"per-execution record to classify")
    agent = ROOT / ".datacore" / "agents" / "failure-analyzer.md"
    assert agent.exists(), "DIP-0031's failure-analyzer agent is missing"


def test_dip_0031_failure_analyzer_is_discoverable():
    """Classification that nothing can find is classification that never runs."""
    reg = yaml.safe_load(
        (ROOT / ".datacore" / "registry" / "agents.yaml").read_text()) or {}
    names = set(reg.get("agents") or {}) | set(reg.get("module_agents") or {})
    assert "failure-analyzer" in names, (
        "failure-analyzer is not in the agent registry, so DIP-0016 discovery "
        "cannot route to it")


# --- DIP-0036: Config Plane -------------------------------------------------
# Normative: one canonical env file per machine, read through config_plane
# rather than each caller inventing its own parser. The loader is PURE — it
# never touches os.environ — because merging with the process environment is
# the caller's decision, and a loader that silently mutated it would make
# precedence unknowable.

def test_dip_0036_config_plane_is_a_pure_loader():
    src = (ROOT / ".datacore" / "lib" / "config_plane.py")
    assert src.exists(), "DIP-0036's config_plane.py is missing"
    body = src.read_text(errors="replace")
    assert "CANONICAL_PATH" in body, "config_plane declares no canonical path"
    load = body[body.index("def load("):] if "def load(" in body else ""
    assert load, "config_plane exposes no load()"
    head = load[:load.index("\ndef ")] if "\ndef " in load else load
    # Look for USE, not mention. config_plane's own docstring says "it never
    # reads or writes `os.environ`", and a substring check on the source hits
    # that sentence — failing the module for documenting the property the test
    # is verifying.
    import ast as _ast
    tree = _ast.parse(head + "\n" if head.strip().endswith(":") else head)
    uses = [n for n in _ast.walk(tree)
            if isinstance(n, _ast.Attribute) and n.attr == "environ"]
    assert not uses, (
        "config_plane.load() touches os.environ. DIP-0036 makes it pure: the "
        "caller decides precedence, and a loader that mutates the environment "
        "makes that unknowable.")


# --- DIP-0037: Grounded Briefings ------------------------------------------
# Normative: a briefing is rendered from a fact table, so every claim in it
# traces to a fact that was gathered — rather than being generated prose that
# happens to look plausible.

def test_dip_0037_briefing_renders_from_a_fact_table():
    lib = ROOT / ".datacore" / "lib"
    for rel in ("briefing/fact_table.py", "briefing/render.py",
                "briefing_grounded.py"):
        assert (lib / rel).exists(), (
            f"DIP-0037 names {rel}; without it a briefing has no fact table to "
            f"be grounded in")


# --- DIP-0039: Server-First Artifacts --------------------------------------
# Normative: artifacts are produced on the server and synced, not produced
# locally and pushed — so a machine being asleep does not silently skip a day.

def test_dip_0039_artifact_sync_surface_exists():
    lib = ROOT / ".datacore" / "lib"
    for rel in ("v2_box_setup.sh", "artifact_sync.py"):
        assert (lib / rel).exists(), f"DIP-0039 names {rel}, which is absent"


# --- DIP-0041: Executor Adapters -------------------------------------------
# Normative: executors sit behind one adapter interface so a task can be run
# by claude-code, hermes or another runtime without the caller knowing which.

def test_dip_0041_executors_share_one_base_interface():
    ex = ROOT / ".datacore" / "lib" / "executors"
    assert (ex / "base.py").exists(), "DIP-0041's executor base class is missing"
    adapters = [p for p in ex.glob("*.py")
                if p.name not in ("__init__.py", "base.py")]
    assert adapters, "DIP-0041 defines an adapter interface with no adapters"
    base_body = (ex / "base.py").read_text(errors="replace")
    assert "class " in base_body, "executors/base.py defines no base class"


def test_dip_0041_generated_agent_definitions_have_a_generator():
    """gen_claude_agents.py turns the registry into runtime agent definitions.
    Without it the two would be maintained by hand — bug class 1."""
    assert (ROOT / ".datacore" / "lib" / "gen_claude_agents.py").exists(), (
        "DIP-0041 names gen_claude_agents.py; without it registry and runtime "
        "agent definitions are two hand-maintained copies of one fact")


# --- DIP-0003: Scaffolding Pattern -----------------------------------------
# Normative: scaffolding uses the SAME layered pattern as CLAUDE.md (DIP-0002)
# — a .base.md layer, optional .space.md overlay, composed output — and two
# agents maintain it.
#
# What these deliberately do NOT assert: that every space has authored its
# _core documents. Only 1-datafund has, and demanding purpose/vision/mission
# from nine spaces would be asserting content nobody has written. Conformance
# is that the PATTERN holds where scaffolding exists; authoring the documents
# is work, not conformance, and conflating the two produces a test that fails
# for the system being honest about a gap.

SCAFFOLDED = [s for s in SPACES if (s / "SCAFFOLDING.space.md").exists()
              or (s / "SCAFFOLDING.md").exists()]


@pytest.mark.parametrize("space", SCAFFOLDED, ids=lambda p: p.name)
def test_dip_0003_scaffolding_follows_the_layered_pattern(space):
    """An overlay or a composed file implies a base layer to compose from."""
    assert (space / "SCAFFOLDING.base.md").exists(), (
        f"{space.name} has a SCAFFOLDING overlay or composed file but no "
        f"SCAFFOLDING.base.md — there is nothing to regenerate it from, so the "
        f"output would be the only source (DIP-0002's failure mode)")


def test_dip_0003_scaffolding_agents_exist_and_are_discoverable():
    """The DIP names create-space and scaffolding-auditor. An agent that is not
    in the registry cannot be routed to, so the pattern has no maintainer."""
    reg = yaml.safe_load(
        (ROOT / ".datacore" / "registry" / "agents.yaml").read_text()) or {}
    names = set(reg.get("agents") or {}) | set(reg.get("module_agents") or {})
    for agent in ("create-space", "scaffolding-auditor"):
        assert (ROOT / ".datacore" / "agents" / f"{agent}.md").exists(), (
            f"DIP-0003 names the `{agent}` agent, which is absent")
        assert agent in names, (
            f"`{agent}` is not in the agent registry — DIP-0016 discovery "
            f"cannot route to it, so the scaffolding pattern has no maintainer")
