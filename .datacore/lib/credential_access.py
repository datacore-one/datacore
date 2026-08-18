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


def _store_for(entry: dict) -> str:
    """The declared store for THIS platform.

    Single implementation on purpose. When resolve() and get_value() each did
    their own lookup, making one platform-aware and not the other left Linux
    reading a macOS Keychain path — a credential that was present and valid
    reported as unreadable.
    """
    import platform as _plat
    if _plat.system() != "Darwin" and entry.get("storage_linux"):
        return str(entry["storage_linux"]).strip()
    return str(entry.get("storage") or "").strip()


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

    # A credential need not live in a file. The Claude subscription token is in
    # the macOS Keychain on this host and in ~/.claude/.credentials.json on the
    # Linux boxes — both correct, neither assembled by sync. Before this, the
    # resolver assumed "somewhere in .datacore/env" and reported a perfectly
    # working credential as unreadable, which is the same false-negative that
    # sends someone to rotate something that was fine.
    # Platform-specific store. The Claude token is in the macOS Keychain here and
    # in ~/.claude/.credentials.json on the Linux boxes — both correct. Declaring
    # only one made the other host report a working credential as unreadable.
    store = _store_for(c)
    if store:
        return Path(store), f"storage={store} (declared, not assembled by sync)"

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


def instance_name() -> str:
    """Which instance this host is, for scope-aware reporting."""
    import os as _os
    if _os.environ.get("DATACORE_INSTANCE"):
        return _os.environ["DATACORE_INSTANCE"]
    for cand in (ENV / ".instance", SECRETS / ".instance"):
        try:
            return cand.read_text().strip()
        except OSError:
            continue
    return "local"


def granted_scopes() -> tuple[set, set] | None:
    """(spaces, projects) this instance may hold, or None if undeclared.

    None means "cannot tell" and must be treated as such — not as "everything".
    """
    import yaml  # noqa: PLC0415
    man = SECRETS / "instances" / f"{instance_name()}.yaml"
    try:
        d = yaml.safe_load(man.read_text()) or {}
    except OSError:
        return None
    return set(d.get("spaces") or []), set(d.get("projects") or [])


def in_scope(entry: dict) -> bool | None:
    """Is this credential one this instance is supposed to hold?

    A credential outside an instance's scope being absent is CORRECT, not a
    failure. Reporting it as FAIL buries the real failures under noise — the
    first scope-aware run on winston showed 27 'failures', every one of them the
    system working as designed.
    """
    g = granted_scopes()
    if g is None:
        return None
    spaces, projects = g
    if "all" in spaces:
        return True
    scope = (entry.get("scope") or "global").strip()
    if scope == "global":
        return True
    if scope == "space":
        return (entry.get("space") or "") in spaces
    if scope == "project":
        return (entry.get("project") or "") in projects
    if scope in ("instance", "instance-local"):
        return True
    return None


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


def _read_keychain(service: str) -> str | None:
    """macOS Keychain. The value is a JSON blob; the token is inside it."""
    import json as _json
    import subprocess  # noqa: PLC0415
    try:
        raw = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        d = _json.loads(raw)
    except ValueError:
        return raw
    return (d.get("claudeAiOauth") or d).get("accessToken") or None


def _read_json_field(path: Path, field: str) -> str | None:
    import json as _json
    try:
        d = _json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return (d.get("claudeAiOauth") or d).get(field) or None


def fingerprint(value: str) -> str:
    """Identify a secret without revealing it — the convention used everywhere
    else in this installation's credential tooling."""
    return hashlib.sha256(value.encode()).hexdigest()[:12] if value else "(empty)"


def get_value(name: str, *, consumer: str = "") -> str:
    """The value, attested. Raises rather than returning a value it guessed at."""
    c = _entry(name)
    var = c.get("var_name") or (c.get("vars") or [name])[0]
    path, why = resolve(name)
    store = _store_for(c)
    if store.startswith("keychain:"):
        value = _read_keychain(store.split(":", 1)[1])
    elif store.startswith("json:"):
        value = _read_json_field(Path(store.split(":", 1)[1]).expanduser(),
                                c.get("storage_field") or "accessToken")
    else:
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



# Every place a credential value is known to live on a Datacore host. Listed
# explicitly rather than globbed: the point is to catch copies in places that
# are NOT the canonical store, and a glob of the canonical directory cannot see
# ~/.config/cos.env or /etc/datacored.env at all — which is exactly where the
# 2026-08-17 drift lived.
KNOWN_STORES = (
    "{DATA}/.datacore/env/.env",              # canonical assembled
    "{DATA}/.datacore/env/*.env",             # per-service, legacy
    "{HOME}/.config/cos.env",                 # sourced with `set -a` by every cos_*.sh
    "/etc/datacored.env",                     # datacored.service
    "{HOME}/.hermes/.env",                    # hermes gateway
    "{HOME}/.datacore/datacore.env",          # agents, assorted crons
)


