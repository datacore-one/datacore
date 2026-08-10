"""Tests for the evaluator personas-as-data roster (Datacore v2 Phase 7,
Task 7.2).

Twenty-two evaluator-* agent definitions (.datacore/modules/nightshift/
agents/evaluator-*.md — gitignored, tracked only via their registry
entries under module_agents:) get consolidated into one parameterized
agent plus a data roster: .datacore/registry/evaluators.yaml.

This suite is read-only against the real repo registry/roster/def files —
no fixtures, no tmp_path mutation. It verifies:
  - the roster parses and has the documented shape
  - it covers a sane minimum of personas (>=15)
  - every persona entry has non-empty key/name/focus
  - the roster's key count matches the known persona count (dict keys are
    unique by construction, so this is the practical stand-in for a
    "keys unique" assertion)
  - the consolidated agent definition file exists and mentions the
    roster path (so a reader lands on the data, not another hardcoded
    persona list)

POST-APPLY REALITY (Task 7.3): Task 7.3 has already run registry_gc's
real `--apply` against `.datacore/registry/agents.yaml` — the 22
evaluator-* entries this roster describes are no longer live under
`module_agents:`; they were correctly archived into
`.datacore/registry/archive/agents-deprecated.yaml`'s own
`module_agents:` key (preserving `status: deprecated` and every other
field, only `source:` repointed to the archived def-file location).
`TestRosterMatchesRegistry` reflects that: it checks presence/count
against the ARCHIVE, and asserts the live registry now carries ZERO
evaluator-* entries, not >=22 live ones.

RELOCATION (final-review wave, Fix 1): the archived def files themselves
have moved again. They used to live at `.datacore/agents/_deprecated/` —
which, because `.claude` is a plain symlink to `.datacore` in this
installation, put all 34 archived agent defs (not just these 22
evaluators) INSIDE the harness-visible `.claude/agents/` tree, so every
one of them still loaded in every session despite being "archived". They
now live at `.datacore/4-archive/agents/` — outside `.claude/agents/`
entirely, mirroring this repo's existing `{space}/4-archive/` convention
for retired content (DIP-0016's `paths.archive` mapping; `.datacore/`
already had a sibling `.datacore/4-archive/learning/`). Every archived
`source:` field was repointed accordingly; `TestArchiveRelocation` below
pins both halves of that invariant so it can't silently regress.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
ROSTER_PATH = REPO_ROOT / ".datacore" / "registry" / "evaluators.yaml"
AGENTS_REGISTRY_PATH = REPO_ROOT / ".datacore" / "registry" / "agents.yaml"
ARCHIVE_PATH = REPO_ROOT / ".datacore" / "registry" / "archive" / "agents-deprecated.yaml"
CONSOLIDATED_DEF_PATH = REPO_ROOT / ".datacore" / "agents" / "evaluator.md"

# Final-review wave (Fix 1): the archived def files' home directory.
# OLD_DEPRECATED_DIR sat inside `.claude/agents/` (via the `.claude` ->
# `.datacore` symlink) and must now be permanently empty of `*.md` files.
# ARCHIVED_AGENTS_DIR is where they live now, outside the harness-visible
# tree.
OLD_DEPRECATED_DIR = REPO_ROOT / ".datacore" / "agents" / "_deprecated"
ARCHIVED_AGENTS_DIR = REPO_ROOT / ".datacore" / "4-archive" / "agents"

MIN_PERSONAS = 15
# Actual count found in the registry at distillation time (Task 7.2) — the
# brief's "20 expected" was a placeholder; the real module_agents section
# carried 22 evaluator-* entries (now archived — see POST-APPLY REALITY
# above). Kept as an explicit constant (rather than re-deriving it from
# the roster itself) so a future accidental drop of an entry fails loudly
# instead of silently redefining "expected".
EXPECTED_PERSONA_COUNT = 22


@pytest.fixture(scope="module")
def roster():
    assert ROSTER_PATH.exists(), f"roster not found at {ROSTER_PATH}"
    with open(ROSTER_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


@pytest.fixture(scope="module")
def agents_registry():
    assert AGENTS_REGISTRY_PATH.exists(), f"registry not found at {AGENTS_REGISTRY_PATH}"
    with open(AGENTS_REGISTRY_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


@pytest.fixture(scope="module")
def archive():
    assert ARCHIVE_PATH.exists(), (
        f"archive not found at {ARCHIVE_PATH} — expected post Task 7.3's apply run"
    )
    with open(ARCHIVE_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


class TestRosterShape:
    def test_roster_parses(self, roster):
        assert isinstance(roster, dict)

    def test_has_version(self, roster):
        assert roster.get("version") == 1

    def test_has_evaluators_mapping(self, roster):
        assert isinstance(roster.get("evaluators"), dict)

    def test_at_least_min_personas(self, roster):
        evaluators = roster["evaluators"]
        assert len(evaluators) >= MIN_PERSONAS

    def test_persona_count_matches_expected(self, roster):
        """Dict keys are unique by construction — the practical way to
        catch an accidental duplicate-key collapse (which would silently
        reduce the count) is to pin the expected count, not merely assert
        uniqueness of what's left."""
        evaluators = roster["evaluators"]
        assert len(evaluators) == EXPECTED_PERSONA_COUNT


