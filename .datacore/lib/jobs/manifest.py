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

_REL = Path(".datacore") / "registry" / "infrastructure.yaml"
_CODE_ROOT = Path(__file__).resolve().parents[3]


def roster_path() -> Path:
    """Where THIS installation's machine roster is, not where this code is.

    The roster is gitignored -- private network topology, deliberately never in
    the repo -- so it exists only in each host's data tree. Scheduled jobs run
    from ~/.datacore/v2-runner, a separate checkout that carries tracked files
    only. Resolving the roster next to `__file__` therefore found nothing in the
    one place the verifier actually runs, and both readers failed quietly to a
    default: known_machines() to shape-only validation, and awake.always_on()
    to "this machine never sleeps".

    That second one is why a laptop's hourly artifacts kept going "stale" every
    night after the sleep-aware fix shipped on 2026-09-16: it was verified from
    ~/Data, where the ignored file exists, and was never once active in the
    runner. Ten of the twelve roster readers already resolved from the data
    root; these two were the outliers.

    Order mirrors job_verify.py and actor_identity.py. An explicit
    $DATACORE_ROOT is authoritative -- a test pointing it at a scratch tree must
    not fall through to the real one. Unset, ~/Data (job_verify's own default),
    then the code's own tree, where code and data coincide.
    """
    import os
    root = os.environ.get("DATACORE_ROOT")
    if root:
        return Path(root) / _REL
    for base in (Path.home() / "Data", _CODE_ROOT):
        if (base / _REL).is_file():
            return base / _REL
    return Path.home() / "Data" / _REL


def known_machines(path: Path | None = None) -> frozenset[str] | None:
    """Absent installation config permits shape-only validation; invalid does not."""
    path = path or roster_path()
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, yaml.YAMLError) as error:
        raise ManifestError("cannot read a valid machine roster") from error
    if not isinstance(data, dict) or not isinstance(data.get("servers"), dict):
        raise ManifestError("machine roster must contain a servers mapping")
    names = set()
    for host, cfg in data["servers"].items():
        if not isinstance(host, str) or not _MACHINE_RE.fullmatch(host) or not isinstance(cfg, dict):
            raise ManifestError("invalid machine roster entry")
        names.add(host)
        alias = cfg.get("manifest_machine")
        if alias is not None:
            if not isinstance(alias, str) or not _MACHINE_RE.fullmatch(alias):
                raise ManifestError("invalid manifest_machine alias")
            names.add(alias)
    return frozenset(names)


# ── the roster as the one source of host names (INS-3) ───────────────────────
# Tools used to carry this fleet's host names as defaults (HOSTS = ('winston',
# ...), SSH_ALIAS = {"box": "winston"}), so a stranger's install ssh'd to our
# machines. They ask the roster instead. None of these raise: a missing or
# unreadable roster means "this install declares no hosts", and each caller
# says so in its own terms rather than guessing one of ours.

def _roster_doc(path: Path | None = None) -> dict:
    try:
        data = yaml.safe_load((path or roster_path()).read_text())
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _servers(path: Path | None = None) -> dict:
    servers = _roster_doc(path).get("servers")
    return {str(k): v for k, v in servers.items() if isinstance(v, dict)} if isinstance(servers, dict) else {}


def _reachable_alias(cfg: dict) -> str | None:
    alias = cfg.get("ssh_alias")
    return str(alias) if alias not in (None, "", "-") else None


def ssh_alias(machine: str, path: Path | None = None) -> str | None:
    """How ssh reaches a roster machine, named by roster key or manifest_machine.

    None for a machine the roster declares unreachable by ssh (ssh_alias null or
    '-', e.g. the workstation this runs on). A name the roster does not know is
    returned unchanged: it may already be an ssh alias.
    """
    for name, cfg in _servers(path).items():
        if machine in (name, cfg.get("manifest_machine")):
            return _reachable_alias(cfg)
    return machine


def ssh_hosts(path: Path | None = None) -> list[str]:
    """Every roster machine ssh can reach, by ssh alias, in roster order."""
    return [a for a in (_reachable_alias(c) for c in _servers(path).values()) if a]


def fleet_names(path: Path | None = None) -> list[str]:
    """Every name this install's own machines go by: roster keys, manifest
    names and ssh aliases. What an allow-list of 'our hosts' is built from."""
    out: list[str] = []
    for name, cfg in _servers(path).items():
        for n in (name, cfg.get("manifest_machine"), _reachable_alias(cfg)):
            if n and str(n) not in out:
                out.append(str(n))
    return out


def role_all(name: str, path: Path | None = None) -> list[str]:
    """`roles.<name>` in the roster: which machine(s) or actor this install
    gives a fleet-wide duty (always_on, sequencer, ...). Empty when unset."""
    roles = _roster_doc(path).get("roles")
    value = roles.get(name) if isinstance(roles, dict) else None
    if value is None or value == "":
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def role(name: str, path: Path | None = None) -> str | None:
    """The single machine or actor holding a roster role, or None."""
    found = role_all(name, path)
    return found[0] if found else None


