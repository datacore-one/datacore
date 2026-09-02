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


def _var_for(entry: dict, name: str) -> str:
    """The variable the caller actually asked for.

    `_entry` accepts an id or ANY variable an entry declares. Having matched,
    the caller's variable must not then be discarded in favour of the entry's
    primary — for a multi-var credential (a key and its secret, an OAuth1 set)
    that serves one value for every member.

    It did until 2026-09-01: `creds get WITHINGS_CLIENT_SECRET` returned the
    client id. 17 entries and 34 variables were affected, among them
    BUYER_PRIVATE_KEY (served the seller's), GATE_API_SECRET, every X OAuth1
    secret, and PYPI_TOKEN_ORG_WORKSPACE. The failure is quiet in the worst
    way: the caller gets a real credential, fails to authenticate with it, and
    concludes the key was revoked — which is how a live key gets rotated away.

    Asking by id keeps the old meaning: the primary, or the first declared.
    """
    want = name.strip()
    if want == entry.get("var_name") or want in (entry.get("vars") or []):
        return want
    return entry.get("var_name") or (entry.get("vars") or [name])[0]


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
    # INSTANCE-LOCAL WINS. local.env is this host's own tier and is never
    # assembled or synced. If it defines the variable, that is deliberate: it is
    # how a machine holds its OWN key rather than a shared one — per-agent
    # OpenRouter keys being the immediate case. Winston already had its own and
    # a fleet-wide copy was pushed over the top of it, creating divergence out of
    # nothing. Precedence belongs here, decided once, rather than in each
    # consumer's sourcing order.
    var_for_local = _var_for(c, name)
    local = ENV / "local.env"
    if local.is_file() and _read_var(local, var_for_local) is not None:
        return local, f"instance-local override ({instance_name()}) — wins over scope={scope}"

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
    # An explicit host list beats every other rule, in both directions: it is the
    # entry saying which instances this credential belongs to at all.
    #
    # Checked FIRST and before granted_scopes(), because "all" spaces would
    # otherwise swallow it. Without this, `doctor` on a host reading the MASTER
    # index reported FAIL for another machine's instance-local credential —
    # github-pat-hermes is hermes's own PAT, correctly absent here, and correctly
    # absent is not a failure. The hosts: filter was being applied only at
    # distribution time, so the one host that reads the unfiltered index was the
    # one host that got it wrong.
    hosts = entry.get("hosts")
    if hosts:
        return instance_name() in hosts

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


def replication_warning(entry: dict) -> str | None:
    """Is this credential one that cannot be copied between hosts?

    A single-use refresh token cannot be replicated: whichever holder refreshes
    first invalidates every other copy, and the losers cannot tell — they hold a
    value that looks fine and 401s. Distribution tooling will happily copy such a
    credential and produce exactly that, so the index declares it and the tools
    say so instead of trying harder.
    """
    if entry.get("replicable") is False:
        return (f"{entry.get('id')} is NOT replicable: single-use refresh means a "
                f"copied value is revoked the moment another host refreshes. "
                f"Mint per host" +
                (f" (mint_host: {entry['mint_host']})" if entry.get("mint_host") else "") + ".")
    return None


def get_value(name: str, *, consumer: str = "") -> str:
    """The value, attested. Raises rather than returning a value it guessed at."""
    c = _entry(name)
    var = _var_for(c, name)
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
# Where credential values are known to live. DECLARED, not hardcoded: this ships
# as a product, and `~/.config/cos.env` or `/etc/datacored.env` are one
# installation's furniture, not everyone's. Read from
# `.datacore/config/credential-stores.yaml` when present; the defaults below are
# only the canonical Datacore locations that exist in every install.
#
# The reason non-canonical stores must be declarable at all: the 2026-08-17 drift
# lived in exactly those app-owned files, and no glob of the canonical directory
# can see them. A scanner that only looks where it expects finds nothing wrong.
DEFAULT_STORES = (
    "{DATA}/.datacore/env/.env",
    "{DATA}/.datacore/env/local.env",
)

