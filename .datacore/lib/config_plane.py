#!/usr/bin/env python3
"""config_plane.py -- canonical env-file loader (config plane, Task 3.1).

One env source per machine: `~/.datacore/datacore.env` (`CANONICAL_PATH`).
Everything else that needs process configuration reads it through this
module rather than inventing its own `.env` parser. The filename is
deliberately self-describing and namespaced under `.datacore/` rather than
a bare `env` -- a real-machine audit (Task 3.3 close) found `~/.datacore/env`
already occupied, on at least one machine, by a pre-existing *directory* of
per-service credential files (an older, different convention). `datacore.env`
sidesteps that specific collision; the non-file guard below (`load()`,
`check_permissions()`) additionally protects against ANY future path
collision, on any machine, defensively.

`load()` is PURE file parsing -- it never reads or writes `os.environ`.
Merging the returned dict with the process environment (and deciding
precedence) is the caller's job, not this module's.

Parse semantics:
    - Trailing newline is stripped from each line before parsing.
    - Blank or whitespace-only lines are skipped.
    - A line whose first non-whitespace character is `#` is a full-line
      comment and is skipped.
    - A line may have a leading `export ` (that exact prefix, after
      leading whitespace is stripped) -- tolerated and discarded before
      the key is parsed, e.g. `export FOO=bar`.
    - The line is split on the FIRST `=` only, so values may themselves
      contain `=` (e.g. `FOO=bar=baz` -> value `bar=baz`).
    - The key (everything before the first `=`) must match
      `[A-Za-z_][A-Za-z0-9_]*`; anything else makes the line malformed.
    - If the value both starts and ends with the same quote character
      (`'` or `"`) and is at least 2 characters long, that one outer
      layer of matching quotes is stripped. Mismatched quote types
      (`"bar'`) or a lone quote character are left untouched.
    - Inline comments are NOT stripped. A `#` appearing inside an
      unquoted value is kept as part of the value -- env files don't
      reliably support inline comments (is `#` a comment marker or a
      literal character in, say, a password?), so this module makes no
      guess and preserves the value byte-for-byte after the `=`.

Malformed lines (no `=`, or a key that fails the identifier pattern) are
never raised one at a time. `load()` collects every malformed line across
the whole file and raises a single `ConfigError` naming each one's line
number and reason -- so a caller sees the full picture in one pass rather
than fixing one line, rerunning, and hitting the next.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# `jobs` is a sibling package of this module (`.datacore/lib/jobs/`). Ensure
# it's importable regardless of the caller's cwd/sys.path -- this module
# itself lives in `.datacore/lib`, so its own directory is exactly the
# directory `jobs` hangs off of.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jobs.manifest import load_manifest  # noqa: E402

CANONICAL_PATH = Path.home() / ".datacore" / "datacore.env"

# Legacy, pre-config-plane env sources -- each one a candidate for
# migration into the canonical file. Paths are injectable via `doctor()`'s
# `legacy_sources` parameter (e.g. for tests); this dict is just the
# real-world default.
LEGACY_SOURCES: dict[str, Path] = {
    "cos.env": Path.home() / ".config" / "cos.env",
    "datacored.env": Path("/etc/datacored.env"),
    "hermes.env": Path.home() / ".hermes" / ".env",
}

# Default job manifest location, resolved relative to this file (not cwd
# or an env var) -- `doctor()` never reads `os.environ`.
DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent / "jobs" / "manifest.yaml"

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigError(ValueError):
    """Raised by `load()` when one or more lines are malformed.

    The message lists every malformed line found in the file (not just
    the first), each naming its 1-indexed line number and the reason
    (`no '=' found` or `invalid key '<key>'`).
    """


def _strip_quotes(value: str) -> str:
    """Strip one layer of matching surrounding quotes, if present.

    Only strips when both ends are the SAME quote character and the
    value is at least 2 characters long (so a lone `"` isn't treated as
    a pair). Mismatched quote types (`"bar'`) are left untouched.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load(path: Path | None = None) -> dict[str, str]:
    """Parse an env file into a dict. Never touches `os.environ`.

    Returns `{}` if the file does not exist -- a missing canonical env
    file is a normal, unconfigured state, not an error. Raises
    `ConfigError` if the path exists but is not a regular file (e.g. a
    directory sitting where a flat env file was expected -- exactly the
    real-machine collision Task 3.3's audit found) -- a symlink TO a
    regular file still passes, since `Path.is_file()` follows symlinks, so
    that convention keeps working. Also raises `ConfigError` (collecting
    every malformed line) if any line is neither blank, a comment, nor a
    valid `KEY=VALUE` line.
    """
    target = Path(path) if path is not None else CANONICAL_PATH

    if not target.exists():
        return {}

    if not target.is_file():
        raise ConfigError(f"canonical env path is not a regular file: {target}")

    result: dict[str, str] = {}
    errors: list[str] = []

    text = target.read_text()
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if not stripped:
            continue
        if stripped[0] == "#":
            continue

        if stripped.startswith("export "):
            stripped = stripped[len("export ") :]

        if "=" not in stripped:
            errors.append(f"line {lineno}: no '=' found: {raw_line!r}")
            continue

        key, _, value = stripped.partition("=")

        if not _KEY_RE.match(key):
            errors.append(f"line {lineno}: invalid key {key!r}")
            continue

        result[key] = _strip_quotes(value)

    if errors:
        raise ConfigError(
            f"{len(errors)} malformed line(s) in {target}:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )

    return result


def check_permissions(path: Path | None = None) -> list[str]:
    """Warn if the env file's permissions are looser than 0600.

    Returns a list of warning strings (empty when clean). A missing file
    is not a warning -- there's nothing to leak. A path that exists but is
    not a regular file (e.g. a directory) is reported as its own distinct
    warning rather than a permissions warning -- `stat()` on a directory
    would otherwise report seemingly-fine permission bits for something
    that was never a valid env file to begin with (the same real-machine
    collision `load()`'s guard closes). The permissions check itself is
    `(mode & 0o077) != 0`: any group or other bit set (read, write, or
    execute) trips a warning, since `~/.datacore/datacore.env` may hold
    secrets.
    """
    target = Path(path) if path is not None else CANONICAL_PATH

    if not target.exists():
        return []

    if not target.is_file():
        return [f"{target} is not a regular file (expected the canonical env file)"]

    mode = target.stat().st_mode & 0o777
    if mode & 0o077:
        return [f"{target} has loose permissions: {oct(mode)[2:].zfill(3)} (expected 600 or tighter)"]
    return []


@dataclass
class DoctorReport:
    """Result of `doctor()` -- the config-plane audit.

    SECRETS RULE (binding): every field here carries variable NAMES and
    source NAMES only -- never a value, never a value length, never a
    value prefix. `conflicts` entries are `(var, source, note)` where
    `note` is a fixed, non-value string (`"differs from canonical"`); the
    differing values themselves are compared internally in `doctor()` and
    then discarded, not reported.
    """

    missing: list[str]
    conflicts: list[tuple[str, str, str]]
    legacy_only: dict[str, list[str]]
    table: str


def _render_list_section(title: str, lines: list[str]) -> str:
    body = "\n".join(f"- {line}" for line in lines) if lines else "(none)"
    return f"## {title}\n\n{body}\n"


def _render_table(
    machine: str,
    canonical_path: Path,
    missing: list[str],
    conflicts: list[tuple[str, str, str]],
    legacy_only: dict[str, list[str]],
    unparseable: list[str],
) -> str:
    header = f"# Config Doctor -- machine: {machine}\nCanonical: {canonical_path}\n"

    missing_section = _render_list_section(
        "Missing (required by manifest jobs, absent from canonical)", missing
    )

    conflict_lines = [f"{var} ({source}): {note}" for var, source, note in conflicts]
    conflicts_section = _render_list_section(
        "Conflicts (legacy value differs from canonical)", conflict_lines
    )

    if legacy_only:
        legacy_body = "\n\n".join(
            f"### {source}\n" + "\n".join(f"- {var}" for var in variables)
            for source, variables in legacy_only.items()
        )
    else:
        legacy_body = "(none)"
    legacy_section = (
        "## Legacy-only (present in a legacy source, absent from canonical)\n\n"
        f"{legacy_body}\n"
    )

    unparseable_section = _render_list_section("Unparseable legacy sources", unparseable)

    return "\n".join(
        [header, missing_section, conflicts_section, legacy_section, unparseable_section]
    )


def doctor(
    machine: str,
    manifest_path: Path | None = None,
    canonical_path: Path | None = None,
    legacy_sources: dict[str, Path] | None = None,
) -> DoctorReport:
    """Audit the config plane for one machine: missing vars + legacy drift.

    Never reads `os.environ` -- everything is derived from the manifest
    (`jobs.manifest.load_manifest`) and the canonical/legacy env FILES via
    `load()`. Reuses `load()` for both canonical and legacy parsing, since
    legacy files are the same `KEY=VALUE` shape.

    - `missing`: union of `required_env` across `machine`'s jobs in the
      manifest, minus the canonical file's keys -- sorted.
    - `conflicts`: for each legacy source that is present and parses, the
      `(var, source, "differs from canonical")` tuples for every key
      present in BOTH that source and canonical whose values differ --
      sorted. Values themselves are never carried into the result.
    - `legacy_only`: for each legacy source that is present, parses, and
      has at least one such var, the sorted var names present there but
      absent from canonical.
    - `table`: human-readable markdown summary (missing / conflicts /
      legacy-only / unparseable sections, `(none)` when empty).

    A legacy source file that doesn't exist is skipped silently (nothing
    to migrate). A legacy source file that exists but fails to parse
    (`ConfigError`) is NOT a crash -- it's surfaced as a finding in
    `table` instead.

    `ManifestError` (invalid manifest) and `OSError` (missing manifest
    file) both propagate -- the manifest is validated infra and a missing
    manifest means doctor has nothing to audit against.
    """
    resolved_manifest_path = (
        Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    )
    resolved_canonical_path = Path(canonical_path) if canonical_path is not None else CANONICAL_PATH
    sources = legacy_sources if legacy_sources is not None else LEGACY_SOURCES

    jobs = load_manifest(resolved_manifest_path)  # ManifestError / OSError propagate
    canonical = load(resolved_canonical_path)

    required_env: set[str] = set()
    for job in jobs:
        if job.machine == machine:
            required_env.update(job.required_env)
    missing = sorted(required_env - canonical.keys())

    conflicts: list[tuple[str, str, str]] = []
    legacy_only: dict[str, list[str]] = {}
    unparseable: list[str] = []

    for source_name, source_path in sources.items():
        source_path = Path(source_path)
        if not source_path.exists():
            continue

        try:
            legacy_values = load(source_path)
        except ConfigError:
            unparseable.append(source_name)
            continue

        only_here = sorted(set(legacy_values) - set(canonical))
        if only_here:
            legacy_only[source_name] = only_here

        for key in sorted(set(legacy_values) & set(canonical)):
            if legacy_values[key] != canonical[key]:
                conflicts.append((key, source_name, "differs from canonical"))

    conflicts.sort()

    table = _render_table(
        machine, resolved_canonical_path, missing, conflicts, legacy_only, sorted(unparseable)
    )

    return DoctorReport(missing=missing, conflicts=conflicts, legacy_only=legacy_only, table=table)
