#!/usr/bin/env python3
"""
Archive Sync Library

Provides utilities for routing content from 4-outbox/archive/ to archive repos.
Used by outbox-processor agent.

Per DIP-0017: Outbox & Archive Pattern
"""

import os
import sys
import yaml
import hashlib
import shlex
import subprocess
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class ArchiveItem:
    """Represents a file to be archived."""
    source_path: Path
    space: str
    relative_path: str  # Path within archive/ folder
    companion_path: Optional[Path] = None

    @property
    def archive_dest(self) -> str:
        """Destination path in archive repo."""
        return self.relative_path


@dataclass
class ArchiveResult:
    """Result of archiving operation."""
    success: bool
    items_processed: int = 0
    errors: List[str] = field(default_factory=list)
    by_space: Dict[str, int] = field(default_factory=dict)


class OutboxConfig:
    """Configuration for outbox operations."""

    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.settings_path = data_root / ".datacore" / "settings.yaml"
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Load settings.yaml."""
        if not self.settings_path.exists():
            return {}
        with open(self.settings_path) as f:
            return yaml.safe_load(f) or {}

    @property
    def archive_location(self) -> str:
        """server or local."""
        return self._config.get("outbox", {}).get("archive_location", "server")

    @property
    def server_host(self) -> str:
        """Server hostname for SSH."""
        return self._config.get("outbox", {}).get("server_host", "")

    @property
    def local_archive_path(self) -> Path:
        """Path for local archives."""
        path = self._config.get("outbox", {}).get("local_archive_path", "~/.datacore/archives")
        return Path(path).expanduser()

    @property
    def archive_repos(self) -> Dict[str, str]:
        """Mapping of space -> archive repo path."""
        return self._config.get("outbox", {}).get("archive_repos", {})


class ArchiveScanner:
    """Scans outbox folders for content to archive."""

    def __init__(self, data_root: Path):
        self.data_root = data_root

    def discover_spaces(self) -> List[str]:
        """Find all spaces with 4-outbox/archive/ content."""
        spaces = []
        for item in self.data_root.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                outbox_archive = item / "4-outbox" / "archive"
                if outbox_archive.exists() and any(outbox_archive.iterdir()):
                    spaces.append(item.name)
        return sorted(spaces)

    def scan_space(self, space: str) -> List[ArchiveItem]:
        """Scan a space's outbox/archive folder."""
        items = []
        archive_path = self.data_root / space / "4-outbox" / "archive"

        if not archive_path.exists():
            return items

        for file_path in archive_path.rglob("*"):
            if file_path.is_file():
                # Skip companion files - they'll be handled with their source
                if file_path.name.endswith(".companion.md"):
                    continue

                relative = file_path.relative_to(archive_path)

                # Check for companion
                companion = None
                companion_path = file_path.with_name(file_path.name + ".companion.md")
                if companion_path.exists():
                    companion = companion_path

                items.append(ArchiveItem(
                    source_path=file_path,
                    space=space,
                    relative_path=str(relative),
                    companion_path=companion
                ))

        return items


