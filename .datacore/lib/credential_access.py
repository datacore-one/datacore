#!/usr/bin/env python3
"""The one way to locate and read a credential. Guessing is the bug it removes.

THREE PROBLEMS, ONE CHOKEPOINT.

1. LOCATION. There are two credential layers by design — `.datacore/secrets/`
   is the source synced from the central repo, `.datacore/env/` is the assembled
   runtime layer — plus per-service files inside the second. Nothing maps a
   credential NAME to which of those to read, so every caller invents an answer.
   The codebase currently holds 76 references to `.datacore/env/.env`, 37 to
   `~/.config/cos.env`, 20 to `/etc/datacored.env` and a long tail besides. When
   one of those guesses is wrong the failure is not "file missing" — it is a
   4-month-stale value read confidently, which surfaces as "this credential is
   invalid" and sends someone to rotate a credential that was fine.

2. ACCOUNTABILITY. On 2026-08-17 five copies of one token drifted across a host
   and nobody could say which process wrote which store. Reconstruction took an
   hour of comparing mtimes, and the conclusion — an operator's own manual sync
   run — stayed a guess until they confirmed it. Every read and write here is
   attested to the ledger, so that question becomes a query.

3. INDEX TRUST. `creds audit` reports "All checks passed" for 35 credentials
   while the one credential that had been failing for weeks was absent from the
   index entirely. An index is only worth consulting if being absent from it is
   an error, so resolution REFUSES unknown names rather than falling back to a
   search. That refusal is the mechanism that keeps the index true.

WHAT THIS IS NOT. It does not refresh rotating credentials — that needs an owner
and a lock, and both are still open questions in DIP-0018's rotating-credential
extension. Reading is safe to centralise today; refreshing is not, and pretending
otherwise is how the last two attempts went wrong.

    credential_access.py resolve <name>     # where it lives, and why
    credential_access.py get <name>         # the value's fingerprint, never the value
    credential_access.py unindexed          # values on disk that no index entry claims

As a library:

    from credential_access import resolve, get_value, CredentialNotIndexed
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

try:
    from datacore.ledger import attest
except Exception:  # noqa: BLE001 — attestation must never gate credential access
    def attest(*_a, **_k):  # type: ignore[misc]
        return None

DATA = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
SECRETS = DATA / ".datacore" / "secrets"          # source of truth, synced
ENV = DATA / ".datacore" / "env"                  # assembled, what runtime reads
INDEX = SECRETS / "credential-index.yaml"


class CredentialNotIndexed(KeyError):
    """The name is not in the index — deliberately fatal.

    Falling back to a filesystem search is what produced the duplicate stores
    this module exists to prevent: a search finds *a* file, and the caller
    cannot tell a current one from an abandoned one.
    """


class CredentialUnresolvable(FileNotFoundError):
    """Indexed, but the file its scope implies is not present on this host."""


def _index() -> list[dict]:
    import yaml  # noqa: PLC0415 — optional at import time, required here
    if not INDEX.is_file():
        raise CredentialUnresolvable(f"no credential index at {INDEX}")
    return (yaml.safe_load(INDEX.read_text()) or {}).get("credentials") or []


def _entry(name: str) -> dict:
    """Find by id, or by any variable name it declares.

    Callers think in variable names (`OURA_PAT`); the index is keyed by id
    (`oura-pat`). Accepting both is what stops a caller from skipping the index
    because the lookup was awkward.
    """
    want = name.strip()
    for c in _index():
        if c.get("id") == want:
            return c
        if c.get("var_name") == want:
            return c
        if want in (c.get("vars") or []):
            return c
    raise CredentialNotIndexed(
        f"{want!r} is not in {INDEX.name}. Add it with `creds add` — a credential "
        f"that is not indexed cannot be located, verified, or audited, and this "
        f"refusal is what keeps that true.")


def resolve(name: str) -> tuple[Path, str]:
    """Return (path, why) for a credential name. Never searches, never guesses.

    Scope decides the file, per DIP-0018's three tiers. The assembled layer is
    what runtime reads; the secrets layer is the source `creds sync` pulls from
    and is NOT read directly, because on any host it may be older than what sync
    last assembled.
    """
    c = _entry(name)
    scope = (c.get("scope") or "global").strip()
    space = (c.get("space") or "").strip()

    # ONE DESTINATION, WHATEVER THE SCOPE. `secrets/scripts/sync.sh` assembles
    # global + every permitted space + projects into a single output file:
    #
    #     OUTPUT_FILE="$OUTPUT_DIR/.env"    # .datacore/env/.env
    #
    # So scope selects which SOURCE file a value comes from; it does not give
    # the value a separate home on the destination side. An earlier revision of
    # this function mapped scope=space to `env/<space>.env` — a reasonable
    # reading of DIP-0018's three tiers, and wrong: no such file is ever
    # written. That is the same mistake this module exists to stop, made inside
    # the module itself, and it was caught only by resolving a real credential
    # and finding the path absent.
    dest = ENV / ".env"
    why = f"scope={scope}" + (f" space={space}" if space else "") + \
          " -> assembled by sync.sh into the single .env"

    if scope in ("instance", "instance-local"):
        # The one genuine exception: instance-local values are host-specific and
        # are NOT assembled, precisely so they never travel to another machine.
        return ENV / "local.env", "scope=instance-local -> this host only, not assembled"
    if scope == "space" and not space:
        raise CredentialUnresolvable(
            f"{name!r} declares scope=space with no `space:` field — "
            f"unresolvable by design rather than by guess")
    return dest, why


LEGACY_PER_SERVICE = (
    "Per-service files in .datacore/env/ (oura.env, gateio.env, forge.env, …) "
    "predate the assembled .env and are NOT written by sync. Where a variable "
    "appears in both, they are duplicates — OURA_PAT is in .env and oura.env "
    "today — and a reader that picks the per-service copy is reading whatever "
    "was last hand-edited. Resolution deliberately never returns one."
)


def _read_var(path: Path, var: str) -> str | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if line.startswith(var + "="):
            v = line.split("=", 1)[1].strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            return v
    return None


def fingerprint(value: str) -> str:
    """Identify a secret without revealing it — the convention used everywhere
    else in this installation's credential tooling."""
    return hashlib.sha256(value.encode()).hexdigest()[:12] if value else "(empty)"