# Third-party applications keep their OWN config, and a value there is not a copy
# of ours — it is a different credential that happens to share a variable name.
# hermes holding its own TELEGRAM_BOT_TOKEN (@kton9_bot) is not drift from the
# Datacore bot (@datacore_1_bot); reporting it as divergence trains the reader to
# ignore the check. Declared per install for the same reason as above.
DEFAULT_EXTERNAL = ()

_STORE_CONFIG = "{DATA}/.datacore/config/credential-stores.yaml"


def _store_config() -> dict:
    import yaml  # noqa: PLC0415
    p = Path(_STORE_CONFIG.format(DATA=DATA))
    try:
        return yaml.safe_load(p.read_text()) or {}
    except (OSError, ValueError):
        return {}


def KNOWN_STORES() -> tuple:
    cfg = _store_config()
    return tuple(cfg.get("stores") or DEFAULT_STORES)


def EXTERNAL_STORES() -> tuple:
    """Files owned by another application, scanned but namespaced separately."""
    cfg = _store_config()
    return tuple(cfg.get("external") or DEFAULT_EXTERNAL)


def _store_paths() -> list[Path]:
    import glob as _glob
    out: list[Path] = []
    for pat in tuple(KNOWN_STORES()) + tuple(EXTERNAL_STORES()):
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


def _expand(pats) -> set:
    import glob as _glob
    out = set()
    for pat in pats:
        out |= {Path(x) for x in _glob.glob(pat.format(DATA=DATA, HOME=Path.home()))}
    return {p for p in out if p.is_file()}


def duplicates() -> tuple[list, list]:
    """Every credential value that exists in more than one DATACORE-OWNED store.

    Returns (divergent, redundant). The split matters more than the count:

      DIVERGENT — the copies disagree. One of them is being read by something,
      and it is not knowable from here which. This is the state that produced
      "works by hand, 401 under cron".

      REDUNDANT — the copies agree today. Harmless now and a countdown: when the
      value next changes, whichever copy is not updated becomes divergent,
      silently.

    Files owned by another application are scanned but namespaced separately, so
    hermes holding its own @kton9_bot token is not reported as drift from the
    Datacore bot. They are different credentials that share a variable name, and
    conflating them produces noise that trains people to ignore the check.
    """
    owned = _expand(KNOWN_STORES())
    external = _expand(EXTERNAL_STORES()) - owned

    seen: dict[str, list[tuple[Path, str]]] = {}
    for path in sorted(owned):
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


def external_namespace() -> list:
    """What other applications hold under names we also use. Informational."""
    owned = _expand(KNOWN_STORES())
    external = _expand(EXTERNAL_STORES()) - owned
    ours = set()
    for path in owned:
        ours |= set(_vars_in(path))
    out = []
    for path in sorted(external):
        for var, val in _vars_in(path).items():
            if var in ours and any(w in var.upper() for w in
                                   ("TOKEN", "KEY", "SECRET", "PAT")):
                out.append((var, path, fingerprint(val)))
    return out


