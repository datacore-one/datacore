"""Conformance tests for DIP-0016 — the agent registry must match the disk.

Bug class 1: a fact duplicated across files with nothing binding the copies.
The registry and the agent files are two statements of the same fact, and
until now nothing compared them. On 2026-09-02 that had produced:

    8 agent files with proper frontmatter and no registry entry — including
      podcast-creator and research-orchestrator, which are called by name by
      research-orchestrator's own pipeline and were invisible to
      datacore_agent_list
    1 byte-identical duplicate of emergency-stop-trader.md, at the repo root
      and inside the trading module — the same agent twice, one registered

DIP-0016 is marked Implemented. Its discovery mechanism cannot work for an
agent it does not know about, so "implemented" and "working" had come apart in
exactly the way this class describes.

The rule is deliberately narrow so it cannot produce false alarms: a file only
has to be registered if it declares BOTH `name:` and `description:` in
frontmatter, which is what makes it an agent rather than a note that happens
to live in an agents/ directory (ROSTER.md, winston.md, landing-generator.md
are such notes and are correctly exempt).
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
REGISTRY = ROOT / ".datacore" / "registry" / "agents.yaml"


def _registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text()) or {}


def _entries() -> list[tuple[str, str, dict]]:
    """(section, name, entry) — NOT a name-keyed dict.

    `agents` and `module_agents` are separate namespaces and the same name
    legitimately appears in both: a module overlay inherits the core agent's
    behaviour and overrides its paths, so `podcast-creator` exists as a 12 KB
    core agent and a 1 KB module stub. Flattening the two into one dict lets
    the module entry overwrite the core one, after which the core FILE looks
    unregistered — which is what this test reported until it was fixed, and is
    the same name-collapsing error that briefly repointed the core registry
    entry at the stub.
    """
    reg, out = _registry(), []
    for section in ("agents", "module_agents"):
        for name, e in (reg.get(section) or {}).items():
            if isinstance(e, dict):
                out.append((section, name, e))
    return out


def _agent_files() -> list[pathlib.Path]:
    return sorted(
        list(ROOT.glob(".datacore/agents/*.md"))
        + list(ROOT.glob(".datacore/modules/*/agents/*.md")))


def _is_agent(p: pathlib.Path) -> bool:
    """Declares both name and description in frontmatter.

    A note living in an agents/ directory is not an agent, and failing one
    would be a false alarm of exactly the kind this suite exists to prevent.
    """
    t = p.read_text(errors="replace")
    if not t.startswith("---") or t.count("---") < 2:
        return False
    fm = t.split("---", 2)[1]
    return bool(re.search(r"^name:\s*\S", fm, re.M)
                and re.search(r"^description:\s*\S", fm, re.M))


def _deliberately_deregistered(p: pathlib.Path) -> bool:
    """Agent files that are unregistered ON PURPOSE.

    DIP-0040 (Agent Consolidation) replaced the evaluator-* family with a
    single parameterized `evaluator` agent selected from evaluators.yaml. The
    definition files remain on disk; their registry entries were removed
    deliberately, and test_registry_gc.py asserts that they stay removed.

    Registering one of them "to satisfy" this test re-broke that assertion —
    which is how this exception came to be written. Two tests disagreeing
    about the same fact is bug class 1, so the reason is recorded here rather
    than the rule being quietly loosened.
    """
    return p.stem.startswith("evaluator-")


def test_every_registry_source_resolves():
    """An entry pointing at a file that does not exist is undiscoverable and
    misleading — the registry claims a capability the system does not have."""
    broken = [(sec, n, e["source"]) for sec, n, e in _entries()
              if e.get("source") and not (ROOT / e["source"]).exists()]
    assert not broken, "registry entries with a missing source:\n" + "\n".join(
        f"  {sec}.{n}: {s}" for sec, n, s in broken)


def test_every_agent_file_is_registered():
    """The direction that actually drifted: files nothing knows about."""
    declared = {str(pathlib.Path(e["source"])) for _, _, e in _entries()
                if e.get("source")}
    unregistered = [p for p in _agent_files()
                    if _is_agent(p) and str(p.relative_to(ROOT)) not in declared
                    and not _deliberately_deregistered(p)]
    assert not unregistered, (
        "agent files with no registry entry — datacore_agent_list cannot see "
        "these:\n" + "\n".join(f"  {p.relative_to(ROOT)}" for p in unregistered))


def test_no_agent_is_defined_twice():
    """Two copies of one agent is bug class 1 in its purest form: an edit to
    one silently leaves the other stale, and only one of them is registered."""
    import hashlib
    by_digest: dict[str, list[str]] = {}
    for p in _agent_files():
        if not _is_agent(p):
            continue
        d = hashlib.sha256(p.read_bytes()).hexdigest()
        by_digest.setdefault(d, []).append(str(p.relative_to(ROOT)))
    dupes = {d: ps for d, ps in by_digest.items() if len(ps) > 1}
    assert not dupes, "byte-identical agent files:\n" + "\n".join(
        "  " + " == ".join(ps) for ps in dupes.values())


@pytest.mark.parametrize("section,name,entry", _entries(),
                         ids=[f"{s}.{n}" for s, n, _ in _entries()])
def test_registry_entry_has_the_fields_discovery_needs(section, name, entry):
    """A description is what makes an agent findable by purpose rather than by
    remembering its name, which is DIP-0016's stated reason to exist."""
    assert entry.get("source"), f"{name} declares no source file"
    assert entry.get("description"), f"{name} has no description"
