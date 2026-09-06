#!/usr/bin/env python3
"""Who is writing? One declared answer per machine, never an inference (DIP-0044).

Every ledger writer, sealer, verifier and adapter used to resolve its own
identity with `os.environ.get("DATACORE_ACTOR") or socket.gethostname()`,
copied into twenty-four files. The copies disagreed on case and on whether to
strip the domain, and every one of them fell back to the hostname, which is
how one laptop wrote under `mac`, `Mac`, `Mac.home` and `air-23.local`, and
how nightshift's events were filed under a hostname instead of the actor the
registry declares. This is the one resolver they all call.

Order, first hit wins:

  1. `DATACORE_ACTOR` in the environment (a service unit, a test).
  2. `~/.datacore/identity.env` (the machine's declaration, written once).
  3. The infrastructure registry: `servers.<name>.access.actor` where
     `access.hostname` or the server name equals this hostname. The hostname
     is a lookup key here, not the answer.
  4. Nothing. `this_actor()` then returns the lowercase short hostname and
     says so on stderr once, so an undeclared machine still writes (an event
     lost is worse than an event misfiled) but never silently. `strict=True`
     raises instead, for callers that must not guess.

`principals.yaml` binds writers to principals: which git identities may
append to a writer's log, which host it runs on, what it owns. `verify`
uses it to check that a log was only ever appended by its own principal.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
REGISTRY_DIR = LIB.parent / "registry"
INFRA = REGISTRY_DIR / "infrastructure.yaml"
PRINCIPALS = REGISTRY_DIR / "principals.yaml"
IDENTITY_FILE = Path(os.environ.get("DATACORE_IDENTITY_FILE", str(Path.home() / ".datacore" / "identity.env")))

_warned = False


class UndeclaredActor(RuntimeError):
    """No declaration for this machine: no env, no identity file, no registry row."""


def _parse_env_file(p: Path) -> dict:
    out: dict[str, str] = {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def short_hostname() -> str:
    return socket.gethostname().split(".")[0].lower()


def registry_actor(hostname: str | None = None, infra: Path = INFRA) -> str | None:
    """The actor the registry declares for this hostname, or None."""
    host = (hostname or short_hostname()).lower()
    try:
        import yaml
        reg = yaml.safe_load(infra.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — a registry problem must not stop a writer
        return None
    for name, cfg in (reg.get("servers") or {}).items():
        if not isinstance(cfg, dict):
            continue
        access = cfg.get("access") or {}
        declared_host = str(access.get("hostname") or "").lower()
        if host in (declared_host, str(name).lower()) and access.get("actor"):
            return str(access["actor"]).strip().lower()
    return None


def resolve(identity_file: Path | None = None, infra: Path | None = None) -> tuple[str | None, str]:
    """(actor, source). source is env | identity.env | registry | none."""
    identity_file = identity_file or IDENTITY_FILE
    infra = infra or INFRA
    env = (os.environ.get("DATACORE_ACTOR") or "").strip().lower()
    if env:
        return env, "env"
    declared = (_parse_env_file(identity_file).get("DATACORE_ACTOR") or "").strip().lower()
    if declared:
        return declared, "identity.env"
    reg = registry_actor(infra=infra)
    if reg:
        return reg, "registry"
    return None, "none"


def this_actor(strict: bool = False) -> str:
    global _warned
    actor, source = resolve()
    if actor:
        return actor
    host = short_hostname()
    if strict:
        raise UndeclaredActor(
            f"no actor declared for {host!r}: set DATACORE_ACTOR in {IDENTITY_FILE} "
            f"or add servers.<name>.access.actor to {INFRA} (DIP-0044)")
    if not _warned:
        _warned = True
        print(f"actor_identity: no declaration for {host!r}; writing as {host!r}. "
              f"Declare DATACORE_ACTOR in {IDENTITY_FILE} (DIP-0044).", file=sys.stderr)
    return host


def actor_source() -> str:
    return resolve()[1]


def principals(path: Path | None = None) -> dict:
    path = path or PRINCIPALS  # resolved at call time so a test can point it elsewhere
    try:
        import yaml
        return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("principals") or {}
    except Exception:  # noqa: BLE001
        return {}


def principal_of(actor: str, path: Path | None = None) -> tuple[str | None, dict]:
    """The principal a writer log belongs to: its own entry, or the one listing it under writes_as."""
    ps = principals(path)
    a = actor.lower()
    if a in ps:
        return a, ps[a]
    for name, p in ps.items():
        if a in [str(w).lower() for w in (p.get("writes_as") or [])]:
            return name, p
    return None, {}


def email_hash(email: str) -> str:
    """The form an author email takes in the public registry.

    datacore-one/datacore is public, and its boundary check refuses personal
    email addresses in tracked files (validate-boundaries.yml, PII scan) —
    the check went red on 2026-09-06 when principals.yaml listed them plainly.
    A writer is therefore bound to the first 16 hex of sha256(lowercased
    email): enough to match a commit author, not enough to publish who the
    operator is. `python3 actor_identity.py hash you@example.com` prints one.
    """
    import hashlib
    return hashlib.sha256(str(email).strip().lower().encode()).hexdigest()[:16]


def allowed_emails(actor: str, path: Path | None = None) -> set[str]:
    """Hashes of the git author emails that may append to this writer's log.

    Reads `email_sha256` (the committed form) and, for a private overlay that
    still lists addresses plainly, `emails` — hashed here so every caller
    compares one way. Empty = unbound.
    """
    _, p = principal_of(actor, path)
    hashes = {str(h).strip().lower() for h in (p.get("email_sha256") or [])}
    hashes |= {email_hash(e) for e in (p.get("emails") or [])}
    return hashes


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "hash":
        print(email_hash(sys.argv[2]))
        raise SystemExit(0)
    a, src = resolve()
    print(f"{a or short_hostname()} ({src})")
    raise SystemExit(0 if a else 1)
