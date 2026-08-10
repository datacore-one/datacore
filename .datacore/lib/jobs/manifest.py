"""Job manifest schema + loader.

A manifest is a YAML document of the shape `{version: 1, jobs: [...]}`.
Each job describes a scheduled command on a given machine, plus the
artifacts (files) it is expected to produce -- used by the (later) unified
verifier to check that a job actually ran and produced what it claims.

`load_manifest` is strict about *shape* but tolerant about *extras*:
unknown top-level or per-job keys are silently ignored (forward
compatibility -- a newer manifest can add fields an older loader doesn't
know about yet), while wrong-typed or missing *required* keys are
collected into a single `ManifestError`. Validation never fails fast: the
whole document is checked, and if any problems were found they are all
raised together, one per line, so a bad manifest can be fixed in one pass
instead of a error-fix-rerun loop per field.

This module is pure data modeling: no I/O beyond reading the manifest
file itself, no clock, no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import re
import yaml

# Machine names are INSTALLATION CONFIGURATION, not part of this spec: they
# name hosts in the installation's own actor roster (DIP-0034, Per-writer
# files), which varies per installation. This was a hardcoded three-name enum
# until 2026-08-10, when adding contracts for a fourth host failed -- DIP-0035
# had already been corrected to say "a machine in the installation's roster"
# while the code still enforced the enum, so the spec described behaviour the
# implementation did not have. Validate the SHAPE, not the membership.
_MACHINE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_ROSTER_PATH = Path(__file__).resolve().parents[1].parent / "registry" / "infrastructure.yaml"


def known_machines() -> frozenset[str] | None:
    """Machine names the installation's roster declares, or None if absent.

    Shape validation alone is not enough: a typo silently creates a job for a
    host that does not exist, which can never pass and never alerts. When a
    roster is present it is authoritative; when it is not (a fresh install,
    a test fixture) validation falls back to shape only.
    """
    try:
        data = yaml.safe_load(_ROSTER_PATH.read_text())
        names = set()
        for host, cfg in (data.get("servers") or {}).items():
            names.add(host)
            if isinstance(cfg, dict) and cfg.get("manifest_machine"):
                names.add(cfg["manifest_machine"])
        return frozenset(names) or None
    except Exception:
        return None
CHECKS = frozenset({"exists", "nonempty", "json_has_keys", "regex", "min_bytes"})
ON_FAILS = frozenset({"log", "telegram"})

# checks that must NOT carry an `arg`
_NO_ARG_CHECKS = frozenset({"exists", "nonempty"})


class ManifestError(ValueError):
    """Raised by `load_manifest` when a manifest fails validation.

    The message lists every problem found in the manifest, one per line --
    never just the first one encountered.
    """


@dataclass
class Artifact:
    path: str
    check: str = "exists"
    max_age_hours: float | None = None
    arg: object = None


@dataclass
class Job:
    name: str
    machine: str
    schedule: str
    cmd: str
    artifacts: list[Artifact]
    required_env: list[str] = field(default_factory=list)
    on_fail: str = "log"


def load_manifest(path: Path) -> list[Job]:
    """Load and validate a job manifest, returning its jobs.

    Raises `ManifestError` (carrying every problem found, one per line) if
    the manifest is missing required fields, uses an unrecognized
    machine/check/on_fail value, declares a job with no artifacts, or
    declares two jobs with the same name. Unknown top-level or per-job
    keys are ignored.
    """
    path = Path(path)
    data = yaml.safe_load(path.read_text())

    if not isinstance(data, dict):
        raise ManifestError(f"manifest root must be a mapping (got {type(data).__name__})")

    errors: list[str] = []

    if "version" not in data:
        errors.append("missing required 'version' field (must be 1)")
    elif data["version"] != 1:
        errors.append(f"'version' must be 1 (got {data['version']!r})")

    if "jobs" not in data:
        errors.append("missing required 'jobs' field")
        raw_jobs: list = []
    elif not isinstance(data["jobs"], list):
        errors.append(f"'jobs' must be a list (got {type(data['jobs']).__name__})")
        raw_jobs = []
    else:
        raw_jobs = data["jobs"]

    seen_names: set[str] = set()
    jobs: list[Job] = []
    for index, raw_job in enumerate(raw_jobs):
        job = _build_job(raw_job, index, errors, seen_names)
        if job is not None:
            jobs.append(job)

    if errors:
        raise ManifestError("\n".join(errors))

    return jobs


def _job_ref(raw: dict, index: int) -> str:
    name = raw.get("name")
    if isinstance(name, str) and name:
        return f"job '{name}'"
    return f"job #{index}"


def _require_str(raw: dict, key: str, ref: str, errors: list[str]) -> str | None:
    if key not in raw:
        errors.append(f"{ref}: missing required field '{key}'")
        return None
    value = raw[key]
    if not isinstance(value, str) or not value:
        errors.append(f"{ref}: field '{key}' must be a non-empty string (got {value!r})")
        return None
    return value


def _build_job(raw: object, index: int, errors: list[str], seen_names: set[str]) -> Job | None:
    if not isinstance(raw, dict):
        errors.append(f"job #{index}: must be a mapping (got {type(raw).__name__})")
        return None

    ref = _job_ref(raw, index)
    start = len(errors)

    name = _require_str(raw, "name", ref, errors)
    if name is not None:
        if name in seen_names:
            errors.append(f"{ref}: duplicate job name {name!r}")
        else:
            seen_names.add(name)

    machine = _require_str(raw, "machine", ref, errors)
    _known = known_machines()
    if machine is not None and _known and machine not in _known:
        errors.append(
            f"{ref}: unknown machine {machine!r} "
            f"(not in the installation roster: {', '.join(sorted(_known))})"
        )
    elif machine is not None and not _MACHINE_RE.match(machine):
        errors.append(
            f"{ref}: unknown machine {machine!r} "
            f"(expected a lowercase host name from the installation's roster)"
        )

    schedule = _require_str(raw, "schedule", ref, errors)
    cmd = _require_str(raw, "cmd", ref, errors)

    artifacts = _build_artifacts(raw, ref, errors)

    required_env = raw.get("required_env", [])
    if not isinstance(required_env, list) or not all(isinstance(x, str) for x in required_env):
        errors.append(
            f"{ref}: field 'required_env' must be a list of strings (got {required_env!r})"
        )

    on_fail = raw.get("on_fail", "log")
    if not isinstance(on_fail, str) or on_fail not in ON_FAILS:
        errors.append(
            f"{ref}: unknown on_fail {on_fail!r} "
            f"(expected one of: {', '.join(sorted(ON_FAILS))})"
        )

    if len(errors) != start:
        return None

    return Job(
        name=name,
        machine=machine,
        schedule=schedule,
        cmd=cmd,
        artifacts=artifacts,
        required_env=list(required_env),
        on_fail=on_fail,
    )


def _build_artifacts(raw: dict, job_ref: str, errors: list[str]) -> list[Artifact] | None:
    if "artifacts" not in raw:
        errors.append(f"{job_ref}: missing required field 'artifacts'")
        return None

    raw_artifacts = raw["artifacts"]
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) == 0:
        errors.append(f"{job_ref}: must declare at least one artifact")
        return None

    start = len(errors)
    artifacts: list[Artifact] = []
    for index, raw_artifact in enumerate(raw_artifacts):
        artifact = _build_artifact(raw_artifact, job_ref, index, errors)
        if artifact is not None:
            artifacts.append(artifact)

    if len(errors) != start:
        return None
    return artifacts


def _build_artifact(raw: object, job_ref: str, index: int, errors: list[str]) -> Artifact | None:
    ref = f"{job_ref}, artifact #{index}"

    if not isinstance(raw, dict):
        errors.append(f"{ref}: must be a mapping (got {type(raw).__name__})")
        return None

    start = len(errors)

    path = _require_str(raw, "path", ref, errors)

    check = raw.get("check", "exists")
    if not isinstance(check, str) or check not in CHECKS:
        errors.append(
            f"{ref}: unknown check {check!r} (expected one of: {', '.join(sorted(CHECKS))})"
        )
        check = None

    max_age_hours = raw.get("max_age_hours")
    if max_age_hours is not None and (
        isinstance(max_age_hours, bool) or not isinstance(max_age_hours, (int, float))
    ):
        errors.append(f"{ref}: field 'max_age_hours' must be numeric (got {max_age_hours!r})")

    arg = raw.get("arg")
    if check in _NO_ARG_CHECKS:
        if arg is not None:
            errors.append(f"{ref}: check {check!r} must not have an 'arg' (got {arg!r})")
    elif check == "json_has_keys":
        if not isinstance(arg, list):
            errors.append(f"{ref}: check 'json_has_keys' requires a list 'arg' (got {arg!r})")
    elif check == "regex":
        if not isinstance(arg, str):
            errors.append(f"{ref}: check 'regex' requires a string 'arg' (got {arg!r})")

    if len(errors) != start:
        return None

    return Artifact(path=path, check=check, max_age_hours=max_age_hours, arg=arg)