class TestPersonaFields:
    def test_every_persona_has_nonempty_key(self, roster):
        for key in roster["evaluators"]:
            assert isinstance(key, str) and key.strip(), f"empty/invalid key: {key!r}"

    def test_every_persona_has_nonempty_name(self, roster):
        for key, entry in roster["evaluators"].items():
            name = entry.get("name")
            assert isinstance(name, str) and name.strip(), f"{key}: missing/empty name"

    def test_every_persona_has_nonempty_focus(self, roster):
        for key, entry in roster["evaluators"].items():
            focus = entry.get("focus")
            assert isinstance(focus, str) and focus.strip(), f"{key}: missing/empty focus"

    def test_every_persona_has_domains_list(self, roster):
        for key, entry in roster["evaluators"].items():
            assert isinstance(entry.get("domains"), list), f"{key}: domains not a list"

    def test_every_persona_has_triggers_list(self, roster):
        for key, entry in roster["evaluators"].items():
            assert isinstance(entry.get("triggers"), list), f"{key}: triggers not a list"

    def test_every_persona_has_core_bool(self, roster):
        for key, entry in roster["evaluators"].items():
            assert isinstance(entry.get("core"), bool), f"{key}: core not a bool"

    def test_at_least_one_core_persona(self, roster):
        core_keys = [k for k, e in roster["evaluators"].items() if e.get("core")]
        assert core_keys, "expected at least one core evaluator (always-runs)"

    def test_at_least_one_domain_persona(self, roster):
        domain_keys = [k for k, e in roster["evaluators"].items() if not e.get("core")]
        assert domain_keys, "expected at least one domain evaluator"


