#!/usr/bin/env python3
"""gen_claude_agents -- .claude artifact generator from the agent registry.

Datacore v2 Phase 8, Task 8.2. Harness artifacts under `.claude/agents/`
become GENERATED outputs of the registry, never hand-authored -- the
anti-lock-in one-way rule: `.datacore/registry/agents.yaml` (DIP-0016) is
the single source of truth; `.claude/agents/*.md` is a build artifact
regenerated from it, not edited directly. This module produces exactly
those build artifacts; it never reads or writes the real `.claude/`
directory itself -- callers decide `out_dir` (task 8.3 wires the real
`.claude/agents/` path once its existence/gitignore status is confirmed).

Registry shape consumed (identical to registry_gc.py's documented shape,
see that module's docstring for the full DIP-0016 background): a mapping
keyed by agent name under two possible top-level sections, `agents:` and
`module_agents:`, each entry carrying at least `description` and `source`.

Two-function design:
  generate(registry_path, out_dir, sections=("agents",)) -> GenReport
      Writes `<out_dir>/<name>.md` for every ACTIVE entry (mutates).
  check(registry_path, out_dir, sections=("agents",)) -> CheckResult
      Regenerates to memory and diffs against `out_dir`'s current content
      (read-only -- never writes anything, including never creating
      out_dir if it doesn't exist).

Active/deprecated: per this task's brief, an entry is skipped (and counted
in `skipped_deprecated`) iff its `status` field is exactly `deprecated`
(case-insensitive, whitespace-tolerant). This is DELIBERATELY narrower than
registry_gc.py's `_is_deprecated`, which also treats a "[DEPRECATED]"
marker in the entry's name/description as deprecated -- that broader
marker-based classification is registry_gc's job, run at GC time, and its
`apply()` physically removes those entries from the registry. By the time
this generator runs (post-GC), any entry still present without
`status: deprecated` is trusted to be ready to ship; this generator does
not re-derive GC's classification, it just honors the one explicit field
a caller could still set between GC runs.

Output shape, per active entry, written to `<out_dir>/<name>.md`:
  line 1:   GENERATED_HEADER (a fixed string naming the canonical registry
            path, `.datacore/registry/agents.yaml` -- NOT necessarily the
            literal `registry_path` argument passed in, since tests and
            other callers may point at a tmp/alternate copy of the same
            canonical file; the header always names the canonical path a
            real deployment's `.claude/agents/*.md` would be regenerated
            from).
  then:     a blank line, a YAML frontmatter block (`---` / name+description
            / `---`), rendered via `yaml.safe_dump` rather than manual
            string interpolation specifically so descriptions containing
            quotes, colons, or embedded newlines still produce valid,
            round-trippable YAML (see TestFrontmatterYamlSafety in the test
            suite).
  then:     a blank line and one body line pointing at the registry's
            `source:` path for that entry.

Determinism: entries are collected and written in sorted-by-name order,
both within one section and across sections when multiple are requested
(`GenReport.written` is always alphabetically sorted). Given an unchanged
registry, `generate()` is idempotent -- re-running produces byte-identical
files (verified by TestIdempotent), since the render is a pure function of
the entry's own fields with no timestamps/randomness involved.

Cross-section collisions: if the SAME name is active in more than one of
the requested `sections` (e.g. both `agents:` and `module_agents:` register
"foo"), `<out_dir>/foo.md` could not be written without silently preferring
one section's entry over the other with no signal that happened. Both
`generate()` and `check()` raise `GenError` naming the entry and every
section it was found active in, before writing/comparing anything.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple, Union

import yaml

PathLike = Union[str, Path]

# The canonical registry path named in every generated file's header line,
# regardless of the (possibly tmp/alternate) `registry_path` a given call
# actually reads from -- see module docstring.
REGISTRY_CANONICAL_PATH = ".datacore/registry/agents.yaml"
GENERATED_HEADER = f"# GENERATED from {REGISTRY_CANONICAL_PATH} — do not edit"

DEFAULT_SECTIONS: Tuple[str, ...] = ("agents",)


class GenError(RuntimeError):
    """Raised by generate()/check() when a name is active in more than one
    of the requested `sections` -- see module docstring. Raised before any
    file is written (generate()) or compared (check())."""


@dataclass
class GenReport:
    written: List[str] = field(default_factory=list)
    skipped_deprecated: int = 0


@dataclass
class CheckResult:
    clean: bool
    missing: List[str] = field(default_factory=list)  # rendered, not on disk
    extra: List[str] = field(default_factory=list)  # on disk, not rendered
    drifted: List[str] = field(default_factory=list)  # on disk + rendered, content differs
    skipped_deprecated: int = 0

    def drift_names(self) -> List[str]:
        """All names with any drift (missing/extra/content-changed), sorted
        and deduplicated -- the identifier list `--check` reports/exits on."""
        return sorted(set(self.missing) | set(self.extra) | set(self.drifted))


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _is_deprecated(entry: dict) -> bool:
    return str((entry or {}).get("status", "")).strip().lower() == "deprecated"


def _render(name: str, entry: dict) -> str:
    """Render one active entry's full `<out_dir>/<name>.md` content."""
    description = (entry or {}).get("description", "")
    source = (entry or {}).get("source", "")
    frontmatter = yaml.safe_dump(
        {"name": name, "description": description},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip("\n")
    lines = [
        GENERATED_HEADER,
        "",
        "---",
        frontmatter,
        "---",
        "",
        f"See registry source: `{source}`",
    ]
    return "\n".join(lines) + "\n"


def _collect_active(
    registry_path: Path, sections: Sequence[str]
) -> Tuple[Dict[str, str], int]:
    """Read `registry_path`, render every active entry across `sections`
    (in sorted-name order within each section), and return
    (name -> rendered content, skipped_deprecated count).

    Raises GenError on a name active in more than one requested section.
    """
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}

    name_sections: Dict[str, List[str]] = {}
    rendered: Dict[str, str] = {}
    skipped_deprecated = 0

    for section in sections:
        entries = data.get(section) or {}
        for name in sorted(entries.keys()):
            entry = entries[name] or {}
            if _is_deprecated(entry):
                skipped_deprecated += 1
                continue
            name_sections.setdefault(name, []).append(section)
            rendered[name] = _render(name, entry)

    collided = {n: secs for n, secs in name_sections.items() if len(secs) > 1}
    if collided:
        parts = [
            f"'{name}' is active in sections: {', '.join(secs)}"
            for name, secs in sorted(collided.items())
        ]
        raise GenError(
            "name collision across requested sections -- cannot generate "
            "(ambiguous which section's entry should win): " + "; ".join(parts)
        )

    return rendered, skipped_deprecated


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def generate(
    registry_path: PathLike,
    out_dir: PathLike,
    sections: Sequence[str] = DEFAULT_SECTIONS,
) -> GenReport:
    """Write `<out_dir>/<name>.md` for every active entry across `sections`.

    Deterministic (sorted-name order), idempotent (byte-stable regen given
    an unchanged registry), creates `out_dir` (with parents) if missing.
    Raises GenError on a cross-section name collision -- before writing
    anything.
    """
    registry_path = Path(registry_path)
    out_dir = Path(out_dir)

    rendered, skipped_deprecated = _collect_active(registry_path, sections)

    out_dir.mkdir(parents=True, exist_ok=True)

    written: List[str] = []
    for name in sorted(rendered):
        filename = f"{name}.md"
        (out_dir / filename).write_text(rendered[name], encoding="utf-8")
        written.append(filename)

    return GenReport(written=written, skipped_deprecated=skipped_deprecated)