def _cli(argv: list[str]) -> int:
    """For shell callers: `manifest.py ssh-alias MACHINE | ssh-hosts | role NAME
    | role-ssh NAME`. Prints one value per line; prints nothing when unset."""
    if argv[:1] == ["ssh-hosts"] and len(argv) == 1:
        out = ssh_hosts()
    elif argv[:1] == ["ssh-alias"] and len(argv) == 2:
        out = [ssh_alias(argv[1]) or ""]
    elif argv[:1] == ["role"] and len(argv) == 2:
        out = role_all(argv[1])
    elif argv[:1] == ["role-ssh"] and len(argv) == 2:
        out = [a for a in (ssh_alias(m) for m in role_all(argv[1])) if a]
    elif argv[:1] == ["jobs"] and len(argv) in (1, 2):
        # The effective job list, one `name<TAB>machine<TAB>cmd` per line: what
        # this install runs, tracked and local together.
        doc = effective_doc(Path(argv[1]) if len(argv) == 2 else OWN_TRACKED)
        out = [f"{j.get('name')}\t{j.get('machine')}\t{j.get('cmd')}"
               for j in doc.get("jobs") or [] if isinstance(j, dict)]
    else:
        print("usage: manifest.py ssh-hosts | ssh-alias MACHINE | role NAME | role-ssh NAME | jobs [MANIFEST]")
        return 2
    print("\n".join(o for o in out if o))
    return 0


CHECKS =frozenset({"exists", "nonempty", "json_has_keys", "regex", "last_line_regex",
                    "min_bytes", "no_crash"})
#: `command` pipes the alert to the install's own command (job_verify._alert_command):
#: mail, a webhook, anything -- alerts need not go to Telegram (INS-5).
ON_FAILS = frozenset({"log", "telegram", "command"})

#: What makes a VISITOR's duty happen (DIP-0046 §14/§15). A visitor carries no
#: clock: its duties run because a person opened the lid, so they are named by
#: the session event that fires them, never by an hour.
#:   wake      the join tick itself (visitor_join.py --tick notices a full wake)
#:   join      after every converged join -- on arrival and every few waking hours
#:   arrival   after the first converged join of a session only
#:   awake     a daemon or stream that runs only while the machine is awake
TRIGGERS = frozenset({"wake", "join", "arrival", "awake"})
#: An artifact judged in session time: it must have been written at or after
#: the last converged join (`join`) or the join that began this session
#: (`arrival`), as recorded in the visitor's join.json.
SINCE = frozenset({"join", "arrival"})

# checks that must NOT carry an `arg`
_NO_ARG_CHECKS = frozenset({"exists", "nonempty", "no_crash"})


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
    since: str | None = None


@dataclass
class Job:
    name: str
    machine: str
    schedule: str
    cmd: str
    artifacts: list[Artifact]
    required_env: list[str] = field(default_factory=list)
    on_fail: str = "log"
    require_synced_repos: list[str] = field(default_factory=list)
    #: False: a failure of this job is never handed to an agent; the operator
    #: hears directly. For the jobs that ARE the delegation machinery -- the
    #: escalation report, the canary, the drill -- delegating their own repair
    #: is circular: on 2026-09-22 box-autofix-escalation was delegated to
    #: Miles, dead-lettered after three attempts, and then listed itself.
    delegate: bool = True
    #: A visitor's session trigger (TRIGGERS); None on a resident.
    trigger: str | None = None


# ── the install's own jobs: manifest.local.yaml over the tracked list (INS-3) ──
# The tracked manifest.yaml ships jobs any fleet member of this shape may run,
# with no machine, agent, path or space of ours in them. The jobs that ARE ours
# -- named after our agents, on a machine only we have, writing into one of our
# spaces -- live in the gitignored manifest.local.yaml, which every reader sees
# laid over the tracked list: a local job replaces the tracked job of the same
# name, and a local-only job is added. One rule, applied here, so no reader can
# see a different job list from another.

LOCAL_NAME = "manifest.local.yaml"
#: The tracked list that ships with this code. Only for THIS file does the
#: install's local list also get looked up under the data root: a runner
#: checkout (~/.datacore/v2-runner) carries tracked files only, while the
#: install's own list sits in its data tree. A manifest anywhere else (a test's
#: scratch file) is overlaid only by a local list beside it.
OWN_TRACKED = Path(__file__).resolve().parent / "manifest.yaml"


def local_for(tracked: Path) -> Path | None:
    """The install's own job list that overlays `tracked`, or None."""
    tracked = Path(tracked)
    beside = tracked.with_name(LOCAL_NAME)
    if beside.is_file():
        return beside
    try:
        own = tracked.resolve() == OWN_TRACKED.resolve()
    except OSError:
        own = False
    if own:
        import os
        root = os.environ.get("DATACORE_ROOT")
        base = Path(root) if root else Path.home() / "Data"
        cand = base / ".datacore" / "lib" / "jobs" / LOCAL_NAME
        if cand.is_file():
            return cand
    return None