class ServerArchiver:
    """Archives content to server-based repos."""

    def __init__(self, config: OutboxConfig):
        self.config = config
        self.host = config.server_host

    def _ssh_command(self, cmd: str) -> Tuple[bool, str]:
        """Execute command on server via SSH."""
        try:
            result = subprocess.run(
                ["ssh", self.host, cmd],
                capture_output=True,
                text=True,
                timeout=60
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, "SSH command timed out"
        except Exception as e:
            return False, str(e)

    def _scp_file(self, local_path: Path, remote_path: str) -> Tuple[bool, str]:
        """Copy file to server via SCP."""
        try:
            result = subprocess.run(
                ["scp", str(local_path), f"{self.host}:{remote_path}"],
                capture_output=True,
                text=True,
                timeout=120
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, "SCP timed out"
        except Exception as e:
            return False, str(e)

    def check_connection(self) -> bool:
        """Verify server is reachable."""
        success, _ = self._ssh_command("echo ok")
        return success

    def ensure_repo_exists(self, space: str) -> bool:
        """Ensure archive repo exists on server."""
        repo_path = self.config.archive_repos.get(space)
        if not repo_path:
            return False

        # Check if repo exists
        success, _ = self._ssh_command(f"test -d ~/{shlex.quote(repo_path)}")
        return success

    def archive_item(self, item: ArchiveItem) -> Tuple[bool, str]:
        """Archive a single item to server."""
        repo_path = self.config.archive_repos.get(item.space)
        if not repo_path:
            return False, f"No archive repo configured for {item.space}"

        # Create destination directory
        dest_dir = os.path.dirname(item.archive_dest)
        if dest_dir:
            self._ssh_command(f"mkdir -p ~/{shlex.quote(repo_path)}/{shlex.quote(dest_dir)}")

        # Copy main file
        remote_path = f"~/{shlex.quote(repo_path)}/{shlex.quote(item.archive_dest)}"
        success, msg = self._scp_file(item.source_path, remote_path)
        if not success:
            return False, f"Failed to copy {item.source_path}: {msg}"

        # Copy companion if exists
        if item.companion_path:
            companion_dest = f"{item.archive_dest}.companion.md"
            remote_companion = f"~/{shlex.quote(repo_path)}/{shlex.quote(companion_dest)}"
            success, msg = self._scp_file(item.companion_path, remote_companion)
            if not success:
                return False, f"Failed to copy companion: {msg}"

        return True, "OK"

    def commit_changes(self, space: str, items: List[ArchiveItem]) -> Tuple[bool, str]:
        """Commit and push changes to archive repo."""
        repo_path = self.config.archive_repos.get(space)
        if not repo_path:
            return False, f"No archive repo configured for {space}"

        # Build commit message (sanitize archive_dest values)
        safe_items = [os.path.basename(item.archive_dest) for item in items]
        file_list = "\n".join([f"- {f}" for f in safe_items])
        commit_msg = f"Archive: {len(items)} items from {space}\n\n{file_list}"

        # Git add and commit
        self._ssh_command(f"cd ~/{shlex.quote(repo_path)} && git add -A")
        escaped_msg = shlex.quote(commit_msg)
        success, msg = self._ssh_command(f"cd ~/{shlex.quote(repo_path)} && git commit -m {escaped_msg}")
        if not success and "nothing to commit" not in msg:
            return False, f"Git commit failed: {msg}"

        return True, "OK"


class LocalArchiver:
    """Archives content to local repos."""

    def __init__(self, config: OutboxConfig):
        self.config = config
        self.archive_base = config.local_archive_path

    def ensure_repo_exists(self, space: str) -> bool:
        """Ensure local archive repo exists."""
        repo_path = self.archive_base / f"{space}-archive"
        if not repo_path.exists():
            repo_path.mkdir(parents=True)
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, timeout=30)
        return True

    def archive_item(self, item: ArchiveItem) -> Tuple[bool, str]:
        """Archive a single item locally."""
        repo_path = self.archive_base / f"{item.space}-archive"
        dest_path = repo_path / item.archive_dest

        # Create destination directory
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Copy main file
        try:
            shutil.copy2(item.source_path, dest_path)
        except Exception as e:
            return False, f"Failed to copy {item.source_path}: {e}"

        # Copy companion if exists
        if item.companion_path:
            companion_dest = dest_path.with_name(dest_path.name + ".companion.md")
            try:
                shutil.copy2(item.companion_path, companion_dest)
            except Exception as e:
                return False, f"Failed to copy companion: {e}"

        return True, "OK"

    def commit_changes(self, space: str, items: List[ArchiveItem]) -> Tuple[bool, str]:
        """Commit changes to local archive repo."""
        repo_path = self.archive_base / f"{space}-archive"

        # Build commit message (sanitize filenames)
        safe_items = [os.path.basename(item.archive_dest) for item in items]
        file_list = "\n".join([f"- {f}" for f in safe_items])
        commit_msg = f"Archive: {len(items)} items from {space}\n\n{file_list}"

        try:
            subprocess.run(["git", "add", "-A"], cwd=repo_path, capture_output=True, timeout=30)
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=repo_path,
                capture_output=True, timeout=30
            )
        except Exception as e:
            return False, str(e)

        return True, "OK"


