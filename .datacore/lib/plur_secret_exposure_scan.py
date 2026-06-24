#!/usr/bin/env python3
"""Scan a PLUR store dir for secret-bearing files that are git-tracked / in history.

Reports which sensitive files (config.yaml with remote-store Bearer tokens,
agent-keystore.json with keys) are committed to the store's git repo and pushed
to its remote — i.e. exposed beyond the local machine. Token/key VALUES are
masked; only enough fingerprint to identify-and-rotate is printed.

Re-run after a rotation + history scrub to verify the exposure is closed.

Usage: python3 plur_secret_exposure_scan.py [PLUR_DIR]   (default: ~/.plur)
"""
import base64
import json
import subprocess
import sys
from pathlib import Path


SENSITIVE = ("config.yaml", "agent-keystore.json", "secrets.yaml")


def mask(s: str) -> str:
    s = str(s)
    if len(s) <= 12:
        return f"<{len(s)} chars>"
    return f"{s[:6]}…{s[-4:]} (len {len(s)})"


def jwt_claims(tok: str):
    """Decode a JWT payload (NOT the signature) for iss/exp/scope. Returns {} if not a JWT."""
    try:
        parts = tok.split(".")
        if len(parts) != 3:
            return {}
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(pad))
    except Exception:
        return {}


def git(plur: Path, *args) -> str:
    try:
        return subprocess.run(["git", "-C", str(plur), *args],
                              capture_output=True, text=True).stdout.strip()
    except Exception as e:
        return f"<git error: {e}>"


def main() -> None:
    plur = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / ".plur"
    print(f"PLUR store: {plur}")
    print(f"remote: {git(plur, 'remote', 'get-url', 'origin') or '<none>'}")
    tracked = set(git(plur, "ls-files").splitlines())
    print("=" * 64)

    for name in SENSITIVE:
        f = plur / name
        if not f.exists() and name not in tracked:
            continue
        in_hist = git(plur, "log", "--oneline", "--all", "--", name)
        ncommits = len(in_hist.splitlines()) if in_hist else 0
        flag = "🔴 EXPOSED" if (name in tracked or ncommits) else "🟢 clean"
        print(f"\n{flag}  {name}")
        print(f"   tracked-now: {name in tracked} | commits-in-history: {ncommits}")

        if not f.exists():
            print("   (file absent locally — only in history)")
            continue

        if name == "config.yaml":
            import yaml
            try:
                cfg = yaml.safe_load(f.read_text()) or {}
            except Exception as e:
                print(f"   <parse error: {e}>")
                continue
            stores = cfg.get("stores", []) or []
            for st in stores:
                tok = st.get("token") or st.get("auth") or st.get("bearer")
                if not tok:
                    continue
                claims = jwt_claims(tok)
                exp = claims.get("exp")
                print(f"   • store scope={st.get('scope','?')} url={st.get('url','?')}")
                print(f"       token: {mask(tok)}"
                      + (f" | jwt iss={claims.get('iss')} sub={claims.get('sub')} exp={exp}" if claims else " | opaque (non-JWT)"))
        elif name == "agent-keystore.json":
            try:
                ks = json.loads(f.read_text())
            except Exception as e:
                print(f"   <parse error: {e}>")
                continue
            def walk(obj, path=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        kl = k.lower()
                        if any(s in kl for s in ("private", "secret", "seed", "mnemonic", "key")) and isinstance(v, str):
                            print(f"   • {path}{k}: {mask(v)}  ⚠ private material")
                        elif kl in ("address", "pubkey", "public_key", "id", "did", "name"):
                            print(f"   • {path}{k}: {v}")
                        else:
                            walk(v, path + k + ".")
                elif isinstance(obj, list):
                    for i, v in enumerate(obj):
                        walk(v, f"{path}[{i}].")
            walk(ks)
    print("\n" + "=" * 64)
    print("Action if any 🔴: rotate the listed tokens/keys at their issuer, then scrub")
    print("history (all refs + tags) and force-push, then re-run this scan to confirm 🟢.")


if __name__ == "__main__":
    main()