def overlay(base: dict | None, local: dict | None) -> dict:
    """`base` with `local`'s jobs laid over it: same name replaces, in place;
    local-only jobs follow, in the local file's order."""
    base = base if isinstance(base, dict) else {}
    local = local if isinstance(local, dict) else {}
    out = {**{k: v for k, v in local.items() if k != "jobs"}, **base}
    mine = [j for j in (local.get("jobs") or []) if isinstance(j, dict)]
    by_name = {j.get("name"): j for j in mine}
    merged = []
    for j in base.get("jobs") or []:
        name = j.get("name") if isinstance(j, dict) else None
        merged.append(by_name.pop(name) if name in by_name else j)
    merged += [j for j in mine if j.get("name") in by_name]
    out["jobs"] = merged
    return out


def lists_roster_machines(tracked: Path, path: Path | None = None) -> bool:
    """Does this install's roster declare a machine the tracked list schedules?
    Only then is the tracked list this install's to run (a stranger's install
    gets its own list alone, never another fleet's)."""
    try:
        roster = known_machines(path)
        if not roster:
            return False
        jobs = (yaml.safe_load(Path(tracked).read_text()) or {}).get("jobs") or []
        return any(isinstance(j, dict) and j.get("machine") in roster for j in jobs)
    except Exception:  # noqa: BLE001 - an unreadable roster or list is not membership
        return False


def _read(path: Path) -> dict:
    try:
        return yaml.safe_load(Path(path).read_text()) or {}
    except yaml.YAMLError as error:
        raise ManifestError(f"cannot parse {path}: {error}") from error


def effective_doc(path: Path) -> dict:
    """The parsed job list at `path` as this install runs it.

    `path` is the tracked list: the install's manifest.local.yaml (beside it, or
    under the data root for the code's own list) is laid over it.
    `path` is a manifest.local.yaml: the tracked list beside it goes underneath,
    when this install's roster declares a machine it schedules.
    OSError from reading `path` itself propagates, as it always did.
    """
    path = Path(path)
    doc = _read(path)
    if path.name == LOCAL_NAME:
        tracked = path.with_name("manifest.yaml")
        if tracked.is_file() and lists_roster_machines(tracked):
            return overlay(_read(tracked), doc)
        return doc
    local = local_for(path)
    return overlay(doc, _read(local)) if local else doc


def load_manifest(path: Path, *, roster_path: Path | None = None) -> list[Job]:
    """Load and validate a job manifest, returning its jobs.

    The document validated is `effective_doc(path)`: the install's own
    manifest.local.yaml laid over the tracked list.

    Raises `ManifestError` (carrying every problem found, one per line) if
    the manifest is missing required fields, uses an unrecognized
    machine/check/on_fail value, declares a job with no artifacts, or
    declares two jobs with the same name. Unknown top-level or per-job
    keys are ignored.
    """
    return validate_manifest(effective_doc(Path(path)), roster_path=roster_path)


def validate_manifest(data, *, roster_path: Path | None = None) -> list[Job]:
    """Validate the same parsed document the caller will execute."""
    machines = known_machines(roster_path)

    if not isinstance(data, dict):
        raise ManifestError(f"manifest root must be a mapping (got {type(data).__name__})")

    errors: list[str] = []

    if "version" not in data:
        errors.append("missing required 'version' field (must be 1)")
    elif type(data["version"]) is not int or data["version"] != 1:
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
        job = _build_job(raw_job, index, errors, seen_names, machines)
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


def _build_job(raw: object, index: int, errors: list[str], seen_names: set[str], machines: frozenset[str] | None) -> Job | None:
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
    _known = machines
    if machine is not None and _known is not None and machine not in _known:
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

    require_synced_repos = raw.get("require_synced_repos", [])
    if not isinstance(require_synced_repos, list) or not all(
        isinstance(x, str) for x in require_synced_repos
    ):
        errors.append(
            f"{ref}: field 'require_synced_repos' must be a list of strings "
            f"(got {require_synced_repos!r})"
        )

    delegate = raw.get("delegate", True)
    if not isinstance(delegate, bool):
        errors.append(f"{ref}: field 'delegate' must be true or false (got {delegate!r})")

    trigger = raw.get("trigger")
    if trigger is not None and trigger not in TRIGGERS:
        errors.append(f"{ref}: unknown trigger {trigger!r} "
                      f"(expected one of: {', '.join(sorted(TRIGGERS))})")

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
        require_synced_repos=list(require_synced_repos),
        delegate=delegate,
        trigger=trigger,
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
    elif check in ("regex", "last_line_regex"):
        if not isinstance(arg, str):
            errors.append(f"{ref}: check {check!r} requires a string 'arg' (got {arg!r})")

    since = raw.get("since")
    if since is not None and since not in SINCE:
        errors.append(f"{ref}: unknown since {since!r} (expected one of: {', '.join(sorted(SINCE))})")

    if len(errors) != start:
        return None

    return Artifact(path=path, check=check, max_age_hours=max_age_hours, arg=arg, since=since)


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
