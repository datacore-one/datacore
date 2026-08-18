#!/usr/bin/env python3
"""
Credential Manager - Query, search, audit, and rotate credentials.

Per DIP-0018 Credential Management. Provides CLI access to
.datacore/specs/credential-index.yaml for credential discoverability
and .datacore/state/credential-index.yaml for rotation tracking.

Usage:
    python creds.py list                        # List all credentials
    python creds.py list --category ai-services # Filter by category
    python creds.py list --tier critical        # Filter by security tier
    python creds.py list --format json          # JSON output
    python creds.py show alchemy-sepolia-rpc    # Show credential details
    python creds.py search "anthropic"          # Search all fields
    python creds.py audit                       # Run audit checks
    python creds.py rotate                      # Show overdue credentials
    python creds.py rotate --all                # Show all credentials
    python creds.py rotate --credential EXA     # Rotate specific credential
    python creds.py rotate --bootstrap          # Bootstrap from .env
    python creds.py rotate --all --format json  # JSON output for MCP/scripts
"""

import argparse
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class LocationEntry:
    path: str
    var_name: str
    primary: bool = False
    note: str = ""


@dataclass
class Credential:
    id: str
    name: str
    type: str
    security_tier: str
    category: str
    provider: str
    locations: List[LocationEntry]
    used_by: List[str] = field(default_factory=list)
    description: str = ""
    documentation: str = ""
    note: str = ""
    status: str = "active"
    # Extra fields captured but not modeled
    extra: Dict = field(default_factory=dict)


@dataclass
class AuditIssue:
    severity: str  # error, warning, info
    credential_id: str
    message: str