# ---- Liveness: ask the provider, do not read the file ----------------------
#
# Every check here before now asked whether a value was PRESENT.
# oauth_health_check returned exit 0 and "no expiresAt (long-lived token?)" on a
# credential that could not authenticate. Presence is not health; only the
# provider knows.
#
# Endpoints are free and side-effect-free. A verifier that costs money or writes
# something is one nobody dares run, and an unrun check is no check.
#
# No self-hosted URLs here: this ships as a product, and a Gitea host belongs to
# the installation, not to this file. Those come from the index entry's
# `api_base` via _entry_verifier().
VERIFIERS = {
    "TELEGRAM_BOT_TOKEN": ("https://api.telegram.org/bot{v}/getMe", None, '"ok":true'),
    "WINSTON_BOT_TOKEN":  ("https://api.telegram.org/bot{v}/getMe", None, '"ok":true'),
    "REDALERT_BOT_TOKEN": ("https://api.telegram.org/bot{v}/getMe", None, '"ok":true'),
    "OPENROUTER_API_KEY": ("https://openrouter.ai/api/v1/key", "Bearer {v}", "data"),
    "ANTHROPIC_API_KEY":  ("https://api.anthropic.com/v1/models", "x-api-key: {v}", "data"),
    "OPENAI_API_KEY":     ("https://api.openai.com/v1/models", "Bearer {v}", "data"),
    "READWISE_ACCESS_TOKEN": ("https://readwise.io/api/v2/auth/", "Token {v}", None),
    "OURA_PERSONAL_ACCESS_TOKEN": (
        "https://api.ouraring.com/v2/usercollection/personal_info", "Bearer {v}", None),
    "GH_TOKEN":           ("https://api.github.com/user", "Bearer {v}", "login"),
    # X / Twitter. Both credential classes get a probe, because they fail
    # independently and for different reasons: the app-only bearer dies when the
    # app's keys are regenerated in the developer portal, the OAuth2 user token
    # when the user-context grant is revoked or rotated. On 2026-08-18 both were
    # dead and nothing said so — plur-x-oauth2 reported n-a ("no verifier"),
    # which is precisely the gap that let a release announcement fail at the
    # last step after every other step had gone green.
    "PLUR_X_BEARER_TOKEN": ("https://api.twitter.com/2/users/by/username/plur_ai",
                            "Bearer {v}", "data"),
    "PLUR_X_OAUTH2_ACCESS_TOKEN": ("https://api.twitter.com/2/users/me",
                                   "Bearer {v}", "data"),
    "JSSR_X_BEARER_TOKEN": ("https://api.twitter.com/2/users/me", "Bearer {v}", "data"),
    # Anthropic rejects a raw-API call with a subscription OAuth token (429/400
    # regardless of validity — measured), so the only honest probe is the
    # first-party client, which is what actually consumes it.
    "CLAUDE_CODE_OAUTH_TOKEN": ("__cli__", None, None),

    # --- Added 2026-08-19, working down the n-a backlog -------------------
    # Each of these is a free, read-only, unmetered endpoint. A verifier that
    # costs money or writes something is one nobody dares run, and an unrun
    # check is not a check.
    "DO_API_TOKEN":      ("https://api.digitalocean.com/v2/account", "Bearer {v}", "account"),
    "DO_TOKEN_DATAFUND": ("https://api.digitalocean.com/v2/account", "Bearer {v}", "account"),
    # /v1/api-key, NOT /v1/models: a key without model-list permission gets 403
    # "permission-denied" from /v1/models while being perfectly valid — it even
    # names the team in the error. That is a live credential reported dead.
    "XAI_API_KEY":       ("https://api.x.ai/v1/api-key", "Bearer {v}", None),
    "DEVTO_API_KEY":     ("https://dev.to/api/articles/me?per_page=1", "api-key: {v}", None),
    # Etsy publishes a ping endpoint whose entire purpose is key validation.
    "ETSY_API_KEY":      ("https://openapi.etsy.com/v3/application/openapi-ping",
                          "x-api-key: {v}", "application_id"),
    # Etherscan answers 200 with {"status":"0","result":"Invalid API Key"} for a
    # bad key, so the HTTP code proves nothing and the body is the real check.
    # V2 with an explicit chainid: the V1 path still answers 200 but with an
    # empty body, which reported a working key as "unexpected body".
    "ETHERSCAN_API_KEY": ("https://api.etherscan.io/v2/api?chainid=1&module=stats"
                          "&action=ethsupply&apikey={v}", None, '"status":"1"'),
    # Notion 400s without Notion-Version regardless of key validity — hence the
    # multi-header form.
    "NOTION_API_KEY":    ("https://api.notion.com/v1/users/me",
                          ("Bearer {v}", "Notion-Version: 2022-06-28"), "object"),
}