class TestRosterMatchesRegistry:
    """Cross-check the roster against the real registry + archive — every
    persona must trace back to a deprecated evaluator-<key> entry.

    POST-APPLY REALITY: Task 7.3 already ran registry_gc's real --apply,
    which archives every deprecated entry it finds. That means the 22
    evaluator-* entries are no longer live under `agents.yaml`'s
    `module_agents:` — they were correctly moved into
    `archive/agents-deprecated.yaml`'s own `module_agents:` key. The two
    tests below assert exactly that split: ZERO live, all 22 archived.
    This is the CORRECT post-apply state, not a regression — do not
    "fix" these assertions back to checking the live registry."""

    def _module_agents(self, agents_registry):
        return agents_registry.get("module_agents") or {}

    def test_every_roster_key_has_archived_entry(self, roster, archive):
        """Every roster key must trace back to an evaluator-<key> entry
        now living under archive/agents-deprecated.yaml's module_agents:
        section (moved there by Task 7.3's real --apply run)."""
        archived_module_agents = self._module_agents(archive)
        missing = [
            key
            for key in roster["evaluators"]
            if f"evaluator-{key}" not in archived_module_agents
        ]
        assert not missing, f"roster keys with no matching archived entry: {missing}"

    def test_every_matching_registry_entry_is_deprecated(self, roster, agents_registry):
        module_agents = self._module_agents(agents_registry)
        not_deprecated = []
        for key in roster["evaluators"]:
            entry = module_agents.get(f"evaluator-{key}")
            if entry is None:
                continue  # covered by test_every_roster_key_has_archived_entry
            status = str((entry or {}).get("status", "")).strip().lower()
            if status != "deprecated":
                not_deprecated.append(key)
        assert not not_deprecated, (
            f"registry entries not marked status: deprecated: {not_deprecated}"
        )

    def test_live_registry_has_zero_evaluator_entries_after_archival(self, agents_registry):
        """Post-apply invariant: the live registry's module_agents: no
        longer carries ANY evaluator-* entries — they were all archived
        by Task 7.3's real --apply run. (Was
        test_registry_still_has_exactly_the_expected_evaluator_entries,
        which asserted == EXPECTED_PERSONA_COUNT live entries — that was
        the correct PRE-apply invariant; this is the correct POST-apply
        one.)"""
        module_agents = self._module_agents(agents_registry)
        evaluator_entries = [k for k in module_agents if k.startswith("evaluator-")]
        assert evaluator_entries == []

    def test_archive_has_exactly_the_expected_evaluator_entries(self, archive):
        """The archive's module_agents: section carries exactly the
        expected number of evaluator-* entries — none dropped, none
        duplicated by the archival move."""
        archived_module_agents = self._module_agents(archive)
        evaluator_entries = [
            k for k in archived_module_agents if k.startswith("evaluator-")
        ]
        assert len(evaluator_entries) == EXPECTED_PERSONA_COUNT

    def test_every_archived_evaluator_entry_is_still_marked_deprecated(self, roster, archive):
        """The archival move must not have silently stripped the
        `status: deprecated` field the entries were archived FOR."""
        archived_module_agents = self._module_agents(archive)
        not_deprecated = []
        for key in roster["evaluators"]:
            entry = archived_module_agents.get(f"evaluator-{key}")
            if entry is None:
                continue  # covered by test_every_roster_key_has_archived_entry
            status = str((entry or {}).get("status", "")).strip().lower()
            if status != "deprecated":
                not_deprecated.append(key)
        assert not not_deprecated, (
            f"archived entries not marked status: deprecated: {not_deprecated}"
        )


class TestArchiveRelocation:
    """Final-review wave, Fix 1: the archived def files moved from
    `.datacore/agents/_deprecated/` (harness-visible via the `.claude` ->
    `.datacore` symlink) to `.datacore/4-archive/agents/`. Both halves of
    that invariant are pinned here so a future change can't silently
    regress it: the old location must stay empty, and every archived
    evaluator entry's `source:` must resolve to a real file at the new
    one."""

    def test_old_deprecated_dir_has_no_md_files_left(self):
        if not OLD_DEPRECATED_DIR.exists():
            return  # fully removed is fine too
        leftover = sorted(p.name for p in OLD_DEPRECATED_DIR.glob("*.md"))
        assert leftover == [], (
            f"agent def files still present under the old harness-visible "
            f"{OLD_DEPRECATED_DIR}: {leftover}"
        )

    def test_every_archived_evaluator_source_resolves_under_new_archive_dir(self, roster, archive):
        archived_module_agents = archive.get("module_agents") or {}
        bad = []
        for key in roster["evaluators"]:
            entry = archived_module_agents.get(f"evaluator-{key}")
            if entry is None:
                continue  # covered by test_every_roster_key_has_archived_entry
            source = str(entry.get("source", ""))
            resolved = (REPO_ROOT / source).resolve()
            if resolved.parent != ARCHIVED_AGENTS_DIR.resolve() or not resolved.exists():
                bad.append((f"evaluator-{key}", source))
        assert not bad, f"archived entries with a stale/broken source path: {bad}"


class TestConsolidatedDefinition:
    def test_consolidated_def_exists(self):
        assert CONSOLIDATED_DEF_PATH.exists(), (
            f"consolidated evaluator def not found at {CONSOLIDATED_DEF_PATH}"
        )

    def test_consolidated_def_mentions_roster_path(self):
        content = CONSOLIDATED_DEF_PATH.read_text(encoding="utf-8")
        assert "evaluators.yaml" in content

    def test_consolidated_def_has_frontmatter(self):
        content = CONSOLIDATED_DEF_PATH.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "name: evaluator" in content