def check(
    registry_path: PathLike,
    out_dir: PathLike,
    sections: Sequence[str] = DEFAULT_SECTIONS,
) -> CheckResult:
    """Regenerate to memory and diff against `out_dir`'s current content.
    Read-only: never writes, never creates `out_dir`. Raises GenError on a
    cross-section name collision, same as generate()."""
    registry_path = Path(registry_path)
    out_dir = Path(out_dir)

    rendered, skipped_deprecated = _collect_active(registry_path, sections)

    existing: Dict[str, Path] = {}
    if out_dir.exists():
        for f in out_dir.glob("*.md"):
            existing[f.stem] = f

    rendered_names = set(rendered)
    existing_names = set(existing)

    missing = sorted(rendered_names - existing_names)
    extra = sorted(existing_names - rendered_names)
    drifted = sorted(
        name
        for name in rendered_names & existing_names
        if existing[name].read_text(encoding="utf-8") != rendered[name]
    )

    clean = not (missing or extra or drifted)
    return CheckResult(
        clean=clean,
        missing=missing,
        extra=extra,
        drifted=drifted,
        skipped_deprecated=skipped_deprecated,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _sections_tuple(raw: str) -> Tuple[str, ...]:
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="gen_claude_agents",
        description="Generate .claude/agents/*.md from the DIP-0016 agent registry.",
    )
    parser.add_argument(
        "--registry",
        required=True,
        type=Path,
        help="Path to the registry yaml (e.g. .datacore/registry/agents.yaml).",
    )
    parser.add_argument(
        "--out",
        required=True,
        dest="out_dir",
        type=Path,
        help="Directory generated agent .md files are written into.",
    )
    parser.add_argument(
        "--sections",
        default="agents",
        help="Comma-separated registry sections to generate from "
        "(default: agents). E.g. --sections agents,module_agents",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report only: diff regenerated output against --out; "
        "exit 1 on any drift (missing/extra/content-changed).",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    sections = _sections_tuple(args.sections)

    try:
        if args.check:
            result = check(args.registry, args.out_dir, sections=sections)
            if result.clean:
                print(f"clean (skipped_deprecated={result.skipped_deprecated})")
                return 0
            drift_names = result.drift_names()
            print(f"DRIFT ({len(drift_names)}): {', '.join(drift_names)}")
            print(f"  missing: {', '.join(result.missing) or '-'}")
            print(f"  extra: {', '.join(result.extra) or '-'}")
            print(f"  content-changed: {', '.join(result.drifted) or '-'}")
            return 1

        report = generate(args.registry, args.out_dir, sections=sections)
        print(f"written ({len(report.written)}): {', '.join(report.written) or '-'}")
        print(f"skipped_deprecated: {report.skipped_deprecated}")
        return 0
    except GenError as exc:
        print(f"[gen] ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