def get_value(name: str, *, consumer: str = "") -> str:
    """The value, attested. Raises rather than returning a value it guessed at."""
    c = _entry(name)
    var = c.get("var_name") or (c.get("vars") or [name])[0]
    path, why = resolve(name)
    value = _read_var(path, var)

    attest("credential.read",
           ref=str(c.get("id") or name),
           detail=f"{var} from {path} ({why}) by {consumer or 'unspecified'} "
                  f"-> {fingerprint(value or '')}")

    if value is None:
        raise CredentialUnresolvable(
            f"{name!r} resolves to {path} ({why}) but {var} is not set there. "
            f"Run `creds sync` — do NOT search for another copy, which is how "
            f"stale duplicates get adopted.")
    return value


def unindexed(paths: list[Path] | None = None) -> list[tuple[Path, str]]:
    """Variables present on disk that no index entry claims.

    The index is only trustworthy if nothing important is missing from it, and
    the credential that caused every outage this quarter was missing. This is
    the check that would have said so.
    """
    known: set[str] = set()
    try:
        for c in _index():
            if c.get("var_name"):
                known.add(c["var_name"])
            known.update(c.get("vars") or [])
    except CredentialUnresolvable:
        return []

    targets = paths or [p for p in ENV.glob("*.env")] + [ENV / ".env"]
    out: list[tuple[Path, str]] = []
    for p in sorted(set(targets)):
        try:
            text = p.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if not line or line.startswith("#") or "=" not in line:
                continue
            var = line.split("=", 1)[0].strip()
            # Only flag things that look like credentials; a config flag is not
            # a secret and demanding it be indexed would train people to ignore
            # this check.
            if var not in known and any(
                    w in var.upper() for w in
                    ("TOKEN", "KEY", "SECRET", "PASSWORD", "PAT", "CREDENTIAL")):
                out.append((p, var))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("op", choices=["resolve", "get", "unindexed"])
    ap.add_argument("name", nargs="?", default="")
    ap.add_argument("--consumer", default="cli")
    a = ap.parse_args()

    if a.op == "unindexed":
        rows = unindexed()
        if not rows:
            print("  every credential-shaped variable on disk is indexed")
            return 0
        print(f"  {len(rows)} credential-shaped variable(s) NOT in the index:")
        for p, var in rows:
            print(f"    {var:34} {p}")
        print("\n  An unindexed credential cannot be located, verified or audited.")
        print("  Add each with `creds add`.")
        return 1

    if not a.name:
        print("  a credential name is required", file=sys.stderr)
        return 2

    try:
        if a.op == "resolve":
            path, why = resolve(a.name)
            print(f"  {a.name}\n    path: {path}\n    why : {why}\n"
                  f"    exists: {path.is_file()}")
            return 0
        value = get_value(a.name, consumer=a.consumer)
        print(f"  {a.name} -> {fingerprint(value)}  (value not printed)")
        return 0
    except (CredentialNotIndexed, CredentialUnresolvable) as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