# X OAuth 1.0a user-context sets. These CANNOT go in VERIFIERS: the credential is
# not a value, it is a four-value tuple, and the proof of life is a signature
# rather than a header. A bearer probe against a signing credential says nothing
# about it.
#
# This is the gap that let 0.18.0 publish everything and then fail to tweet.
# release.sh signs with OAuth1 (PLUR_X_API_KEY/_API_SECRET/_ACCESS_TOKEN/
# _ACCESS_TOKEN_SECRET), while the only X probes that existed were bearer probes
# against two OAuth2 values that NOTHING consumes. So `doctor` reported FAIL for
# two credentials nobody uses and stayed silent about the four that post.
#
# Keyed on the user-context token, because that is the value that dies when a
# grant is revoked, and it is what the entry should name as its var_name.
OAUTH1_SETS = {
    "PLUR_X_ACCESS_TOKEN": ("PLUR_X_API_KEY", "PLUR_X_API_SECRET",
                            "PLUR_X_ACCESS_TOKEN", "PLUR_X_ACCESS_TOKEN_SECRET"),
    "X_ACCESS_TOKEN": ("X_CONSUMER_KEY", "X_CONSUMER_SECRET",
                       "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"),
    "FDS_X_ACCESS_TOKEN": ("FDS_X_API_KEY", "FDS_X_API_SECRET",
                           "FDS_X_ACCESS_TOKEN", "FDS_X_ACCESS_TOKEN_SECRET"),
    "JSSR_X_ACCESS_TOKEN": ("JSSR_X_API_KEY", "JSSR_X_API_SECRET",
                            "JSSR_X_ACCESS_TOKEN", "JSSR_X_ACCESS_TOKEN_SECRET"),
}

# Credentials with no free probe. Listed EXPLICITLY rather than left to fall
# through, so the reason is visible and someone can disagree with it. An unlisted
# credential reporting n-a means "nobody has thought about this"; a listed one
# means "we decided, and here is why".
NO_PROBE = {
    "GEMINI_API_KEY": "no free introspection endpoint; every call bills",
    "PERPLEXITY_API_KEY": "no free introspection endpoint",
    "SERPAPI_API_KEY": "quota-metered; a probe consumes a search",
    "GAMMA_API_KEY": "no public introspection endpoint",
    "EXA_API_KEY": "search is POST-only and metered; a probe consumes quota",
    "TELEGRAM_CHAT_ID": "an identifier, not a secret — nothing to authenticate",
    "WINSTON_CHAT_ID": "an identifier, not a secret",

    # --- 2026-08-19: the rest of the n-a backlog, decided rather than left ---
    # "no verifier declared" means nobody has thought about it. Each line below
    # means someone did, and disagreed with probing. That distinction is the
    # whole point of the three-state report.

    # Signed-request APIs. Verifiable in principle (the same way X OAuth1 now is)
    # but these are TRADING keys: the cheapest signed endpoint still authenticates
    # against an account that can place orders. Not probing on a schedule.
    "GATE_API_KEY": "trading API — HMAC-signed; will not probe an order-capable key on a timer",
    "GATE_API_KEY_2": "trading API — HMAC-signed; will not probe an order-capable key on a timer",

    # Region-dependent hosts. A probe against the wrong region returns 401 for a
    # perfectly good key, and a false FAIL is worse than an honest n-a.
    "POSTHOG_API_KEY": "host is region-specific (us/eu/self-hosted); declare api_base on the entry to enable",
    "GITEA_TOKEN": "self-hosted; declare api_base on the entry and _entry_verifier will probe it",
    "COINGECKO_API_KEY": "demo and pro tiers take different auth params; probing the wrong one reports a good key dead",

    # No introspection endpoint that does not consume quota or produce output.
    "COINGLASS_API_KEY": "no free introspection endpoint; every call is metered",
    "CREATOMATE_API_KEY": "render API — the cheapest authenticated call creates a render",
    "LATE_API_KEY": "no read-only introspection endpoint published",
    "APIFRAME_API_KEY": "image-generation proxy; authenticated calls are billed per job",

    # Not API credentials at all.
    "SEPOLIA_RPC_URL": "an endpoint URL; reachability is not authentication",
    "DATA_ESCROW_ADDRESS_SEPOLIA": "a contract address, public by construction — nothing to authenticate",
    "SELLER_PRIVATE_KEY": "a wallet key; proving control means signing, and signing on a timer is not free of consequence",
    "BUYER_PRIVATE_KEY": "a wallet key; see SELLER_PRIVATE_KEY",
    "WALLET_PRIVATE_KEY": "a wallet key; see SELLER_PRIVATE_KEY",
    "ENGAGEMENT_CHAT_ID": "an identifier, not a secret",
    # Left behind after their dead keys were deleted 2026-08-19. Identifiers, not
    # credentials — kept because each still has a consumer, and named here so the
    # entries report a reason rather than the undecided "no verifier declared".
    "DO_DROPLET_ID": "an identifier, not a secret — the DO token was deleted as dead",
    "ETSY_SHOP_ID": "an identifier, not a secret — the Etsy key was deleted as dead",
    "PHONE_TOKEN": "device pairing token; no server-side introspection exists",

    # Write-only by design.
    "PYPI_TOKEN_PLUR_HERMES": "upload-scoped; the only call that exercises it publishes a release",
    "PYPI_TOKEN_ORG_WORKSPACE": "upload-scoped; the only call that exercises it publishes a release",

    # Probing these would look like account takeover to the provider.
    "MIDJOURNEY_DISCORD_TOKEN": "a user session token; automated probing risks the account itself",

    # OAuth2 with a refresh dance — verifying means possibly rotating, and a
    # verifier with a side effect is a verifier nobody runs.
    "WITHINGS_ACCESS_TOKEN": "OAuth2; a probe may trigger refresh, and a check with side effects is not a check",
}