class DisposeHandler:
    """Handles content marked for permanent deletion.

    Files are logged to a dispose log before deletion.
    Without --confirm, operates in dry-run mode showing what WOULD be deleted.
    """

    def __init__(self, data_root: Optional[Path] = None):
        self.data_root = data_root or Path.home() / "Data"
        self.log_path = self.data_root / ".datacore" / "state" / "dispose_log.yaml"

    def _load_log(self) -> List[dict]:
        """Load existing dispose log."""
        if not self.log_path.exists():
            return []
        with open(self.log_path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("disposed", [])

    def _save_log(self, entries: List[dict]) -> None:
        """Write dispose log."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "w") as f:
            yaml.dump({"disposed": entries}, f, default_flow_style=False, sort_keys=False)

    @staticmethod
    def _file_hash(path: Path) -> str:
        """SHA-256 hash of file content."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def dispose(self, paths: List[str], reason: str = "", confirm: bool = False) -> Dict:
        """Dispose of files. Only deletes when confirm=True.

        Args:
            paths: List of file paths to dispose.
            reason: Reason for disposal.
            confirm: If True, actually delete files. Otherwise dry-run.

        Returns:
            Dict with status, processed list, and errors.
        """
        entries = self._load_log()
        processed = []
        errors = []

        for file_path in paths:
            p = Path(file_path)
            if not p.exists():
                errors.append(f"File not found: {file_path}")
                continue
            if not p.is_file():
                errors.append(f"Not a file: {file_path}")
                continue

            entry = {
                "path": str(p),
                "hash": self._file_hash(p),
                "disposed_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
            }

            if confirm:
                entries.append(entry)
                self._save_log(entries)
                p.unlink()
                processed.append({"path": str(p), "action": "deleted"})
            else:
                processed.append({"path": str(p), "action": "would delete"})

        return {
            "mode": "confirm" if confirm else "dry-run",
            "processed": processed,
            "errors": errors,
        }

    def restore(self, file_path: str) -> Optional[dict]:
        """Look up a disposed file in the log. Content is gone; returns metadata only.

        Args:
            file_path: Original path of the disposed file.

        Returns:
            Log entry dict if found, None otherwise.
        """
        entries = self._load_log()
        for entry in entries:
            if entry.get("path") == file_path:
                return entry
        return None


class OutboxProcessor:
    """Main processor for outbox routing."""

    def __init__(self, data_root: Optional[Path] = None):
        self.data_root = data_root or Path.home() / "Data"
        self.config = OutboxConfig(self.data_root)
        self.scanner = ArchiveScanner(self.data_root)

        if self.config.archive_location == "server":
            self.archiver = ServerArchiver(self.config)
        else:
            self.archiver = LocalArchiver(self.config)

    def discover(self) -> Dict[str, List[ArchiveItem]]:
        """Discover all items to archive."""
        result = {}
        for space in self.scanner.discover_spaces():
            items = self.scanner.scan_space(space)
            if items:
                result[space] = items
        return result

    def process(self, dry_run: bool = False, spaces: Optional[List[str]] = None) -> ArchiveResult:
        """Process all outbox content."""
        result = ArchiveResult(success=True)

        # Check server connection if using server mode
        if self.config.archive_location == "server":
            if not self.archiver.check_connection():
                result.success = False
                result.errors.append(f"Cannot connect to server {self.config.server_host}")
                return result

        # Discover items
        discovered = self.discover()

        # Filter to requested spaces
        if spaces:
            discovered = {k: v for k, v in discovered.items() if k in spaces}

        if not discovered:
            return result

        # Process each space
        for space, items in discovered.items():
            if dry_run:
                result.by_space[space] = len(items)
                result.items_processed += len(items)
                continue

            # Ensure archive repo exists
            if not self.archiver.ensure_repo_exists(space):
                result.errors.append(f"Archive repo not found for {space}")
                continue

            # Archive each item
            archived = []
            for item in items:
                success, msg = self.archiver.archive_item(item)
                if success:
                    archived.append(item)
                    # Remove source files
                    item.source_path.unlink()
                    if item.companion_path:
                        item.companion_path.unlink()
                else:
                    result.errors.append(f"{item.source_path}: {msg}")

            # Commit changes
            if archived:
                success, msg = self.archiver.commit_changes(space, archived)
                if not success:
                    result.errors.append(f"Commit failed for {space}: {msg}")

            result.by_space[space] = len(archived)
            result.items_processed += len(archived)

        result.success = len(result.errors) == 0
        return result

    def to_json(self, result: ArchiveResult) -> dict:
        """Convert result to JSON-serializable dict."""
        return {
            "status": "success" if result.success else "error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "processed": {
                "archive": result.by_space
            },
            "total": result.items_processed,
            "errors": result.errors
        }


def main():
    """CLI entry point."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Process outbox archive queue")
    parser.add_argument("--dry-run", action="store_true", help="Preview without moving")
    parser.add_argument("--space", type=str, help="Process only specified space")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--data-root", type=str, help="Data root path")

    args = parser.parse_args()

    data_root = Path(args.data_root) if args.data_root else None
    processor = OutboxProcessor(data_root)

    spaces = [args.space] if args.space else None
    result = processor.process(dry_run=args.dry_run, spaces=spaces)

    if args.json:
        print(json.dumps(processor.to_json(result), indent=2))
    else:
        if args.dry_run:
            print("Dry Run - No files will be moved")
            print("=" * 40)
        else:
            print("Outbox Processing Complete")
            print("=" * 40)

        for space, count in result.by_space.items():
            action = "would be archived" if args.dry_run else "archived"
            print(f"\n{space}: {count} items {action}")

        print(f"\nTotal: {result.items_processed} items")

        if result.errors:
            print("\nErrors:")
            for err in result.errors:
                print(f"  - {err}")

    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
