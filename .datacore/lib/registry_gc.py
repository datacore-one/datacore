#!/usr/bin/env python3
"""registry_gc — lifecycle enforcement engine for the agent registry.

Datacore v2 Phase 7, Task 7.1 (+ two post-review hardening passes). The
registry (.datacore/registry/agents.yaml, DIP-0016 schema) carries agent
metadata under TWO top-level sections: `agents:` (core, built-in agents)
and `module_agents:` (agents contributed by installed modules — e.g. the
22 `evaluator-*` personas under `module_agents:` distilled by Task 7.2).
Both sections use the identical per-entry shape: a mapping keyed by agent
name, each entry carrying description/version/source/skills/triggers/etc.
The registry accumulates deprecated-but-listed agents, entries whose
`source:` def file has been deleted or moved, and stray `*.bak*` files
next to it. This makes cleanup mechanical instead of manual.

Two-phase design:
  audit(registry_path, agents_dirs)  -> GcReport   (read-only, never writes)
  apply(report, registry_path, archive_dir) -> list[str]  (mutates)

AGENT_SECTIONS = ("agents", "module_agents") — both scanned identically.
Every per-entry identifier surfaced anywhere (GcReport.deprecated,
GcReport.orphaned_entries, duplicate_keys' nested entries, and apply()'s
action-log strings) is a "<section>/<name>" string, e.g.
"module_agents/evaluator-critic" or "agents/youtube-transcriber" — never a
bare name — so a report line is unambiguous about which section an entry
lives in. This matters because Task 7.3 consumes these lists directly:
an un-prefixed "evaluator-critic" would be ambiguous (and, before this
extension, invisible — see the Controller Scope Amendment note below).

Classification (binding, see task-7.1-brief.md):
  deprecated      entry matches ANY of three accepted spellings, checked
                  per-section (see RULINGS.md R4, .datacore/dip-review/
                  inspection/RULINGS.md — amendment pass adjudicating
                  DIP-0040 vs DIP-0016/DIP-0021):

                    1. `deprecated: true` — CANONICAL. Established by
                       DIP-0021 (search-research-architecture.md,
                       "Agent Deprecation" / "DIP-0016 Schema Extension"
                       sections) together with `superseded_by: <agent>`
                       naming the replacement. This is a DIP-0016 schema
                       extension, not a separate mechanism. New
                       deprecations MUST use this form.
                    2. `status: deprecated` — accepted legacy alias (the
                       "v2" spelling this tool originally shipped with).
                       Tooling MUST continue to recognise it.
                    3. `[DEPRECATED]` marker in name or description —
                       accepted legacy alias, pre-dating both of the
                       above. Also still recognised.

                  All three classify identically; `_is_deprecated` checks
                  them in that order. `superseded_by`, when present, is
                  provenance (which agent replaced this one) and is
                  preserved verbatim into the archived entry by apply()
                  step 1 below — it is never a classification input, only
                  a payload field carried through.
  orphaned        entry is not deprecated, but its `source:` file does not
                  exist on disk (including entries with no `source:` at
                  all). Checked per-section.
  unregistered    a `*.md` file under one of `agents_dirs` that no entry
                  in EITHER section's `source:` points at. REPORT ONLY —
                  registering an agent needs semantic judgement (skills,
                  triggers, spawns), which is agent-registry-auditor's
                  job, not this mechanical pass. Never auto-fixed by
                  apply().
  duplicate_keys  (a) top-level (column-0) YAML keys that appear more
                  than once in registry_path's raw text — e.g. two
                  `agents:` blocks from a bad merge — reported bare (e.g.
                  "agents"); AND (b) second-level (entry-name) keys
                  duplicated WITHIN one section's block — e.g. two
                  `evaluator-critic:` entries under the same
                  `module_agents:` block — reported as "<section>/<name>".
                  PyYAML's loader silently keeps only the LAST occurrence
                  of any duplicate mapping key with no error, at EITHER
                  level, which can silently drop half a registry (or half
                  a section). audit() only reports this (populates the
                  field); apply() additionally treats it as a hard
                  pre-flight abort — see below.
  bak_files       `*.bak*` files sitting in the registry directory.
  active_count    entries (across BOTH sections) that are neither
                  deprecated nor orphaned.

`GcReport.has_actionable()` is true iff deprecated/orphaned/bak/
duplicate_keys carry anything — unregistered_files alone never counts
(matches its REPORT-ONLY framing above). duplicate_keys is included
deliberately: an operator running `--check` before ever attempting
`--apply` should see the corruption up front, not just discover it as an
abort message when `--apply` is actually invoked.

Controller Scope Amendment (Task 7.2 structural find, adjudicated): the
original implementation only ever read the top-level `agents:` key. The
real registry's `module_agents:` section — where all 22 newly-deprecated
`evaluator-*` entries from Task 7.2's persona consolidation live — was
completely invisible: audit() against the real registry reported exactly
1 deprecated entry (`agents/youtube-transcriber`, a pre-existing,
correctly-classified deprecation that happens to live in `agents:`), with
all 22 `module_agents/evaluator-*` deprecations silently unreported. Left
unfixed, Task 7.3's real `--apply` run would have silently no-op'd on
precisely the entries it was meant to archive. Verified and fixed in this
pass — see task-7.1-report.md for the exact before/after audit output
against the real registry.

apply() actions, in crash-safe order (identical across both sections):
  0. Pre-flight: re-scan registry_path's raw text for duplicate keys (both
     top-level and per-section nested), UNCONDITIONALLY, before anything
     else (including before checking whether the report has anything
     actionable at all). If any are found, raise DuplicateKeyError naming
     them — no file is opened for writing before this check runs.
  1. Deprecated entries: for each one (in either section), its `source:`
     def file is moved into `archive_dir` (preserving its filename WHERE
     POSSIBLE — see the collision guard below) UNLESS that resolved
     source path is also referenced by a surviving entry (one not being
     archived/removed this run, in EITHER section) — in that case the
     file is deliberately LEFT IN PLACE and a "[gc] WARNING shared source
     retained: ..." line is added to the action log instead of a move.
     The entry's metadata (with `source:` repointed to the new location,
     when actually moved) is staged into an in-memory merge of
     `<registry_dir>/archive/agents-deprecated.yaml`'s existing section
     (its OWN section — an `agents:` entry archives under the archive
     file's `agents:` key, a `module_agents:` entry under the archive
     file's `module_agents:` key. The section split is preserved end to
     end, never flattened together).

     Archive destination collision guard (Task 7.3 production incident,
     recovered manually, fixed here): a move NEVER silently overwrites
     whatever already sits at `archive_dir / <basename>` — two different
     agents' def files can legitimately share a basename (different
     source directories, e.g. two modules each naming their def file the
     same way), or a genuinely unrelated file may simply already be
     archived under that name. If the destination already exists, a
     non-colliding sibling name is derived by appending `-2`, `-3`, ...
     before the extension (see `_non_colliding_path`), the entry's
     `source:` is repointed at whichever name was actually used, and a
     "[gc] WARNING archive destination collision: ..." line names the
     chosen destination in the action log.
  2. Orphaned entries: staged for removal from the live registry (from
     their own section); a plain-text comment line (never YAML data,
     naming the full "<section>/<name>" id) is queued to record what was
     removed and when.
  3. The archive file (all staged def-file moves from step 1, THEN the
     merged agents-deprecated.yaml content covering both steps 1 and 2,
     for BOTH sections) is written to disk FIRST, atomically (tmp-file +
     os.replace) — BEFORE the live registry is rewritten to drop those
     entries. This is the crash-safety boundary: a crash between step 1's
     file move(s) and this write can at worst leave a def file already
     moved but not yet reflected in agents-deprecated.yaml (self-healing —
     see the `dest_abs.exists()` handling below); a crash after this write
     but before step 4 leaves the entry present in BOTH the archive and
     the live registry (harmless, idempotent — a re-run just re-archives
     it). It is never possible to drop an entry from the live registry
     without its data already being durable in the archive.
  4. Only then: the live registry is rewritten — deprecated + orphaned
     names popped from their respective section's mapping (only sections
     that already existed in the document are ever written back; a
     section absent from the original file is never invented), written
     atomically, with its original leading comment/blank header block
     re-prepended verbatim (see header-preservation note below).
  5. `.bak` files: deleted.
  6. Unregistered files: left untouched, always.
  Idempotency: if a freshly-computed report has nothing actionable,
  apply() returns `[]` immediately and does not open, read-for-writing, or
  write ANY file. Since a real apply run removes exactly what the next
  audit() would otherwise find again, running --apply twice in a row
  touches nothing the second time and leaves the tree byte-identical.

Shared-source collision guard: two registry entries — in the SAME section
or across the two different sections — can legitimately (or accidentally)
point `source:` at the same file. If one is deprecated and the other
survives, physically moving the file into `archive_dir` would break the
survivor. apply() computes the set of resolved source paths still
referenced by surviving entries (scanning BOTH sections) BEFORE processing
any deprecated entry, and skips the physical move (archiving only the
registry metadata) whenever a deprecated entry's source collides with a
survivor's, regardless of which section the survivor is in.

Gitignored-source guard (final-review wave): before physically moving a
deprecated entry's def file, apply() checks whether that source path is
itself git-ignored in the repo rooted at `base_dir` (`git -C <base_dir>
check-ignore -q <source>`). A gitignored source (e.g. a module def file
living under a directory the repo's own `.gitignore` excludes) must NOT be
moved into `archive_dir` — `archive_dir` is a TRACKED location, and moving
an ignored file into it would either silently vanish the file from disk
tracking expectations or require force-adding it against the repo's own
stated intent, neither of which this mechanical GC pass should ever decide
on its own. Instead, the entry is archived as metadata ONLY (exactly like
the shared-source collision case) and a "[gc] WARNING gitignored source
retained: <path> (archive entry created, file left in place)" line is
added to the action log — `entry["source"]` is left pointing at the
original (unmoved) location. Determining "ignored" is best-effort: if git
isn't installed, `base_dir` isn't inside a git working tree, or the check
otherwise fails to run, the path is treated as NOT ignored (apply() falls
back to its normal archival behavior rather than silently skipping a move
it has no actual reason to skip) — this keeps every existing tmp_path
fixture (none of which are git repos) behaving exactly as before.

YAML round-trip caveat: this project's declared dependency is plain PyYAML
(see requirements.txt) — ruamel.yaml (which preserves comments/formatting
losslessly) is not assumed available. Both registry.yaml and
agents-deprecated.yaml are re-dumped with a plain `yaml.safe_load` /
`yaml.dump(sort_keys=False)` round-trip whenever they must be rewritten.
Top-level key order and per-entry key order are preserved (dict insertion
order survives the round-trip), and entries that aren't touched keep
identical *values*. Two mitigations limit the formatting-normalization
blast radius: (1) each file's leading comment/blank header block is
captured verbatim on read and re-prepended verbatim on write (so a
DIP-0016 documentation header at the top of a real registry survives),
and (2) registry.yaml is only rewritten at all when there is something in
deprecated/orphaned (in either section) to actually remove — a bak-only,
unregistered-only, or shared-source-collision-only apply never opens it
for writing, so its comments/formatting stay 100% intact in that case.
Comments that live *inside* the document (e.g. between profile blocks or
between the two agent sections, not in the leading header) are NOT
preserved — only the single leading block is.

Duplicate-key detection is a deliberately simple, non-YAML-aware raw line
scan (per the review's own framing: "manual line scan for top-level keys
or custom loader") — a line is treated as a top-level key if it has no
leading whitespace, isn't a comment, and contains a `:`. Nested (per-
section entry-name) duplicates are found the same way, scoped to each
section's own line range and restricted to the shallowest indentation
level found inside it (the entry-name level, not each entry's own inner
fields, which sit one or more levels deeper). This is accurate for
well-formed block-style YAML documents (which is what this registry
always is) but is not a general YAML parser.

Atomic writes: every write to registry.yaml or agents-deprecated.yaml goes
through a temp-file-in-the-same-directory + os.replace, so a process crash
during the write can never leave a half-written file in the real path —
either the old content is still there (if the crash was before
os.replace), or the new content is fully there (os.replace is atomic on
the same filesystem), never a truncated/partial mix of both. The target's
existing file mode is preserved across the swap (mkstemp always creates
0600; an existing target's real mode is copied onto the temp file before
the replace, so a rewrite of a normal 0644 tracked file doesn't silently
downgrade it).
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

DEPRECATED_MARKER = "[DEPRECATED]"
ARCHIVE_YAML_NAME = "agents-deprecated.yaml"

# The two top-level sections of a DIP-0016 registry that carry agent
# entries with identical per-entry shape. `agents:` holds core, built-in
# agents; `module_agents:` holds agents contributed by installed modules
# (e.g. the 22 evaluator-* personas from Task 7.2). Both are scanned,
# classified, and mutated identically — see module docstring.
AGENT_SECTIONS: Tuple[str, ...] = ("agents", "module_agents")


class DuplicateKeyError(RuntimeError):
    """Raised by apply() when registry_path has duplicate keys — either
    top-level (e.g. two `agents:` blocks) or nested within one section
    (e.g. two `evaluator-critic:` entries under `module_agents:`). See
    _scan_duplicate_keys. Raised before ANY file is opened for writing —
    apply() performs no mutation in this case."""


@dataclass
class GcReport:
    deprecated: List[str] = field(default_factory=list)
    orphaned_entries: List[str] = field(default_factory=list)
    unregistered_files: List[str] = field(default_factory=list)
    bak_files: List[str] = field(default_factory=list)
    duplicate_keys: List[str] = field(default_factory=list)
    active_count: int = 0

    def has_actionable(self) -> bool:
        """True iff deprecated/orphaned/bak/duplicate_keys carry anything.
        Drives --check's exit code — unregistered_files alone does NOT
        count as actionable (it's report-only, never auto-fixed).
        duplicate_keys DOES count: it's a structural corruption risk an
        operator should see before ever attempting --apply, not just
        discover as an abort message when --apply is invoked."""
        return bool(
            self.deprecated
            or self.orphaned_entries
            or self.bak_files
            or self.duplicate_keys
        )


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _base_dir(registry_path: Path) -> Path:
    """Repo root that `source:` fields are relative to.

    The registry lives at <root>/.datacore/registry/agents.yaml, so the
    root is three parents up from the file — mirrors registry_validate.py's
    DATA_DIR convention. Fixtures mirror this same shape.
    """
    return registry_path.resolve().parents[2]


def _entry_id(section: str, name: str) -> str:
    return f"{section}/{name}"


def _split_entry_id(entry_id: str) -> Tuple[str, str]:
    section, _, name = entry_id.partition("/")
    return section, name


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` via tmp-file + os.replace in the same
    directory, so a same-filesystem rename is atomic: a crash before
    os.replace leaves the original file untouched; os.replace itself is
    atomic, so readers never see a partially-written file.

    Mode preservation: `tempfile.mkstemp` always creates its temp file
    0600 (owner read/write only), regardless of the target's existing
    permissions. Replacing an existing, more permissive file (e.g. 0644,
    the norm for a tracked registry.yaml) with that temp file would
    silently downgrade it to 0600 on every apply() run. If `path` already
    exists, its current mode is copied onto the temp file before
    os.replace, so the replace is a pure content swap — permissions
    untouched. If `path` doesn't exist yet (e.g. a first-time
    agents-deprecated.yaml), there's nothing to preserve, so mkstemp's
    0600 default is left as-is (a reasonable default for a freshly
    created file, not a downgrade of anything).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        if path.exists():
            os.chmod(tmp_path, stat.S_IMODE(path.stat().st_mode))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(str(tmp_path), str(path))
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _split_header(text: str) -> Tuple[List[str], str]:
    """Split raw file text into (leading comment/blank header lines, rest
    of the text). Plain YAML doesn't preserve comments through a
    load/dump cycle, so the leading block is kept verbatim as text and
    re-prepended on write instead of being round-tripped through the
    parser."""
    lines = text.splitlines()
    header: List[str] = []
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            header.append(line)
            body_start = i + 1
            continue
        break
    while header and header[-1].strip() == "":
        header.pop()
    body_text = "\n".join(lines[body_start:])
    return header, body_text


def _read_yaml_with_header(path: Path) -> Tuple[List[str], dict]:
    """Read `path`, returning (header_lines, parsed_data)."""
    text = path.read_text(encoding="utf-8")
    header, body_text = _split_header(text)
    data = yaml.safe_load(body_text) if body_text.strip() else {}
    return header, (data or {})


def _write_yaml_with_header(path: Path, header_lines: List[str], data: dict) -> None:
    """Write `data` to `path` (atomically), re-prepending `header_lines`
    verbatim before the freshly-dumped YAML body."""
    parts = []
    if header_lines:
        parts.append("\n".join(header_lines))
        parts.append("")
    dumped = yaml.dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100000,
    ).rstrip("\n")
    parts.append(dumped)
    _atomic_write_text(path, "\n".join(parts) + "\n")


def _rel(path: Path, base_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(base_dir))
    except ValueError:
        return str(path)


def _non_colliding_path(dest_abs: Path) -> Path:
    """If `dest_abs` already exists, derive a non-colliding sibling path by
    appending -2, -3, ... before the extension, trying each in turn until
    one is free. If `dest_abs` doesn't exist, it is returned unchanged.

    Production incident this guards against (Task 7.3's real --apply run,
    recovered manually): the archive def-file move used the destination
    basename only — `gtd-research-processor-module`'s def file collided
    with, and silently overwrote via shutil.move, an unrelated,
    pre-existing archived file that happened to share the same basename.
    Two different agents' def files can legitimately end up with the same
    filename (different source directories, e.g. two different modules
    each naming their def file the same way) — an archive move must NEVER
    silently destroy whatever is already sitting at the destination."""
    if not dest_abs.exists():
        return dest_abs
    stem, suffix = dest_abs.stem, dest_abs.suffix
    n = 2
    while True:
        candidate = dest_abs.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _is_gitignored(base_dir: Path, path: Path) -> bool:
    """True iff `path` is git-ignored in the repo rooted at (or above)
    `base_dir`. Best-effort: git not installed, `base_dir` not inside a
    git working tree, or any other failure to run the check is treated as
    "not ignored" — apply() falls back to its normal archival move rather
    than skipping one it has no actual reason to skip (see module
    docstring's gitignored-source guard note)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(base_dir), "check-ignore", "-q", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0


def _is_deprecated(name: str, entry: dict) -> bool:
    """True iff `entry` is deprecated under ANY of the three accepted
    spellings (DIP-0021 §"Agent Deprecation" / §"DIP-0016 Schema
    Extension", RULINGS.md R4 — canonical first, then the two accepted
    legacy aliases in the order they were introduced):

      1. `deprecated: true`  — CANONICAL (DIP-0021). Checked with `is True`
         (not truthiness) so an explicit `deprecated: false` — a real,
         active entry that simply states its non-deprecated status — is
         never misclassified; only the YAML boolean `true` counts.
      2. `status: deprecated` — v2 alias. Tooling MUST keep recognising it;
         new deprecations should use the canonical field instead.
      3. `[DEPRECATED]` marker in name or description — legacy alias,
         pre-dating both of the above. Also still recognised.

    All three are equivalent for classification purposes; which one an
    entry carries is provenance about when it was deprecated, not a
    distinction this function makes.
    """
    if entry.get("deprecated") is True:
        return True
    if str(entry.get("status", "")).strip().lower() == "deprecated":
        return True
    haystack = f"{name} {entry.get('description', '')}"
    return DEPRECATED_MARKER in haystack


def _source_path(base_dir: Path, entry: dict) -> Optional[Path]:
    src = entry.get("source")
    if not src:
        return None
    return base_dir / str(src)


def _scan_duplicate_top_level_keys(text: str) -> List[str]:
    """Manual line scan for duplicated top-level (column-0) YAML keys.

    PyYAML's SafeLoader silently keeps only the LAST occurrence of a
    duplicate mapping key with no error, which can silently drop half a
    merged registry (e.g. two `agents:` blocks from a bad merge). This is
    a structural pre-flight check, independent of parsing — deliberately
    simple (a raw line scan, not a YAML-aware duplicate-key detector), per
    the review's own framing. A line counts as a top-level key line iff it
    has no leading whitespace, is not blank/a comment, and contains ':'.
    """
    counts: Dict[str, int] = {}
    for line in text.splitlines():
        if not line or line[0] in (" ", "\t"):
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in ("---", "..."):
            continue
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip().strip('"').strip("'")
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return sorted(k for k, c in counts.items() if c > 1)


def _top_level_key_line_ranges(text: str) -> List[Tuple[str, int, int]]:
    """Return (key_name, start_line_idx, end_line_idx_exclusive) for every
    top-level (column-0) key line found, in document order. end_line_idx
    is the index of the NEXT top-level key line, or len(lines) for the
    last one — i.e. the half-open line range that key's block occupies."""
    lines = text.splitlines()
    positions: List[Tuple[str, int]] = []
    for i, line in enumerate(lines):
        if not line or line[0] in (" ", "\t"):
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped in ("---", "..."):
            continue
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip().strip('"').strip("'")
        if not key:
            continue
        positions.append((key, i))
    ranges = []
    for idx, (key, start) in enumerate(positions):
        end = positions[idx + 1][1] if idx + 1 < len(positions) else len(lines)
        ranges.append((key, start, end))
    return ranges


def _scan_duplicate_nested_keys(
    text: str, sections: Tuple[str, ...] = AGENT_SECTIONS
) -> List[str]:
    """Manual line scan for duplicated second-level (entry-name) keys
    WITHIN each of `sections`'s top-level block — e.g. two
    `evaluator-critic:` entries under the same `module_agents:` block from
    a bad merge. Returns a sorted list of "<section>/<name>" identifiers,
    matching the identifier convention used throughout GcReport.

    If a section's top-level key itself appears more than once (already
    caught separately by _scan_duplicate_top_level_keys), only its LAST
    occurrence's block is scanned here — mirroring PyYAML's own last-wins
    parsing semantics, so this check reports duplicates in the block that
    would actually end up being parsed.

    The "entry-name level" is identified as the SHALLOWEST indentation
    found among non-blank/non-comment lines in the block (each entry's own
    inner fields — description, source, skills, etc. — sit one or more
    levels deeper and are correctly excluded)."""
    lines = text.splitlines()
    ranges = _top_level_key_line_ranges(text)
    dupes: List[str] = []
    for section in sections:
        matches = [r for r in ranges if r[0] == section]
        if not matches:
            continue
        _, start, end = matches[-1]
        block_lines = lines[start + 1 : end]

        indents = []
        for line in block_lines:
            if not line.strip() or line.strip().startswith("#"):
                continue
            if line[0] in (" ", "\t"):
                indents.append(len(line) - len(line.lstrip(" \t")))
        if not indents:
            continue
        min_indent = min(indents)

        counts: Dict[str, int] = {}
        for line in block_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line[:1].isspace():
                continue
            indent = len(line) - len(line.lstrip(" \t"))
            if indent != min_indent:
                continue
            if ":" not in line:
                continue
            key = line.split(":", 1)[0].strip().strip('"').strip("'")
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1

        for name, count in counts.items():
            if count > 1:
                dupes.append(_entry_id(section, name))
    return sorted(dupes)


def _scan_duplicate_keys(
    text: str, sections: Tuple[str, ...] = AGENT_SECTIONS
) -> List[str]:
    """Combined pre-flight duplicate-key scan: top-level duplicates
    (bare key names, e.g. "agents") plus nested per-section entry-name
    duplicates (e.g. "module_agents/evaluator-critic")."""
    return sorted(
        _scan_duplicate_top_level_keys(text) + _scan_duplicate_nested_keys(text, sections)
    )


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def audit(registry_path: Path, agents_dirs: Optional[List[Path]] = None) -> GcReport:
    """Read-only classification pass over BOTH AGENT_SECTIONS. Never
    writes anything.

    registry_path: path to a DIP-0016-shaped agents.yaml.
    agents_dirs: directories to scan for `*.md` agent def files when
        computing unregistered_files. Pass [] (or None) to skip that check
        entirely — deprecated/orphaned/bak/duplicate_keys classification
        does not depend on it. (Task 7.3's real run derives these from the
        registry's own `source:` paths rather than a hardcoded glob list.)
    """
    registry_path = Path(registry_path)
    base_dir = _base_dir(registry_path)
    raw_text = registry_path.read_text(encoding="utf-8")
    duplicate_keys = _scan_duplicate_keys(raw_text)
    data = yaml.safe_load(raw_text) or {}

    deprecated: List[str] = []
    orphaned: List[str] = []
    active_count = 0
    registered_sources = set()

    for section in AGENT_SECTIONS:
        entries = data.get(section) or {}
        for name, raw_entry in entries.items():
            entry = raw_entry or {}
            entry_id = _entry_id(section, name)
            src_path = _source_path(base_dir, entry)
            if src_path is not None:
                try:
                    registered_sources.add(src_path.resolve())
                except OSError:
                    pass
            if _is_deprecated(name, entry):
                deprecated.append(entry_id)
            elif src_path is None or not src_path.exists():
                orphaned.append(entry_id)
            else:
                active_count += 1

    unregistered: List[str] = []
    for d in agents_dirs or []:
        d = Path(d)
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                resolved = f.resolve()
            except OSError:
                continue
            if resolved not in registered_sources:
                unregistered.append(_rel(f, base_dir))
    unregistered.sort()

    bak_files = sorted(_rel(p, base_dir) for p in registry_path.parent.glob("*.bak*"))

    return GcReport(
        deprecated=sorted(deprecated),
        orphaned_entries=sorted(orphaned),
        unregistered_files=unregistered,
        bak_files=bak_files,
        duplicate_keys=duplicate_keys,
        active_count=active_count,
    )


def apply(report: GcReport, registry_path: Path, archive_dir: Path) -> List[str]:
    """Mutate the registry + filesystem per `report`, across BOTH
    AGENT_SECTIONS. See module docstring for the crash-safe ordering
    guarantee, the shared-source collision guard, and the idempotency
    contract."""
    registry_path = Path(registry_path)
    archive_dir = Path(archive_dir)
    actions: List[str] = []

    # --- 0. pre-flight duplicate-key abort — unconditional, before any
    # other check, before any file is opened for writing -------------------
    raw_text = registry_path.read_text(encoding="utf-8")
    dup_keys = _scan_duplicate_keys(raw_text)
    if dup_keys:
        raise DuplicateKeyError(
            "registry has duplicate keys: "
            + ", ".join(dup_keys)
            + " — aborting apply(); no writes performed. Fix the registry "
            "by hand (or resolve the merge conflict) before re-running."
        )

    if not report.has_actionable():
        return actions

    base_dir = _base_dir(registry_path)
    registry_header, body_text = _split_header(raw_text)
    data = yaml.safe_load(body_text) if body_text.strip() else {}
    data = data or {}

    section_entries: Dict[str, dict] = {
        section: (data.get(section) or {}) for section in AGENT_SECTIONS
    }

    archive_yaml_path = registry_path.parent / "archive" / ARCHIVE_YAML_NAME
    archive_header: List[str] = []
    archived_by_section: Dict[str, dict] = {section: {} for section in AGENT_SECTIONS}
    if archive_yaml_path.exists():
        archive_header, archived_data = _read_yaml_with_header(archive_yaml_path)
        for section in AGENT_SECTIONS:
            archived_by_section[section] = archived_data.get(section) or {}

    to_remove_ids = set(report.deprecated) | set(report.orphaned_entries)
    to_remove_by_section: Dict[str, set] = {section: set() for section in AGENT_SECTIONS}
    for entry_id in to_remove_ids:
        section, name = _split_entry_id(entry_id)
        if section in to_remove_by_section:
            to_remove_by_section[section].add(name)

    # --- shared-source collision guard: resolve every surviving entry's
    # source, across BOTH sections, BEFORE touching any deprecated entry's
    # file -------------------------------------------------------------
    survivor_sources: Dict[Path, List[str]] = {}
    for section in AGENT_SECTIONS:
        for name, raw_entry in section_entries[section].items():
            if name in to_remove_by_section[section]:
                continue
            entry = raw_entry or {}
            src = entry.get("source")
            if not src:
                continue
            try:
                resolved = (base_dir / str(src)).resolve()
            except OSError:
                continue
            survivor_sources.setdefault(resolved, []).append(_entry_id(section, name))

    new_note_lines: List[str] = []

    # --- 1. deprecated -> archive (metadata + def file move) -------------
    for entry_id in report.deprecated:
        section, name = _split_entry_id(entry_id)
        entries = section_entries.get(section)
        if entries is None or name not in entries:
            continue
        entry = dict(entries[name] or {})
        src_rel = entry.get("source")
        if src_rel:
            src_abs = base_dir / str(src_rel)
            dest_abs = archive_dir / Path(str(src_rel)).name
            try:
                resolved_src = src_abs.resolve()
            except OSError:
                resolved_src = src_abs
            survivors = survivor_sources.get(resolved_src)
            if survivors:
                actions.append(
                    "[gc] WARNING shared source retained: "
                    f"{_rel(src_abs, base_dir)} (referenced by {', '.join(sorted(survivors))})"
                )
            elif src_abs.exists() and _is_gitignored(base_dir, src_abs):
                actions.append(
                    "[gc] WARNING gitignored source retained: "
                    f"{_rel(src_abs, base_dir)} (archive entry created, file left in place)"
                )
            elif src_abs.exists():
                archive_dir.mkdir(parents=True, exist_ok=True)
                final_dest_abs = _non_colliding_path(dest_abs)
                if final_dest_abs != dest_abs:
                    actions.append(
                        "[gc] WARNING archive destination collision: "
                        f"{_rel(dest_abs, base_dir)} already exists "
                        f"(unrelated file) — moved to {_rel(final_dest_abs, base_dir)} instead"
                    )
                shutil.move(str(src_abs), str(final_dest_abs))
                entry["source"] = _rel(final_dest_abs, base_dir)
                actions.append(f"moved def file: {src_rel} -> {entry['source']}")
            elif dest_abs.exists():
                # Already moved by a prior apply that crashed before writing
                # the archive yaml / rewriting the registry — just repoint
                # this entry at where the file already is.
                entry["source"] = _rel(dest_abs, base_dir)
        archived_by_section[section][name] = entry
        actions.append(f"archived deprecated entry '{entry_id}'")

    # --- 2. orphaned -> comment note (not data) ---------------------------
    for entry_id in report.orphaned_entries:
        section, name = _split_entry_id(entry_id)
        entries = section_entries.get(section)
        if entries is None or name not in entries:
            continue
        new_note_lines.append(
            f"# removed orphaned entry '{entry_id}': source file missing "
            f"(removed {date.today().isoformat()})"
        )
        actions.append(f"removed orphaned entry '{entry_id}' (source missing)")

    # --- 3. archive write happens BEFORE the live registry is rewritten ---
    if report.deprecated or new_note_lines:
        archive_data = {
            section: archived_by_section[section]
            for section in AGENT_SECTIONS
            if archived_by_section[section]
        }
        _write_yaml_with_header(
            archive_yaml_path, archive_header + new_note_lines, archive_data
        )

    # --- 4. now it's safe to drop the entries from the live registry -----
    if report.deprecated or report.orphaned_entries:
        for entry_id in list(report.deprecated) + list(report.orphaned_entries):
            section, name = _split_entry_id(entry_id)
            entries = section_entries.get(section)
            if entries is not None:
                entries.pop(name, None)
        for section in AGENT_SECTIONS:
            if section in data:
                data[section] = section_entries[section]
        _write_yaml_with_header(registry_path, registry_header, data)

    # --- 5. delete stray .bak files ---------------------------------------
    for bak in report.bak_files:
        bak_path = Path(bak)
        if not bak_path.is_absolute():
            bak_path = base_dir / bak_path
        if bak_path.exists():
            bak_path.unlink()
            actions.append(f"deleted {bak}")

    # --- 6. unregistered files: never touched -----------------------------

    return actions


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="registry_gc",
        description="Lifecycle enforcement for the agent registry (DIP-0016).",
    )
    parser.add_argument(
        "--registry",
        required=True,
        type=Path,
        help="Path to the registry yaml (e.g. .datacore/registry/agents.yaml).",
    )
    parser.add_argument(
        "--agents-dir",
        action="append",
        default=[],
        type=Path,
        help="Directory to scan for unregistered agent def files "
        "(repeatable). Omit to skip the unregistered-files check.",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
        help="Directory deprecated agent def files are moved into. "
        "Required together with --apply.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report only; exit 1 if deprecated/orphaned/.bak/duplicate-key "
        "entries exist.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply fixes: archive deprecated entries, drop orphaned ones, "
        "delete .bak files. Unregistered files are only ever reported.",
    )
    args = parser.parse_args(argv)
    if args.check and args.apply:
        parser.error(
            "--check and --apply cannot be used together — pick one: "
            "--check to report (exit code signals actionable findings), "
            "--apply to fix (always reports first, then applies)."
        )
    if args.apply and args.archive_dir is None:
        parser.error("--archive-dir is required together with --apply")
    return args


def _print_report(report: GcReport) -> None:
    print(f"active: {report.active_count}")
    print(f"deprecated ({len(report.deprecated)}): {', '.join(report.deprecated) or '-'}")
    print(
        f"orphaned ({len(report.orphaned_entries)}): "
        f"{', '.join(report.orphaned_entries) or '-'}"
    )
    print(f"bak files ({len(report.bak_files)}): {', '.join(report.bak_files) or '-'}")
    print(
        f"unregistered ({len(report.unregistered_files)}): "
        f"{', '.join(report.unregistered_files) or '-'}"
    )
    print(
        f"duplicate keys ({len(report.duplicate_keys)}): "
        f"{', '.join(report.duplicate_keys) or '-'}"
    )


def main(argv=None) -> int:
    args = _parse_args(argv)
    report = audit(args.registry, args.agents_dir)
    _print_report(report)

    if args.apply:
        try:
            actions = apply(report, args.registry, args.archive_dir)
        except DuplicateKeyError as exc:
            print(f"[gc] ERROR {exc}", file=sys.stderr)
            return 2
        for a in actions:
            print(a)
        return 0

    return 1 if report.has_actionable() else 0


if __name__ == "__main__":
    sys.exit(main())