def _entry_verifier(entry: dict) -> tuple | None:
    """A verifier built from the index entry itself.

    Self-hosted services have no universal endpoint — a Gitea URL belongs to the
    installation. An entry declaring `api_base` gets a probe; one that does not
    reports n-a and says why, which is honest and portable.
    """
    base = (entry or {}).get("api_base")
    if not base:
        return None
    if (entry.get("provider") or "").lower() == "gitea":
        return (base.rstrip("/") + "/api/v1/user", "token {v}", "login")
    return (base.rstrip("/"), "Bearer {v}", None)


def _oauth1_probe(entry: dict, primary_var: str, value: str,
                  timeout: int = 25) -> tuple[str, str]:
    """Verify an X OAuth 1.0a set by signing a real read-only request.

    GET /2/users/me needs user context, so a 200 here proves the whole tuple:
    consumer pair, token pair, and signature agreement. Read-only and unmetered,
    so it is a probe someone will actually run.

    The companions come from the SAME resolved store as the primary value. They
    are deliberately not fetched via get_value(): a signing set is one credential
    and must be read as one, from one place. Pulling three of four from wherever
    they happen to be found is how a working set gets diagnosed as broken.
    """
    import base64
    import hashlib
    import hmac
    import json as _json
    import secrets as _secrets
    import time as _time
    import urllib.error
    import urllib.parse
    import urllib.request

    def pe(s: object) -> str:
        return urllib.parse.quote(str(s), safe="-._~")

    ck_v, cs_v, tok_v, ts_v = OAUTH1_SETS[primary_var]
    try:
        path, _why = resolve(entry.get("id") or primary_var)
    except (CredentialNotIndexed, CredentialUnresolvable) as e:
        return "n-a", f"cannot resolve store: {e}"

    vals = {}
    for name in (ck_v, cs_v, tok_v, ts_v):
        vals[name] = value if name == primary_var else _read_var(path, name)
    missing = [k for k, v in vals.items() if not v]
    if missing:
        # Not n-a: the entry claims a signing set and the store does not hold one.
        return "FAIL", "incomplete OAuth1 set — missing " + ", ".join(missing)

    url = "https://api.twitter.com/2/users/me"
    params = {
        "oauth_consumer_key": vals[ck_v],
        "oauth_nonce": _secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(_time.time())),
        "oauth_token": vals[tok_v],
        "oauth_version": "1.0",
    }
    norm = "&".join(f"{pe(k)}={pe(params[k])}" for k in sorted(params))
    base = "&".join(["GET", pe(url), pe(norm)])
    key = f"{pe(vals[cs_v])}&{pe(vals[ts_v])}".encode()
    params["oauth_signature"] = base64.b64encode(
        hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    header = "OAuth " + ", ".join(
        f'{pe(k)}="{pe(v)}"' for k, v in sorted(params.items()))

    try:
        req = urllib.request.Request(url, headers={"Authorization": header})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            body = r.read().decode(errors="replace")
        who = ((_json.loads(body) or {}).get("data") or {}).get("username")
        return "ok", f"OAuth1 signature accepted — @{who}" if who else "OAuth1 signature accepted"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode(errors="replace")[:120].replace("\n", " ")
        except Exception:  # noqa: BLE001, S110
            pass
        return "FAIL", f"HTTP {e.code} {detail}".strip()
    except Exception as e:  # noqa: BLE001
        return "n-a", f"probe failed: {type(e).__name__}"


def verify_value(var: str, value: str, timeout: int = 25,
                 entry: dict | None = None) -> tuple[str, str]:
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

    # Deliberately disabled. Distinct from "broken": an account switched off on
    # purpose must not report FAIL forever, because a permanent red line is how
    # doctor stops being read — the failure mode behind the 0.18.0 release. It
    # must not report ok either. n-a with the decision stated is the honest
    # answer, and the reason makes it arguable rather than inherited.
    if (entry or {}).get("disabled"):
        why = (entry or {}).get("disabled_reason") or "no reason recorded"
        return "n-a", f"disabled by decision: {why}"

    if var in OAUTH1_SETS:
        return _oauth1_probe(entry or {}, var, value, timeout)

    if var in NO_PROBE:
        return "n-a", f"no probe by design: {NO_PROBE[var]}"
    spec = VERIFIERS.get(var) or _entry_verifier(entry or {})
    if not spec or not spec[0]:
        return "n-a", "no verifier declared for this variable"
    url, auth, expect = spec
    if not value:
        return "FAIL", "empty value"
    try:
        req = urllib.request.Request(url.format(v=value))
        # A real User-Agent. urllib defaults to "Python-urllib/3.x", which
        # Cloudflare-fronted APIs reject outright: dev.to returned 403 to the
        # probe and 200 to curl with the identical auth header. That is a live
        # credential reported dead by a header the check never thought about,
        # and it would have looked exactly like a revoked key.
        req.add_header("User-Agent", "datacore-creds-doctor/1.0 (+credential liveness probe)")
        if auth:
            # "Header-Name: template" targets a specific header; a bare template
            # means Authorization. Anthropic takes x-api-key and rejects Bearer
            # with a 401 that reads exactly like a dead key — which is how a
            # perfectly good key got reported FAIL on the first run here.
            #
            # A tuple sends several headers. Some APIs refuse without a second,
            # non-secret header — Notion requires Notion-Version, and omitting it
            # returns 400 regardless of whether the key is good. A probe that
            # cannot express the request the API actually needs produces a
            # confident wrong verdict, which is worse than reporting n-a.
            for spec in (auth if isinstance(auth, (tuple, list)) else [auth]):
                if ":" in spec.split("{")[0]:
                    hdr, _, tmpl = spec.partition(":")
                    req.add_header(hdr.strip(), tmpl.strip().format(v=value))
                else:
                    req.add_header("Authorization", spec.format(v=value))
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
        ext = external_namespace()
        if not div and not red:
            print("  no credential appears in more than one Datacore-owned store")
            if ext:
                # Still worth showing: a name collision is not drift, but it is
                # how someone reads the wrong value into the wrong app.
                print(f"\n  {len(ext)} name collision(s) with app-owned config — NOT our drift:")
                for var, path, fp in ext:
                    print(f"    {var:30} {fp}  {path}")
                print("    (a different credential that shares a variable name)")
            return 0
        if div:
            print(f"  {len(div)} DIVERGENT — copies disagree, and which one is read is not knowable here:")
            for var, places in div:
                print(f"    {var}")
                for path, fp in places:
                    print(f"      {fp}  {path}")
        ext = external_namespace()
        if ext:
            print(f"\n  {len(ext)} name collision(s) with app-owned config — NOT our drift:")
            for var, path, fp in ext:
                print(f"    {var:30} {fp}  {path}")
            print("    (a different credential that shares a variable name)")
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
