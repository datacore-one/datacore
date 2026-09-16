#!/usr/bin/env python3
"""Collect writers' PUBLIC verify keys from their hosts into principals.yaml.

principals.yaml has told readers to "re-collect with ledger_keys_collect.py
after a rotation" since 2026-09-06, and no such script existed -- so the four
keys there were whatever was collected by hand that day, and three writers
that had been signing since (nightshift, tris, ceo) were never added. 493 of
the ledger's 4,377 signatures could not be checked by anything, which is
exactly the gap S10 names: a contract claiming a boundary the keys do not
actually establish.

WHY A KEY IS PROVEN, NOT JUST READ
    `ensure_keypair` GENERATES a key when the file is absent, so a host ends up
    holding a key for every actor it ever instantiated an EventLog for --
    including actors that are not it. Measured 2026-09-16: nightshift held its
    own `genesis`, `mac` and `winston` keys, none of which match the identities
    those writers actually sign with. Reading a key off a host is therefore no
    evidence of whose it is.

    So a candidate is accepted only when it verifies that actor's real signed
    events in the ledger. An actor with signatures that the candidate cannot
    verify is reported and NOT written.

PRIVATE KEYS NEVER MOVE. The derivation runs on the owning host and only the
public half is printed; nothing here reads, copies or transmits a private key.
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from ledger.events import body_dict, canonical_bytes  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PRINCIPALS = ROOT / ".datacore" / "registry" / "principals.yaml"

# Derives the public half of every key on the host and prints "<actor> <hex>".
# Nothing else leaves the machine.
_DERIVE = (
    "python3 -c '"
    "import glob, os\n"
    "from pathlib import Path\n"
    "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey\n"
    "for p in sorted(glob.glob(os.path.expanduser(\"~/.datacore/keys/*.key\"))):\n"
    "    try:\n"
    "        k = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(Path(p).read_text().strip()))\n"
    "        print(os.path.basename(p)[:-4], k.public_key().public_bytes_raw().hex())\n"
    "    except Exception:\n"
    "        pass\n"
    "'"
)


def candidates(hosts: list[str]) -> dict[str, dict[str, str]]:
    """{actor: {host: verify_key_hex}} -- every key each host holds."""
    found: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for host in hosts:
        try:
            out = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", host, _DERIVE],
                capture_output=True, text=True, timeout=60,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"  {host}: unreachable ({type(exc).__name__})", file=sys.stderr)
            continue
        if out.returncode != 0:
            print(f"  {host}: derivation failed rc={out.returncode}", file=sys.stderr)
            continue
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and len(parts[1]) == 64:
                found[parts[0]][host] = parts[1]
    return found


def signatures(root: Path) -> dict[str, list[tuple[dict, str]]]:
    """{actor: [(body, sig_hex)]} for every signed event in every space."""
    sigs: dict[str, list[tuple[dict, str]]] = collections.defaultdict(list)
    for path in sorted(root.glob("[0-9]-*/.datacore/events/*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not event.get("sig"):
                continue
            body = body_dict(event["seq"], event["hlc"], event["actor"],
                             event["type"], event["payload"], event["prev"])
            sigs[event["actor"]].append((body, event["sig"]))
    return sigs


def proves(verify_key_hex: str, evidence: list[tuple[dict, str]]) -> tuple[int, int]:
    """(verified, failed) for this key over that actor's signed events."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(verify_key_hex))
    except (TypeError, ValueError):
        return 0, len(evidence)
    ok = bad = 0
    for body, sig in evidence:
        try:
            public.verify(bytes.fromhex(sig), canonical_bytes(body))
            ok += 1
        except (TypeError, ValueError, InvalidSignature):
            bad += 1
    return ok, bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hosts", default="winston,nightshift,hermes,plur-claw",
                    help="comma-separated ssh targets to collect from")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--apply", action="store_true",
                    help="write proven keys into principals.yaml (default: report only)")
    args = ap.parse_args()

    doc = yaml.safe_load(PRINCIPALS.read_text(encoding="utf-8")) or {}
    registered = dict(doc.get("verify_keys") or {})

    print("collecting public keys...", file=sys.stderr)
    held = candidates([h.strip() for h in args.hosts.split(",") if h.strip()])
    evidence = signatures(args.root)

    proven: dict[str, str] = {}
    print(f"\n{'actor':<14}{'verifies':>9}{'fails':>7}  status")
    for actor in sorted(set(held) | set(evidence)):
        signed = evidence.get(actor, [])
        if not signed:
            print(f"{actor:<14}{'-':>9}{'-':>7}  never signed; nothing to prove")
            continue
        scored = {h: proves(k, signed) for h, k in held.get(actor, {}).items()}
        winners = {h: held[actor][h] for h, (ok, bad) in scored.items() if bad == 0 and ok > 0}
        distinct = set(winners.values())
        if not distinct:
            print(f"{actor:<14}{0:>9}{len(signed):>7}  NO KEY PROVES {len(signed)} signature(s)")
            continue
        if len(distinct) > 1:
            print(f"{actor:<14}{'-':>9}{'-':>7}  AMBIGUOUS: {len(distinct)} keys verify; refusing")
            continue
        key = distinct.pop()
        ok, bad = proves(key, signed)
        state = "already registered" if registered.get(actor) == key else (
            "REPLACES a different registered key" if actor in registered else "new")
        print(f"{actor:<14}{ok:>9}{bad:>7}  {state} (from {','.join(sorted(winners))})")
        proven[actor] = key

    stray = {a: hs for a, hs in held.items() if len(set(hs.values())) > 1}
    if stray:
        print("\nDIVERGENT key material (same actor, different key per host):")
        for actor, by_host in sorted(stray.items()):
            for host, key in sorted(by_host.items()):
                mark = "  <- registered" if registered.get(actor) == key else ""
                print(f"  {actor:<12} {host:<12} {key[:16]}...{mark}")
        print("  A host generates a key for any actor it opens an EventLog for.")
        print("  Only the proven one above is that writer's identity.")

    changed = {a: k for a, k in proven.items() if registered.get(a) != k}
    if not changed:
        print("\nprincipals.yaml already carries every proven key.")
        return 0
    print(f"\n{len(changed)} key(s) to write: {', '.join(sorted(changed))}")
    if not args.apply:
        print("re-run with --apply to write them.")
        return 0

    registered.update(changed)
    lines = PRINCIPALS.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.startswith("verify_keys:"))
    # Everything after the block's indented entries is someone else's section.
    # Rewriting to end-of-file would delete it.
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith((" ", "\t"))):
        end += 1
    block = ["verify_keys:\n"] + [f"  {a}: {registered[a]}\n" for a in sorted(registered)]
    PRINCIPALS.write_text("".join(lines[:start] + block + lines[end:]), encoding="utf-8")
    print(f"wrote {PRINCIPALS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