def _store_paths() -> list[Path]:
    import glob as _glob
    out: list[Path] = []
    for pat in KNOWN_STORES:
        s = pat.format(DATA=DATA, HOME=Path.home())
        out.extend(Path(x) for x in _glob.glob(s))
    return sorted({p for p in out if p.is_file()})


def _vars_in(path: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return vals
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        vals[k.strip()] = v
    return vals


def duplicates() -> tuple[list, list]:
    """Every credential value that exists in more than one store.

    Returns (divergent, redundant). The split matters more than the count:

      DIVERGENT — the copies disagree. One of them is being read by something,
      and it is not knowable from here which. This is the state that produced
      "works by hand, 401 under cron": ~/.config/cos.env held a revoked token
      and EXPORTED it over the store that could refresh.

      REDUNDANT — the copies agree today. Harmless right now and a countdown:
      when the value next changes, whichever copy is not updated becomes
      divergent, silently.
    """
    seen: dict[str, list[tuple[Path, str]]] = {}
    for path in _store_paths():
        for var, val in _vars_in(path).items():
            if not any(w in var.upper() for w in
                       ("TOKEN", "KEY", "SECRET", "PASSWORD", "PAT", "CREDENTIAL")):
                continue
            seen.setdefault(var, []).append((path, fingerprint(val)))

    divergent, redundant = [], []
    for var, places in sorted(seen.items()):
        if len(places) < 2:
            continue
        (divergent if len({fp for _, fp in places}) > 1 else redundant).append(
            (var, places))
    return divergent, redundant



# ---- Liveness: ask the provider, do not read the file ----------------------
#
# Every check in this installation before now asked whether a value was PRESENT.
# oauth_health_check returned exit 0 and "no expiresAt (long-lived token?)" on a
# credential that could not authenticate at all. Presence is not health; only the
# provider knows.
#
# Endpoints are chosen to be free and side-effect-free. A verifier that costs
# money or writes something is one nobody dares run, and an unrun check is the
# same as no check.
VERIFIERS = {
    # Telegram bots — getMe is free and read-only.
    "TELEGRAM_BOT_TOKEN": ("https://api.telegram.org/bot{v}/getMe", None, '"ok":true'),
    "WINSTON_BOT_TOKEN":  ("https://api.telegram.org/bot{v}/getMe", None, '"ok":true'),
    "REDALERT_BOT_TOKEN": ("https://api.telegram.org/bot{v}/getMe", None, '"ok":true'),
    # Key-introspection endpoints: free, and they answer the only question that
    # matters — does the provider still accept this.
    "OPENROUTER_API_KEY": ("https://openrouter.ai/api/v1/key", "Bearer {v}", "data"),
    "ANTHROPIC_API_KEY":  ("https://api.anthropic.com/v1/models", "x-api-key: {v}", "data"),
    "OPENAI_API_KEY":     ("https://api.openai.com/v1/models", "Bearer {v}", "data"),
    "GITEA_TOKEN":        ("https://gitea.datafund.io/api/v1/user", "token {v}", "login"),
    "READWISE_ACCESS_TOKEN": ("https://readwise.io/api/v2/auth/", "Token {v}", None),
    "OURA_PERSONAL_ACCESS_TOKEN": (
        "https://api.ouraring.com/v2/usercollection/personal_info", "Bearer {v}", None),
    "GH_TOKEN":           ("https://api.github.com/user", "Bearer {v}", "login"),
    # The Claude subscription token. Anthropic rejects a raw-API call with this
    # OAuth token (429/400 regardless of validity — measured), so the only
    # honest probe is the first-party client, which is what actually consumes it.
    "CLAUDE_CODE_OAUTH_TOKEN": ("__cli__", None, None),
}

# Credentials with no free probe. Listed EXPLICITLY rather than left to fall
# through to n-a, so the reason is visible and someone can disagree with it. An
# unlisted credential reporting n-a means "nobody has thought about this yet";
# a listed one means "we decided, and here is why".
NO_PROBE = {
    "GEMINI_API_KEY": "no free introspection endpoint; every call bills",
    "PERPLEXITY_API_KEY": "no free introspection endpoint",
    "SERPAPI_API_KEY": "quota-metered; a probe consumes a search",
    "GAMMA_API_KEY": "no public introspection endpoint",
    "EXA_API_KEY": "search is POST-only and metered; a probe consumes quota",
    "TELEGRAM_CHAT_ID": "an identifier, not a secret — nothing to authenticate",
    "WINSTON_CHAT_ID": "an identifier, not a secret",
}


def verify_value(var: str, value: str, timeout: int = 25) -> tuple[str, str]:
    """Return (state, detail) where state is ok / FAIL / n-a.

    n-a means "no verifier for this variable" — reported, never counted as a
    pass. A check whose "healthy" and "could not tell" are the same output is
    not a check.
    """
    import json as _json
    import urllib.error
    import urllib.request

    if var == "CLAUDE_CODE_OAUTH_TOKEN":
        import subprocess  # noqa: PLC0415
        import shutil as _sh
        if not _sh.which("claude"):
            return "n-a", "claude CLI not on PATH on this host"
        try:
            r = subprocess.run(
                ["claude", "-p", "Reply with exactly: OK", "--output-format", "json"],
                capture_output=True, text=True, timeout=150,
                stdin=subprocess.DEVNULL,
                env={**os.environ, "DATACORE_HEADLESS": "1",
                     "CLAUDE_CODE_OAUTH_TOKEN": value})
        except Exception as e:  # noqa: BLE001
            return "n-a", f"probe failed: {type(e).__name__}"
        raw = r.stdout or ""
        i = raw.find('{"')
        if i < 0:
            return "FAIL", (r.stderr or raw).strip()[:100] or "no output"
        import json as _json
        try:
            d = _json.loads(raw[i:])
        except ValueError:
            return "FAIL", raw[:100]
        return ("FAIL", str(d.get("result"))[:100]) if d.get("is_error") else ("ok", "claude -p accepted it")

    if var in NO_PROBE:
        return "n-a", f"no probe by design: {NO_PROBE[var]}"
    spec = VERIFIERS.get(var)
    if not spec or not spec[0]:
        return "n-a", "no verifier declared for this variable"
    url, auth, expect = spec
    if not value:
        return "FAIL", "empty value"
    try:
        req = urllib.request.Request(url.format(v=value))
        if auth:
            # "Header-Name: template" targets a specific header; a bare template
            # means Authorization. Anthropic takes x-api-key and rejects Bearer
            # with a 401 that reads exactly like a dead key — which is how a
            # perfectly good key got reported FAIL on the first run here.
            if ":" in auth.split("{")[0]:
                hdr, _, tmpl = auth.partition(":")
                req.add_header(hdr.strip(), tmpl.strip().format(v=value))
            else:
                req.add_header("Authorization", auth.format(v=value))
        if "anthropic.com" in url:
            req.add_header("anthropic-version", "2023-06-01")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(400).decode(errors="ignore")
        ok = (expect in body) if expect else True
        return ("ok", f"HTTP {r.status}") if ok else ("FAIL", f"HTTP {r.status}, unexpected body")
    except urllib.error.HTTPError as e:
        return "FAIL", f"HTTP {e.code} {e.read(160).decode(errors='ignore')[:100]}"
    except Exception as e:  # noqa: BLE001 — a network fault is n-a, not a failure
        return "n-a", f"probe failed: {type(e).__name__}: {str(e)[:80]}"


def test_divergent() -> int:
    """For every divergent credential, ask the provider which copy is real."""
    div, _ = duplicates()
    if not div:
        print("  no divergent credentials on this host")
        return 0
    worst = 0
    for var, places in div:
        print(f"  {var}")
        results = []
        for path, fp in places:
            val = _vars_in(path).get(var, "")
            state, detail = verify_value(var, val)
            results.append(state)
            mark = {"ok": "WORKS  ", "FAIL": "DEAD   ", "n-a": "unknown"}[state]
            print(f"    {mark} {fp}  {path}")
            if state != "ok":
                print(f"             {detail[:88]}")
        if "ok" in results and "FAIL" in results:
            print("    -> one copy is live and another is dead. The dead copy is a "
                  "trap: whichever reader finds it first reports the credential invalid.")
            worst = max(worst, 1)
        elif results and all(r == "n-a" for r in results):
            print("    -> no verifier; cannot say which copy is real. Declare one.")
    return worst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("op", choices=["resolve", "get", "unindexed", "duplicates", "test-divergent"])
    ap.add_argument("name", nargs="?", default="")
    ap.add_argument("--consumer", default="cli")
    a = ap.parse_args()

    if a.op == "test-divergent":
        return test_divergent()

    if a.op == "duplicates":
        div, red = duplicates()
        if not div and not red:
            print("  no credential appears in more than one store")
            return 0
        if div:
            print(f"  {len(div)} DIVERGENT — copies disagree, and which one is read is not knowable here:")
            for var, places in div:
                print(f"    {var}")
                for path, fp in places:
                    print(f"      {fp}  {path}")
        if red:
            print(f"\n  {len(red)} redundant — copies agree today, divergent the next time one changes:")
            for var, places in red:
                print(f"    {var:34} in {len(places)} stores")
                for path, fp in places:
                    print(f"      {fp}  {path}")
        return 1 if div else 0

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