@dataclass
class AuditResult:
    issues: List[AuditIssue] = field(default_factory=list)
    total_credentials: int = 0
    checked: int = 0

    @property
    def errors(self) -> List[AuditIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[AuditIssue]:
        return [i for i in self.issues if i.severity == "warning"]


@dataclass
class RotationEntry:
    provider: str
    provider_url: str
    env_var: str
    rotated_at: Optional[str] = None
    rotated_by: str = "unknown"
    next_rotation: Optional[str] = None
    status: str = "unknown"  # active, expired, unknown

    @property
    def credential(self) -> str:
        return self.env_var.lower().replace("_", "-")


# Known provider patterns for bootstrap detection
PROVIDER_PATTERNS = {
    "ANTHROPIC": ("Anthropic", "https://console.anthropic.com/settings/keys"),
    "OPENAI": ("OpenAI", "https://platform.openai.com/api-keys"),
    "EXA": ("Exa", "https://dashboard.exa.ai/api-keys"),
    "SERPAPI": ("SerpAPI", "https://serpapi.com/manage-api-key"),
    "PERPLEXITY": ("Perplexity", "https://www.perplexity.ai/settings/api"),
    "POSTHOG": ("PostHog", "https://app.posthog.com/project/settings"),
    "GAMMA": ("Gamma", "https://gamma.app/settings"),
    "GEMINI": ("Google Gemini", "https://aistudio.google.com/apikey"),
    "ETHERSCAN": ("Etherscan", "https://etherscan.io/myapikey"),
    "ALCHEMY": ("Alchemy", "https://dashboard.alchemy.com/apps"),
    "APIFRAME": ("Apiframe", "https://apiframe.ai/dashboard"),
    "LATE": ("Late.dev", "https://late.dev/settings"),
    "TELEGRAM": ("Telegram", "https://t.me/BotFather"),
    "DO_": ("DigitalOcean", "https://cloud.digitalocean.com/account/api/tokens"),
    "XAI": ("x.ai", "https://console.x.ai/"),
    "X_": ("X / Twitter", "https://developer.x.com/en/portal/dashboard"),
    "JINA": ("Jina", "https://jina.ai/api-dashboard"),
    "MIDJOURNEY": ("Discord/Midjourney", "https://discord.com/app"),
}

ROTATION_DAYS = 90


class RotationIndex:
    """Manages credential rotation state in .datacore/state/credential-index.yaml."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: List[RotationEntry] = []
        self._index: Dict[str, RotationEntry] = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        with open(self.path) as f:
            data = yaml.safe_load(f) or {}
        for entry in data.get("credentials", []):
            re = RotationEntry(
                provider=entry.get("provider", ""),
                provider_url=entry.get("provider_url", ""),
                env_var=entry.get("env_var", ""),
                rotated_at=entry.get("rotated_at"),
                rotated_by=entry.get("rotated_by", "unknown"),
                next_rotation=entry.get("next_rotation"),
                status=entry.get("status", "unknown"),
            )
            self.entries.append(re)
            self._index[re.env_var] = re

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "1.0",
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "credentials": [
                {
                    "credential": e.credential,
                    "provider": e.provider,
                    "provider_url": e.provider_url,
                    "env_var": e.env_var,
                    "rotated_at": e.rotated_at,
                    "rotated_by": e.rotated_by,
                    "next_rotation": e.next_rotation,
                    "status": e.status,
                }
                for e in self.entries
            ],
        }
        with open(self.path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def get(self, env_var: str) -> Optional[RotationEntry]:
        return self._index.get(env_var)

    def upsert(self, entry: RotationEntry):
        for i, e in enumerate(self.entries):
            if e.env_var == entry.env_var:
                self.entries[i] = entry
                self._index[entry.env_var] = entry
                return
        self.entries.append(entry)
        self._index[entry.env_var] = entry

    def stale(self, days: int = ROTATION_DAYS) -> List[RotationEntry]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = []
        for e in self.entries:
            if not e.rotated_at:
                result.append(e)
                continue
            try:
                rotated = datetime.fromisoformat(e.rotated_at.replace("Z", "+00:00"))
                if rotated < cutoff:
                    result.append(e)
            except (ValueError, AttributeError):
                result.append(e)
        return result


class CredentialIndex:
    """Loads and queries the credential index YAML."""

    KNOWN_FIELDS = {
        "id", "name", "type", "security_tier", "category", "provider",
        "locations", "used_by", "description", "documentation", "note", "status"
    }

    def __init__(self, path: Path):
        self.path = path
        self.credentials: List[Credential] = []
        self.security_tiers: Dict = {}
        self.categories: Dict = {}
        self._load()

    def _load(self):
        with open(self.path) as f:
            data = yaml.safe_load(f) or {}

        self.security_tiers = data.get("security_tiers", {})
        self.categories = data.get("categories", {})

        for entry in data.get("credentials", []):
            if entry is None:
                continue
            locations = []
            for loc in entry.get("locations", []):
                locations.append(LocationEntry(
                    path=loc.get("path", ""),
                    var_name=loc.get("var_name", ""),
                    primary=loc.get("primary", False),
                    note=loc.get("note", ""),
                ))
            extra = {k: v for k, v in entry.items() if k not in self.KNOWN_FIELDS}
            self.credentials.append(Credential(
                id=entry.get("id", "unknown"),
                name=entry.get("name", ""),
                type=entry.get("type", ""),
                security_tier=entry.get("security_tier", "") or entry.get("tier", ""),
                category=entry.get("category", ""),
                provider=entry.get("provider", ""),
                locations=locations,
                used_by=entry.get("used_by", []),
                description=entry.get("description", ""),
                documentation=entry.get("documentation", ""),
                note=entry.get("note", ""),
                status=entry.get("status", "active"),
                extra=extra,
            ))

    def filter(self, category: str = None, tier: str = None,
               status: str = None) -> List[Credential]:
        result = self.credentials
        if category:
            result = [c for c in result if c.category == category]
        if tier:
            result = [c for c in result if c.security_tier == tier]
        if status:
            result = [c for c in result if c.status == status]
        return result

    def get(self, cred_id: str) -> Optional[Credential]:
        for c in self.credentials:
            if c.id == cred_id:
                return c
        return None

    def search(self, query: str) -> List[Credential]:
        q = query.lower()
        results = []
        for c in self.credentials:
            searchable = " ".join([
                c.id, c.name, c.type, c.category, c.provider,
                c.description, c.note,
                " ".join(c.used_by),
                " ".join(loc.var_name for loc in c.locations),
            ]).lower()
            if q in searchable:
                results.append(c)
        return results

    def did_you_mean(self, cred_id: str, n: int = 3) -> List[str]:
        all_ids = [c.id for c in self.credentials]
        return difflib.get_close_matches(cred_id, all_ids, n=n, cutoff=0.4)


class CredentialManager:
    """CLI operations for credential management."""

    VALID_TIERS = {"critical", "high", "medium", "low"}
    VALID_STATUSES = {"active", "missing", "revoked", "expired", "deprecated", "planned"}

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.environ.get(
            "DATACORE_ROOT", os.path.expanduser("~/Data")))
        # THE INDEX LIVES IN THE SECRETS REPO, not in specs/. Both files existed
        # for four months and drifted: specs/ was last updated 2026-04-23 with 35
        # entries while the secrets-repo copy kept being maintained. Only the
        # secrets-repo copy travels — it is inside the repo `creds sync` pulls to
        # every instance, so it is the only one a second machine can ever see.
        # specs/ is left as a pointer stub.
        self.index_path = self.data_dir / ".datacore" / "secrets" / "credential-index.yaml"
        self.example_path = self.data_dir / ".datacore" / "specs" / "credential-index.yaml.example"

    def _load_index(self) -> Optional[CredentialIndex]:
        if not self.index_path.exists():
            return None
        return CredentialIndex(self.index_path)

    def cmd_list(self, category: str = None, tier: str = None,
                 fmt: str = "table") -> int:
        index = self._load_index()
        if index is None:
            self._print_bootstrap()
            return 1

        creds = index.filter(category=category, tier=tier)
        if not creds:
            print("No credentials match the filter.")
            return 0

        if fmt == "json":
            print(json.dumps([self._cred_to_dict(c) for c in creds], indent=2))
            return 0

        # Table output
        print(f"{'ID':<30} {'Tier':<10} {'Category':<20} {'Provider':<15}")
        print("-" * 78)
        for c in creds:
            print(f"{c.id:<30} {c.security_tier:<10} {c.category:<20} {c.provider:<15}")
        print(f"\n{len(creds)} credential(s)")
        return 0

    def cmd_show(self, cred_id: str) -> int:
        index = self._load_index()
        if index is None:
            self._print_bootstrap()
            return 1

        cred = index.get(cred_id)
        if cred is None:
            suggestions = index.did_you_mean(cred_id)
            print(f"Credential '{cred_id}' not found.")
            if suggestions:
                print(f"Did you mean: {', '.join(suggestions)}?")
            return 1

        print(f"ID:            {cred.id}")
        print(f"Name:          {cred.name}")
        print(f"Type:          {cred.type}")
        print(f"Security Tier: {cred.security_tier}")
        print(f"Category:      {cred.category}")
        print(f"Provider:      {cred.provider}")
        print(f"Status:        {cred.status}")
        if cred.description:
            print(f"Description:   {cred.description}")
        if cred.documentation:
            print(f"Docs:          {cred.documentation}")
        if cred.note:
            print(f"Note:          {cred.note}")

        if cred.locations:
            print(f"\nLocations:")
            for loc in cred.locations:
                primary = " (primary)" if loc.primary else ""
                print(f"  {loc.path}: ${loc.var_name}{primary}")
                if loc.note:
                    print(f"    Note: {loc.note}")

        if cred.used_by:
            print(f"\nUsed by:")
            for project in cred.used_by:
                print(f"  - {project}")

        if cred.extra:
            print(f"\nAdditional fields:")
            for k, v in cred.extra.items():
                print(f"  {k}: {v}")

        return 0

    def cmd_search(self, query: str) -> int:
        index = self._load_index()
        if index is None:
            self._print_bootstrap()
            return 1

        results = index.search(query)
        if not results:
            print(f"No credentials matching '{query}'.")
            return 0

        print(f"{'ID':<30} {'Name':<35} {'Tier':<10}")
        print("-" * 75)
        for c in results:
            name = c.name[:33] + ".." if len(c.name) > 35 else c.name
            print(f"{c.id:<30} {name:<35} {c.security_tier:<10}")
        print(f"\n{len(results)} result(s)")
        return 0

    def cmd_audit(self) -> int:
        index = self._load_index()
        if index is None:
            self._print_bootstrap()
            return 1

        result = AuditResult(total_credentials=len(index.credentials))

        for cred in index.credentials:
            result.checked += 1

            # Check 1: Invalid status
            if cred.status not in self.VALID_STATUSES:
                result.issues.append(AuditIssue(
                    severity="error",
                    credential_id=cred.id,
                    message=f"Invalid status '{cred.status}' (valid: {', '.join(sorted(self.VALID_STATUSES))})"
                ))

            # Check 2: Invalid tier
            if cred.security_tier not in self.VALID_TIERS:
                result.issues.append(AuditIssue(
                    severity="error",
                    credential_id=cred.id,
                    message=f"Invalid security tier '{cred.security_tier}' (valid: {', '.join(sorted(self.VALID_TIERS))})"
                ))

            # Check 3: Missing primary location file
            for loc in cred.locations:
                if not loc.primary:
                    continue
                loc_path = loc.path
                if loc_path.startswith("secrets-repo://"):
                    continue  # Can't check remote paths
                full_path = self.data_dir / loc_path
                if not full_path.exists():
                    result.issues.append(AuditIssue(
                        severity="warning",
                        credential_id=cred.id,
                        message=f"Primary location file missing: {loc_path}"
                    ))

            # Check 4: No locations or var_name
            #
            # `file_path` counts. A file-based credential — an ssh_key, a service
            # account JSON — has no environment variable by nature, and demanding
            # one flagged the single best-documented entry in the index
            # (plur-website-deploy-key, which carries file_path, public_key,
            # fingerprint, hosts and mirrored_to) as an error. The entry was right
            # and the rule was too narrow.
            has_location = (bool(cred.locations)
                            or cred.extra.get("var_name")
                            or cred.extra.get("vars")
                            or cred.extra.get("file_path"))
            if not has_location:
                result.issues.append(AuditIssue(
                    severity="error",
                    credential_id=cred.id,
                    message="No storage locations or var_name defined"
                ))

        # Check 5: Duplicate IDs
        seen_ids = {}
        for i, cred in enumerate(index.credentials):
            if cred.id in seen_ids:
                result.issues.append(AuditIssue(
                    severity="error",
                    credential_id=cred.id,
                    message=f"Duplicate credential ID (first at index {seen_ids[cred.id]})"
                ))
            seen_ids[cred.id] = i

        # Print results
        print(f"Credential Audit — {result.total_credentials} credential(s) checked")
        print("=" * 60)

        if not result.issues:
            print("All checks passed.")
            return 0

        for issue in sorted(result.issues, key=lambda i: (
            {"error": 0, "warning": 1, "info": 2}[i.severity], i.credential_id
        )):
            icon = {"error": "E", "warning": "W", "info": "I"}[issue.severity]
            print(f"[{icon}] {issue.credential_id}: {issue.message}")

        print(f"\nSummary: {len(result.errors)} error(s), "
              f"{len(result.warnings)} warning(s), "
              f"{len([i for i in result.issues if i.severity == 'info'])} info")
        return 1 if result.errors else 0

    def cmd_rotate(self, credential: str = None, all_creds: bool = False,
                   bootstrap: bool = False, fmt: str = "text") -> int:
        rotation_path = self.data_dir / ".datacore" / "state" / "credential-index.yaml"
        rotation = RotationIndex(rotation_path)

        # Bootstrap from .env if requested or index is empty
        env_path = self.data_dir / ".datacore" / "env" / ".env"
        if bootstrap or (not rotation.entries and env_path.exists()):
            if env_path.exists():
                count = self._bootstrap_rotation(rotation, env_path)
                print(f"Bootstrapped {count} credential(s) from {env_path}")
                rotation.save()
                if bootstrap:
                    return 0
            else:
                print(f"No .env file found at {env_path}")
                return 1

        if not rotation.entries:
            print("No credentials in rotation index.")
            print(f"Run with --bootstrap to scan {env_path}")
            return 1

        # Filter entries
        if credential:
            entries = [e for e in rotation.entries if
                       credential.lower() in e.env_var.lower() or
                       credential.lower() in e.credential.lower()]
            if not entries:
                print(f"No credential matching '{credential}'.")
                return 1
        elif all_creds:
            entries = rotation.entries
        else:
            entries = rotation.stale()
            if not entries:
                print("All credentials are within rotation window. Nothing to rotate.")
                self._print_rotation_summary(rotation)
                return 0

        # JSON output — non-interactive, just dump the entries
        if fmt == "json":
            output = {
                "total": len(rotation.entries),
                "credentials": [
                    {
                        "env_var": e.env_var,
                        "provider": e.provider,
                        "provider_url": e.provider_url,
                        "status": e.status,
                        "rotated_at": e.rotated_at,
                        "next_due": e.next_rotation or "unknown",
                    }
                    for e in entries
                ],
            }
            print(json.dumps(output, indent=2))
            return 0

        # Display rotation checklist
        print(f"Credential Rotation Checklist")
        print(f"{'=' * 58}")
        print(f"{len(entries)} credential(s) to review:\n")

        for i, entry in enumerate(entries, 1):
            age = self._rotation_age(entry)
            flag = " ** OVERDUE" if age and age > ROTATION_DAYS else ""
            print(f"  {i}. {entry.env_var}")
            print(f"     Provider:  {entry.provider} ({entry.provider_url})")
            print(f"     Status:    {entry.status}")
            print(f"     Last rot:  {entry.rotated_at or 'never'}{flag}")
            print(f"     Next due:  {entry.next_rotation or 'unknown'}")
            print()

        # Interactive confirmation
        print("Rotation steps for each credential:")
        print("  1. Visit the provider URL above")
        print("  2. Generate a new key/token")
        print("  3. Update the value in .datacore/env/.env")
        print("  4. Deploy updated env to any servers")
        print("  5. Confirm rotation below")
        print()

        try:
            answer = input("Enter credential numbers to mark as rotated "
                           "(comma-separated, or 'q' to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 0

        if answer.lower() in ("q", "quit", ""):
            print("No changes made.")
            return 0

        now = datetime.now(timezone.utc)
        rotated_count = 0
        for part in answer.split(","):
            part = part.strip()
            if not part.isdigit():
                continue
            idx = int(part) - 1
            if 0 <= idx < len(entries):
                entry = entries[idx]
                entry.rotated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                entry.rotated_by = "manual"
                entry.next_rotation = (now + timedelta(days=ROTATION_DAYS)).strftime("%Y-%m-%d")
                entry.status = "active"
                rotation.upsert(entry)
                rotated_count += 1
                print(f"  Marked '{entry.env_var}' as rotated.")

        if rotated_count:
            rotation.save()
            print(f"\nUpdated {rotated_count} credential(s) in {rotation_path}")
        else:
            print("No credentials were marked as rotated.")
        return 0


    # ---- Broker + liveness (DIP-0018 rotating-credential extension) --------

    def _access(self):
        """The single resolver. Imported lazily so the rest of the CLI still
        works on a host where the module has not been deployed yet."""
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        import credential_access as ca  # noqa: PLC0415
        return ca

    def cmd_get(self, cred_id: str, consumer: str = "cli",
                no_verify: bool = False) -> int:
        """Serve a currently-valid credential value. THE BROKER.

        Holds an exclusive lock for the credential id for the whole operation.
        That lock is the entire point: a rotating credential's refresh token is
        SINGLE-USE, so two processes refreshing concurrently do not both get a
        fresh value — the loser invalidates the winner's. On 2026-08-17 that
        produced an empty access token in the CLI store while four env copies
        held a superseded one, and the machine that could refresh had lost.

        It prints the VALUE on stdout (that is the point of a broker) and
        everything else on stderr, so `X=$(creds get id)` is safe.
        """
        import fcntl  # noqa: PLC0415
        ca = self._access()

        lockdir = Path.home() / ".datacore" / "locks"
        lockdir.mkdir(parents=True, exist_ok=True)
        lock = lockdir / f"cred-{cred_id.replace('/', '_')}.lock"
        fh = open(lock, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                value = ca.get_value(cred_id, consumer=consumer)
            except (ca.CredentialNotIndexed, ca.CredentialUnresolvable) as exc:
                print(f"{exc}", file=sys.stderr)
                return 1

            entry = ca._entry(cred_id)
            var = entry.get("var_name") or (entry.get("vars") or [cred_id])[0]

            if not no_verify:
                state, detail = ca.verify_value(var, value)
                if state == "FAIL":
                    # Refusing to serve a value proven dead is the difference
                    # between this and reading the file yourself. A dead value
                    # served silently is what sends someone to rotate a
                    # credential that was fine.
                    print(f"{cred_id}: value is DEAD ({detail}). Not served.",
                          file=sys.stderr)
                    if entry.get("lifecycle") == "rotating":
                        owner = entry.get("owner", "unresolved")
                        mint = entry.get("mint_host", "unknown")
                        print(f"  rotating: owner={owner} mint_host={mint} — "
                              f"renew there, not here.", file=sys.stderr)
                    return 1
                if state == "n-a":
                    print(f"{cred_id}: served WITHOUT verification ({detail})",
                          file=sys.stderr)
            print(value)
            return 0
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def cmd_doctor(self, cred_id: str = None) -> int:
        """Liveness for every indexed credential. ok / FAIL / n-a.

        Replaces shape-checking. `oauth_health_check` returned exit 0 and
        "no expiresAt (long-lived token?)" against a credential that could not
        authenticate — because it read the file rather than asking the provider.
        n-a is reported and never counted as a pass.
        """
        ca = self._access()
        index = self._load_index()
        creds = [c for c in index.credentials
                 if not cred_id or c.id == cred_id]
        if not creds:
            print(f"no credential matching {cred_id!r}")
            return 2

        ok = fail = na = 0
        rows = []
        for c in creds:
            var = c.extra.get("var_name") or (c.extra.get("vars") or [None])[0]
            if not var:
                rows.append(("n-a", c.id, "file-based credential; no env var to probe"))
                na += 1
                continue
            try:
                value = ca.get_value(c.id, consumer="creds-doctor")
            except Exception as exc:  # noqa: BLE001
                scoped = ca.in_scope(c.extra if isinstance(c.extra, dict) else {})
                if scoped is False:
                    rows.append(("n-a", c.id,
                                 f"not scoped to instance '{ca.instance_name()}'"))
                    na += 1
                else:
                    rows.append(("FAIL", c.id, f"unreadable: {str(exc)[:70]}"))
                    fail += 1
                continue
            state, detail = ca.verify_value(
                var, value, entry=c.extra if isinstance(c.extra, dict) else {})
            if state == "FAIL":
                warn = ca.replication_warning(
                    {**(c.extra if isinstance(c.extra, dict) else {}), "id": c.id})
                if warn:
                    detail = f"{detail[:60]} — {warn}"
            rows.append((state, c.id, detail))
            if state == "ok":
                ok += 1
            elif state == "FAIL":
                fail += 1
            else:
                na += 1

        for state, cid, detail in sorted(rows, key=lambda r: {"FAIL": 0, "n-a": 1, "ok": 2}[r[0]]):
            print(f"  {state:5} {cid:32} {detail[:70]}")
        print(f"\n  ok {ok}   FAIL {fail}   n-a {na}"
              f"   ({na} could not be determined — not counted as passing)")
        return 1 if fail else 0


    def cmd_adopt_token(self, cred_id: str = "claude-code-oauth") -> int:
        """Install a freshly minted token into its declared store. ONE place.

        Reads from STDIN only — never argv, which would put the value in shell
        history and in `ps` output for every user on the box.

        CLEARS any existing refreshToken and expiresAt. That is the important
        part, not the write: a `setup-token` value is long-lived and is NOT part
        of the previous credential's refresh pair. Leaving the old refresh token
        beside it invites the CLI to renew the new token using the old chain —
        and a single-use refresh chain that two hosts share is precisely what
        revoked winston's credential twice today.

        Writes NOTHING else. No env copies, no propagation. Four derived copies
        were removed on 2026-08-18 because a copy cannot refresh itself and is
        therefore a countdown; re-creating one here would undo that.
        """
        import json as _json  # noqa: PLC0415
        ca = self._access()

        if sys.stdin.isatty():
            print("  paste the token from `claude setup-token`, then Ctrl-D:",
                  file=sys.stderr)
        token = sys.stdin.read().strip()
        if not token:
            print("no token on stdin", file=sys.stderr)
            return 2
        if not re.fullmatch(r"[A-Za-z0-9._\-]+", token):
            print("that does not look like a token (unexpected characters)",
                  file=sys.stderr)
            return 2

        try:
            entry = ca._entry(cred_id)
        except Exception as exc:  # noqa: BLE001
            print(f"{exc}", file=sys.stderr)
            return 1

        store = ca._store_for(entry)
        if not store.startswith("json:"):
            print(f"{cred_id} declares storage {store!r}; adopt-token only "
                  f"handles a json: store", file=sys.stderr)
            return 1
        path = Path(store.split(":", 1)[1]).expanduser()

        try:
            cur = _json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            cur = {}
        blk = cur.setdefault("claudeAiOauth", {})
        old = blk.get("accessToken", "")
        blk["accessToken"] = token
        blk.pop("refreshToken", None)      # see docstring — this is the point
        blk.pop("expiresAt", None)
        blk.setdefault("scopes", ["user:inference", "user:profile"])

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(cur))
        os.chmod(path, 0o600)
        print(f"  wrote {ca.fingerprint(old)} -> {ca.fingerprint(token)}  {path}",
              file=sys.stderr)
        print("  cleared refreshToken/expiresAt — a long-lived token has no "
              "refresh pair", file=sys.stderr)

        state, detail = ca.verify_value("CLAUDE_CODE_OAUTH_TOKEN", token)
        print(f"  live check: {state} — {detail}", file=sys.stderr)
        return 0 if state == "ok" else 1

    def cmd_sync(self, instance: str = None) -> int:
        """Run sync.sh from the secrets repo."""
        secrets_dir = self.data_dir / ".datacore" / "secrets"
        sync_script = secrets_dir / "scripts" / "sync.sh"

        if not secrets_dir.exists():
            print("Secrets repo not found at .datacore/secrets/")
            print("Bootstrap with: git clone gregor@blackpi.local:~/secrets.git .datacore/secrets")
            return 1

        if not sync_script.exists():
            print("sync.sh not found. Is the secrets repo properly initialized?")
            return 1

        import subprocess
        cmd = [str(sync_script)]
        if instance:
            cmd.append(instance)

        result = subprocess.run(cmd, cwd=str(secrets_dir))
        return result.returncode

    def _bootstrap_rotation(self, rotation: RotationIndex, env_path: Path) -> int:
        """Parse .env and create rotation entries from known patterns."""
        count = 0
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                match = re.match(r'^([A-Z_][A-Z0-9_]*)=', line)
                if not match:
                    continue
                var_name = match.group(1)
                # Skip non-secret config values
                if var_name in ("DO_DROPLET_ID", "DO_DROPLET_IP", "DO_SSH_KEY_ID",
                                "POSTHOG_PROJECT_ID", "ENGAGEMENT_CHAT_ID",
                                "DATA_ESCROW_ADDRESS_SEPOLIA", "MIDJOURNEY_CHANNEL_ID"):
                    continue
                provider, url = self._detect_provider(var_name)
                existing = rotation.get(var_name)
                if existing:
                    continue
                rotation.upsert(RotationEntry(
                    provider=provider,
                    provider_url=url,
                    env_var=var_name,
                    status="active",
                ))
                count += 1
        return count

    def _detect_provider(self, var_name: str) -> tuple:
        """Match env var name to a known provider."""
        for prefix, (name, url) in PROVIDER_PATTERNS.items():
            if var_name.startswith(prefix):
                return name, url
        if "_PRIVATE_KEY" in var_name or "_SECRET" in var_name:
            return "self-managed", ""
        return "unknown", ""

    def _rotation_age(self, entry: RotationEntry) -> Optional[int]:
        if not entry.rotated_at:
            return None
        try:
            rotated = datetime.fromisoformat(entry.rotated_at.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - rotated).days
        except (ValueError, AttributeError):
            return None

    def _print_rotation_summary(self, rotation: RotationIndex):
        print(f"\n{'Env Var':<40} {'Last Rotated':<22} {'Next Due':<12}")
        print("-" * 74)
        for e in rotation.entries:
            rotated = e.rotated_at[:10] if e.rotated_at else "never"
            due = e.next_rotation or "unknown"
            print(f"{e.env_var:<40} {rotated:<22} {due:<12}")
        print(f"\n{len(rotation.entries)} credential(s) tracked")

    def _cred_to_dict(self, c: Credential) -> dict:
        d = {
            "id": c.id, "name": c.name, "type": c.type,
            "security_tier": c.security_tier, "category": c.category,
            "provider": c.provider, "status": c.status,
            "description": c.description,
            "locations": [
                {"path": l.path, "var_name": l.var_name,
                 "primary": l.primary, "note": l.note}
                for l in c.locations
            ],
            "used_by": c.used_by,
        }
        if c.documentation:
            d["documentation"] = c.documentation
        if c.note:
            d["note"] = c.note
        if c.extra:
            d.update(c.extra)
        return d

    def cmd_add(self, cred_id: str, var_name: str = None, value: str = None,
                scope: str = None, space: str = None, project: str = None,
                tier: str = None, category: str = None, provider: str = None,
                description: str = None) -> int:
        """Add a credential to the secrets repo and index."""
        secrets_dir = self.data_dir / ".datacore" / "secrets"
        if not secrets_dir.exists():
            print("Secrets repo not found at .datacore/secrets/")
            return 1

        # Interactive prompts for missing fields
        if not var_name:
            var_name = input("Environment variable name (e.g. MY_API_KEY): ").strip()
            if not var_name:
                print("Variable name is required.")
                return 1

        if not value:
            value = input(f"Value for {var_name}: ").strip()
            if not value:
                print("Value is required.")
                return 1

        if not scope:
            print("\nScope:")
            print("  1. global  — all instances get this")
            print("  2. space   — only instances working in a specific space")
            print("  3. project — project-specific credential")
            choice = input("Select scope (1/2/3): ").strip()
            scope = {"1": "global", "2": "space", "3": "project"}.get(choice)
            if not scope:
                print("Invalid scope.")
                return 1

        if scope == "space" and not space:
            spaces = sorted([f.stem for f in (secrets_dir / "spaces").glob("*.env")])
            print("\nAvailable spaces:")
            for i, s in enumerate(spaces, 1):
                print(f"  {i}. {s}")
            choice = input("Select space number: ").strip()
            try:
                space = spaces[int(choice) - 1]
            except (ValueError, IndexError):
                print("Invalid selection.")
                return 1

        if scope == "project" and not project:
            project = input("Project path (e.g. 3-fds/fairdrop): ").strip()
            if not project:
                print("Project path is required.")
                return 1

        if not tier:
            tier = input("Security tier (critical/high/medium/low) [medium]: ").strip() or "medium"
        if not category:
            category = input("Category (e.g. ai-services, trading, social): ").strip() or "general"
        if not provider:
            provider = input("Provider (e.g. anthropic, gateio): ").strip() or "unknown"
        if not description:
            description = input("Description: ").strip() or ""

        # Determine target env file
        if scope == "global":
            env_file = secrets_dir / "global.env"
        elif scope == "space":
            env_file = secrets_dir / "spaces" / f"{space}.env"
        elif scope == "project":
            env_file = secrets_dir / "projects" / f"{project}.env"
            env_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            print(f"Unknown scope: {scope}")
            return 1

        if not env_file.exists():
            print(f"Target file does not exist: {env_file}")
            return 1

        # Append to env file
        with open(env_file, "a") as f:
            f.write(f"\n{var_name}={value}\n")
        print(f"Added {var_name} to {env_file.relative_to(secrets_dir)}")

        # Add to credential index
        index_file = secrets_dir / "credential-index.yaml"
        if index_file.exists():
            with open(index_file) as f:
                index_data = yaml.safe_load(f) or {}
            entry = {
                "id": cred_id,
                "name": description or cred_id,
                "type": "api_key",
                "tier": tier,
                "scope": scope,
                "category": category,
                "provider": provider,
                "var_name": var_name,
                "description": description,
            }
            if scope == "space":
                entry["space"] = space
            if scope == "project":
                entry["project"] = project
            index_data.setdefault("credentials", []).append(entry)
            with open(index_file, "w") as f:
                yaml.dump(index_data, f, default_flow_style=False, sort_keys=False)
            # Also update the specs copy
            specs_index = self.data_dir / ".datacore" / "specs" / "credential-index.yaml"
            if specs_index.exists():
                import shutil
                shutil.copy2(index_file, specs_index)
            print(f"Added to credential index")

        # Commit in secrets repo
        import subprocess
        subprocess.run(["git", "add", "-A"], cwd=str(secrets_dir), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"feat: add credential {cred_id}"],
            cwd=str(secrets_dir), capture_output=True
        )
        print(f"Committed to secrets repo")
        print(f"\nNext: run 'creds sync' to assemble, then push to BlackPi")
        return 0

    def _print_bootstrap(self):
        print("Credential index not found.")
        print(f"\nTo set up, copy the example template:")
        print(f"  cp {self.example_path} {self.index_path}")
        print(f"\nThen edit {self.index_path} with your credentials.")
        print(f"See DIP-0018 for schema documentation.")


def main():
    parser = argparse.ArgumentParser(
        description="Credential Manager — query and audit the credential index (DIP-0018)")
    parser.add_argument("--data-dir", help="Datacore root directory")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list
    list_p = subparsers.add_parser("list", help="List credentials")
    list_p.add_argument("--category", help="Filter by category")
    list_p.add_argument("--tier", help="Filter by security tier")
    list_p.add_argument("--format", dest="fmt", default="table",
                        choices=["table", "json"], help="Output format")

    # show
    show_p = subparsers.add_parser("show", help="Show credential details")
    show_p.add_argument("id", help="Credential ID")

    # search
    search_p = subparsers.add_parser("search", help="Search credentials")
    search_p.add_argument("query", help="Search query")

    # audit
    subparsers.add_parser("audit", help="Audit credential index")

    # rotate
    rotate_p = subparsers.add_parser("rotate", help="Guided credential rotation checklist")
    rotate_p.add_argument("--credential", help="Filter to specific credential name or env var")
    rotate_p.add_argument("--all", dest="all_creds", action="store_true",
                          help="Show all credentials, not just overdue")
    rotate_p.add_argument("--bootstrap", action="store_true",
                          help="Bootstrap rotation index from .datacore/env/.env")
    rotate_p.add_argument("--format", dest="fmt", default="text",
                          choices=["text", "json"], help="Output format")

    # add
    add_p = subparsers.add_parser("add", help="Add a credential to the secrets repo")
    add_p.add_argument("id", help="Credential ID (e.g. my-api-key)")
    add_p.add_argument("--var", dest="var_name", help="Environment variable name")
    add_p.add_argument("--value", help="Credential value")
    add_p.add_argument("--scope", choices=["global", "space", "project"], help="Scope")
    add_p.add_argument("--space", help="Space name (for scope=space)")
    add_p.add_argument("--project", help="Project path (for scope=project)")
    add_p.add_argument("--tier", choices=["critical", "high", "medium", "low"], help="Security tier")
    add_p.add_argument("--category", help="Category")
    add_p.add_argument("--provider", help="Provider name")
    add_p.add_argument("--description", help="Description")

    # sync
    get_p = subparsers.add_parser("get", help="Serve a verified credential value (broker)")
    get_p.add_argument("credential")
    get_p.add_argument("--consumer", default="cli", help="who is asking — recorded in the ledger")
    get_p.add_argument("--no-verify", action="store_true", help="skip the liveness call")

    adopt_p = subparsers.add_parser("adopt-token",
        help="Install a freshly minted token from stdin into its declared store")
    adopt_p.add_argument("--id", dest="adopt_id", default="claude-code-oauth")

    doctor_p = subparsers.add_parser("doctor", help="Liveness for every credential (ok/FAIL/n-a)")
    doctor_p.add_argument("--id", dest="doctor_id", default=None)

    sync_p = subparsers.add_parser("sync", help="Sync credentials from central repo")
    sync_p.add_argument("--instance", help="Instance name (auto-detected if not given)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    mgr = CredentialManager(data_dir=args.data_dir)

    if args.command == "list":
        return mgr.cmd_list(category=args.category, tier=args.tier, fmt=args.fmt)
    elif args.command == "show":
        return mgr.cmd_show(args.id)
    elif args.command == "search":
        return mgr.cmd_search(args.query)
    elif args.command == "audit":
        return mgr.cmd_audit()
    elif args.command == "rotate":
        return mgr.cmd_rotate(credential=args.credential,
                              all_creds=args.all_creds,
                              bootstrap=args.bootstrap,
                              fmt=args.fmt)
    elif args.command == "add":
        return mgr.cmd_add(
            cred_id=args.id, var_name=args.var_name, value=args.value,
            scope=args.scope, space=args.space, project=args.project,
            tier=args.tier, category=args.category, provider=args.provider,
            description=args.description)
    elif args.command == "get":
        return mgr.cmd_get(args.credential, consumer=args.consumer,
                           no_verify=args.no_verify)
    elif args.command == "adopt-token":
        return mgr.cmd_adopt_token(args.adopt_id)
    elif args.command == "doctor":
        return mgr.cmd_doctor(args.doctor_id)
    elif args.command == "sync":
        return mgr.cmd_sync(instance=getattr(args, 'instance', None))
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
