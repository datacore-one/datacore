#!/usr/bin/env python3
"""
assets_sync — rsync-based heavy-asset mirror for Datacore spaces.

Replaces git-lfs for files that don't need versioning (telegram exports,
archived PDFs, video, static brand assets, etc).

Layout
------
  local:     ~/Data/<space>/<path>/...
  remote:    nightshift:~/assets/<space>/<path>/...
  manifest:  ~/Data/<space>/assets.manifest   (committed to git)

The manifest is a plain text file listing every path under the mirror
with its size + sha256. It lets git show you *what exists* even when
the bytes are only on the server.

Usage
-----
  assets_sync.py push <space> [path ...]   # local -> remote
  assets_sync.py pull <space> [path ...]   # remote -> local
  assets_sync.py status <space>            # diff local vs remote
  assets_sync.py manifest <space>          # rebuild manifest from remote
  assets_sync.py evict <space> <path>      # delete local copy (after verify)

Config: ~/.config/datacore/assets_sync.yaml  (optional)
  remote_host: my-server     # else the roster's roles.assets machine, by ssh alias
  remote_root: assets
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

CONFIG = Path.home() / ".config" / "datacore" / "assets_sync.yaml"


def _config() -> dict:
    try:
        import yaml
        data = yaml.safe_load(CONFIG.read_text())
    except Exception:  # noqa: BLE001 -- absent or unreadable: defaults
        return {}
    return data if isinstance(data, dict) else {}


def _remote_host() -> str:
    """The mirror host: the config's remote_host, else the machine holding
    roles.assets in the install's roster, by ssh alias -- never a host of
    ours written here (INS-3). "" when neither says."""
    if _config().get("remote_host"):
        return str(_config()["remote_host"])
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from jobs.manifest import role, ssh_alias
        machine = role("assets")
        return (ssh_alias(machine) or "") if machine else ""
    except Exception:  # noqa: BLE001
        return ""


REMOTE_HOST = _remote_host()
REMOTE_ROOT = str(_config().get("remote_root") or "assets")  # expanded to ~/assets on remote
DATA_ROOT = Path.home() / "Data"


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True,
                          capture_output=capture)


def space_dir(space: str) -> Path:
    p = DATA_ROOT / space
    if not p.exists():
        sys.exit(f"error: space not found: {p}")
    return p


def remote_path(space: str, sub: str = "") -> str:
    base = f"{REMOTE_HOST}:{REMOTE_ROOT}/{space}"
    return f"{base}/{sub}" if sub else base + "/"


def ensure_remote_dir(space: str) -> None:
    run(["ssh", REMOTE_HOST, f"mkdir -p {REMOTE_ROOT}/{space}"])


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: Path) -> list[str]:
    lines = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            rel = p.relative_to(root)
            size = p.stat().st_size
            lines.append(f"{size}\t{rel}")
    return lines


def write_manifest(space: str, paths: list[str]) -> None:
    """Write manifest of all paths that have been evicted to remote."""
    sd = space_dir(space)
    mf = sd / ".datacore" / "assets.manifest"
    mf.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if mf.exists():
        existing = set(mf.read_text().splitlines())
    existing.update(paths)
    mf.write_text("\n".join(sorted(existing)) + "\n")
    print(f"manifest updated: {mf} ({len(existing)} entries)")


def cmd_push(space: str, paths: list[str]) -> None:
    sd = space_dir(space)
    ensure_remote_dir(space)
    paths = paths or ["."]
    for p in paths:
        src = sd / p
        if not src.exists():
            print(f"skip (missing): {src}")
            continue
        src_str = str(src).rstrip("/") + ("/" if src.is_dir() else "")
        dst = remote_path(space, p.rstrip("/") + ("/" if src.is_dir() else ""))
        # ensure remote parent dir exists (rsync --mkpath not on all hosts)
        parent = str(Path(p).parent)
        if parent and parent != ".":
            run(["ssh", REMOTE_HOST,
                 f"mkdir -p {REMOTE_ROOT}/{space}/{parent}"])
        print(f"push: {src_str} -> {dst}")
        run(["rsync", "-avh", "--progress", "--partial",
             src_str, dst])


def cmd_pull(space: str, paths: list[str]) -> None:
    sd = space_dir(space)
    paths = paths or ["."]
    for p in paths:
        dst = sd / p
        dst.parent.mkdir(parents=True, exist_ok=True)
        src = remote_path(space, p.rstrip("/") + "/")
        print(f"pull: {src} -> {dst}/")
        run(["rsync", "-avh", "--progress", "--partial",
             src, str(dst).rstrip("/") + "/"])


def cmd_status(space: str) -> None:
    ensure_remote_dir(space)
    sd = space_dir(space)
    print(f"=== local  {sd}")
    run(["du", "-sh", str(sd)])
    print(f"=== remote {remote_path(space)}")
    run(["ssh", REMOTE_HOST, f"du -sh {REMOTE_ROOT}/{space} 2>/dev/null || echo 'empty'"])
    print("=== diff (dry-run, local -> remote)")
    run(["rsync", "-avnh", "--delete",
         str(sd) + "/", remote_path(space)], check=False)


def cmd_evict(space: str, path: str) -> None:
    """Delete local copy after verifying remote has it."""
    sd = space_dir(space)
    src = sd / path
    if not src.exists():
        sys.exit(f"error: local path missing: {src}")
    # verify remote exists and file counts + total sizes match
    print(f"verifying remote copy of {path} ...")
    def local_summary(root: Path) -> tuple[int, int]:
        files = 0
        total = 0
        if root.is_file():
            return 1, root.stat().st_size
        for p in root.rglob("*"):
            if p.is_file() and not p.is_symlink():
                files += 1
                total += p.stat().st_size
        return files, total
    lf, lb = local_summary(src)
    remote_out = run(
        ["ssh", REMOTE_HOST,
         f"find {REMOTE_ROOT}/{space}/{path} -type f -printf '%s\\n' 2>/dev/null | "
         f"awk 'BEGIN{{n=0;s=0}}{{n++;s+=$1}}END{{print n, s}}'"],
        capture=True).stdout.strip().split()
    if len(remote_out) != 2:
        sys.exit(f"abort: remote path missing or unreadable: {remote_out}")
    rf, rb = int(remote_out[0]), int(remote_out[1])
    print(f"  local:  {lf} files, {lb} bytes")
    print(f"  remote: {rf} files, {rb} bytes")
    if (lf, lb) != (rf, rb):
        sys.exit("abort: local/remote mismatch")
    print("match. deleting local.")
    run(["rm", "-rf", str(src)])
    write_manifest(space, [path])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("push", "pull"):
        p = sub.add_parser(c)
        p.add_argument("space")
        p.add_argument("paths", nargs="*")
    p = sub.add_parser("status")
    p.add_argument("space")
    p = sub.add_parser("evict")
    p.add_argument("space")
    p.add_argument("path")
    args = ap.parse_args()
    if not REMOTE_HOST:
        sys.exit(f"error: no mirror host: set remote_host in {CONFIG} "
                 "or roles.assets in .datacore/registry/infrastructure.yaml")

    if args.cmd == "push":
        cmd_push(args.space, args.paths)
    elif args.cmd == "pull":
        cmd_pull(args.space, args.paths)
    elif args.cmd == "status":
        cmd_status(args.space)
    elif args.cmd == "evict":
        cmd_evict(args.space, args.path)


if __name__ == "__main__":
    main()
