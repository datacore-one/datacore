#!/usr/bin/env python3
"""
Archive Search Library

Provides semantic search across archived content using datacortex embeddings.
Archive snapshots are stored in _datacortex/ folders within archive repos.

Per DIP-0017: Outbox & Archive Pattern
"""

import os
import sys
import json
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import yaml

# Try to import datacortex components
try:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "datacortex" / "src"))
    from datacortex.ai.embeddings import embed_text, get_model, MODEL_NAME
    from datacortex.ai.similarity import cosine_similarity
    DATACORTEX_AVAILABLE = True
except ImportError:
    DATACORTEX_AVAILABLE = False
    MODEL_NAME = "sentence-transformers/all-mpnet-base-v2"

import numpy as np


@dataclass
class SearchResult:
    """A single search result."""
    file_path: str
    space: str
    score: float
    chunk_text: str
    chunk_index: int


@dataclass
class SearchResults:
    """Collection of search results."""
    query: str
    results: List[SearchResult] = field(default_factory=list)
    total_searched: int = 0
    spaces_searched: List[str] = field(default_factory=list)


class ArchiveConfig:
    """Configuration for archive search."""

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

    def get_snapshot_path(self, space: str) -> Optional[Path]:
        """Get path to datacortex snapshot for a space."""
        if self.archive_location == "local":
            return self.local_archive_path / f"{space}-archive" / "_datacortex"
        else:
            # For server mode, snapshot should be synced locally
            # Check if we have a local copy
            local_snapshot = self.data_root / f"{space}" / ".archive-snapshot"
            if local_snapshot.exists():
                return local_snapshot
            return None


class ArchiveDatabase:
    """Interface to archive embeddings database."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS embeddings (
        file_path TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        chunk_text TEXT NOT NULL,
        embedding BLOB NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (file_path, chunk_index)
    );

    CREATE TABLE IF NOT EXISTS manifest (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_embeddings_file ON embeddings(file_path);
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = None

    def connect(self) -> sqlite3.Connection:
        """Connect to database."""
        if self.conn is None:
            self.conn = sqlite3.connect(str(self.db_path))
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def initialize(self):
        """Initialize database schema."""
        conn = self.connect()
        conn.executescript(self.SCHEMA)
        conn.commit()

    def get_manifest(self) -> Dict[str, str]:
        """Get manifest metadata."""
        conn = self.connect()
        cursor = conn.execute("SELECT key, value FROM manifest")
        return {row["key"]: row["value"] for row in cursor}

    def set_manifest(self, key: str, value: str):
        """Set manifest metadata."""
        conn = self.connect()
        conn.execute(
            "INSERT OR REPLACE INTO manifest (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()

    def add_embedding(
        self,
        file_path: str,
        chunk_index: int,
        chunk_text: str,
        embedding: np.ndarray
    ):
        """Add an embedding to the database."""
        conn = self.connect()
        conn.execute(
            """INSERT OR REPLACE INTO embeddings
               (file_path, chunk_index, chunk_text, embedding)
               VALUES (?, ?, ?, ?)""",
            (file_path, chunk_index, chunk_text, embedding.tobytes())
        )
        conn.commit()

    def get_all_embeddings(self) -> List[Tuple[str, int, str, np.ndarray]]:
        """Get all embeddings from database."""
        conn = self.connect()
        cursor = conn.execute(
            "SELECT file_path, chunk_index, chunk_text, embedding FROM embeddings"
        )
        results = []
        for row in cursor:
            embedding = np.frombuffer(row["embedding"], dtype=np.float32)
            results.append((
                row["file_path"],
                row["chunk_index"],
                row["chunk_text"],
                embedding
            ))
        return results

    def get_file_count(self) -> int:
        """Get count of unique files."""
        conn = self.connect()
        cursor = conn.execute("SELECT COUNT(DISTINCT file_path) FROM embeddings")
        return cursor.fetchone()[0]

    def get_chunk_count(self) -> int:
        """Get total chunk count."""
        conn = self.connect()
        cursor = conn.execute("SELECT COUNT(*) FROM embeddings")
        return cursor.fetchone()[0]


class ArchiveIndexer:
    """Creates and updates archive embeddings index."""

    def __init__(self, config: ArchiveConfig):
        self.config = config
        self.chunk_size = 1000
        self.chunk_overlap = 200

    def _chunk_text(self, text: str) -> List[str]:
        """Split text into overlapping chunks."""
        if len(text) <= self.chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + self.chunk_size

            # Try to break at paragraph boundary
            if end < len(text):
                para_break = text.rfind("\n\n", start, end)
                if para_break > start + self.chunk_size // 2:
                    end = para_break + 2

            chunks.append(text[start:end].strip())
            start = end - self.chunk_overlap

        return [c for c in chunks if c]

    def _read_file(self, file_path: Path) -> Optional[str]:
        """Read file content for indexing."""
        # Only index text files
        indexable_extensions = {".md", ".txt", ".org", ".companion.md"}

        suffix = file_path.suffix.lower()
        if suffix not in indexable_extensions:
            # Check for .companion.md
            if not file_path.name.endswith(".companion.md"):
                return None

        try:
            return file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

    def index_archive(self, space: str, archive_path: Path) -> Tuple[int, int]:
        """Index all content in an archive repo.

        Args:
            space: Space name
            archive_path: Path to archive repo root

        Returns:
            Tuple of (files_indexed, chunks_created)
        """
        if not DATACORTEX_AVAILABLE:
            raise RuntimeError("Datacortex not available - cannot generate embeddings")

        # Setup database
        snapshot_dir = archive_path / "_datacortex"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        db = ArchiveDatabase(snapshot_dir / "archive.db")
        db.initialize()

        files_indexed = 0
        chunks_created = 0

        # Walk archive content
        for file_path in archive_path.rglob("*"):
            if file_path.is_file():
                # Skip system folders
                if "_datacortex" in file_path.parts:
                    continue
                if ".git" in file_path.parts:
                    continue

                content = self._read_file(file_path)
                if content is None:
                    continue

                # Get relative path for storage
                rel_path = str(file_path.relative_to(archive_path))

                # Chunk and embed
                chunks = self._chunk_text(content)
                for i, chunk in enumerate(chunks):
                    embedding = embed_text(chunk)
                    db.add_embedding(rel_path, i, chunk, embedding)
                    chunks_created += 1

                files_indexed += 1

        # Update manifest
        db.set_manifest("last_indexed", datetime.now().isoformat())
        db.set_manifest("model", MODEL_NAME)
        db.set_manifest("files_indexed", str(files_indexed))
        db.set_manifest("chunks_created", str(chunks_created))
        db.set_manifest("space", space)

        db.close()

        return files_indexed, chunks_created


class ArchiveSearcher:
    """Searches archive embeddings."""

    def __init__(self, config: ArchiveConfig):
        self.config = config

    def _load_snapshot(self, space: str) -> Optional[ArchiveDatabase]:
        """Load snapshot database for a space."""
        snapshot_path = self.config.get_snapshot_path(space)
        if snapshot_path is None:
            return None

        db_path = snapshot_path / "archive.db"
        if not db_path.exists():
            return None

        return ArchiveDatabase(db_path)

    def search(
        self,
        query: str,
        spaces: Optional[List[str]] = None,
        limit: int = 10,
        threshold: float = 0.5
    ) -> SearchResults:
        """Search across archive snapshots.

        Args:
            query: Search query
            spaces: Spaces to search (None = all)
            limit: Max results
            threshold: Minimum similarity score

        Returns:
            SearchResults object
        """
        if not DATACORTEX_AVAILABLE:
            # Return empty results if datacortex not available
            return SearchResults(query=query, results=[], total_searched=0)

        # Embed query
        query_embedding = embed_text(query)

        results = SearchResults(query=query)

        # Get spaces to search
        if spaces is None:
            spaces = list(self.config.archive_repos.keys())

        for space in spaces:
            db = self._load_snapshot(space)
            if db is None:
                continue

            results.spaces_searched.append(space)

            # Get all embeddings
            embeddings = db.get_all_embeddings()
            results.total_searched += len(embeddings)

            # Compute similarities
            for file_path, chunk_index, chunk_text, embedding in embeddings:
                score = cosine_similarity(query_embedding, embedding)

                if score >= threshold:
                    results.results.append(SearchResult(
                        file_path=file_path,
                        space=space,
                        score=score,
                        chunk_text=chunk_text[:200] + "..." if len(chunk_text) > 200 else chunk_text,
                        chunk_index=chunk_index
                    ))

            db.close()

        # Sort by score and limit
        results.results.sort(key=lambda r: -r.score)
        results.results = results.results[:limit]

        return results


class ServerSnapshotSync:
    """Syncs datacortex snapshots from server."""

    def __init__(self, config: ArchiveConfig):
        self.config = config

    def sync_snapshot(self, space: str) -> bool:
        """Sync snapshot from server for a space.

        Downloads _datacortex/ folder from server archive repo.

        Returns:
            True if successful
        """
        if self.config.archive_location != "server":
            return False

        repo_path = self.config.archive_repos.get(space)
        if not repo_path:
            return False

        # Local destination
        local_dest = self.config.data_root / space / ".archive-snapshot"
        local_dest.mkdir(parents=True, exist_ok=True)

        # Rsync from server
        server = self.config.server_host
        remote_path = f"~/{repo_path.replace('.git', '')}/_datacortex/"

        try:
            result = subprocess.run(
                ["rsync", "-avz", f"{server}:{remote_path}", str(local_dest) + "/"],
                capture_output=True,
                text=True,
                timeout=300
            )
            return result.returncode == 0
        except Exception:
            return False

    def sync_all(self) -> Dict[str, bool]:
        """Sync snapshots for all spaces."""
        results = {}
        for space in self.config.archive_repos.keys():
            results[space] = self.sync_snapshot(space)
        return results


def search_archives(
    query: str,
    spaces: Optional[List[str]] = None,
    limit: int = 10,
    data_root: Optional[Path] = None
) -> SearchResults:
    """Convenience function to search archives.

    Args:
        query: Search query
        spaces: Spaces to search (None = all)
        limit: Max results
        data_root: Data root path

    Returns:
        SearchResults object
    """
    data_root = data_root or Path.home() / "Data"
    config = ArchiveConfig(data_root)
    searcher = ArchiveSearcher(config)
    return searcher.search(query, spaces, limit)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Search archived content")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Search command
    search_parser = subparsers.add_parser("search", help="Search archives")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--space", type=str, help="Search specific space")
    search_parser.add_argument("--limit", type=int, default=10, help="Max results")
    search_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Index command
    index_parser = subparsers.add_parser("index", help="Index archive content")
    index_parser.add_argument("space", help="Space to index")
    index_parser.add_argument("archive_path", help="Path to archive repo")

    # Sync command
    sync_parser = subparsers.add_parser("sync", help="Sync snapshots from server")
    sync_parser.add_argument("--space", type=str, help="Sync specific space")

    args = parser.parse_args()
    data_root = Path.home() / "Data"
    config = ArchiveConfig(data_root)

    if args.command == "search":
        spaces = [args.space] if args.space else None
        results = search_archives(args.query, spaces, args.limit, data_root)

        if args.json:
            output = {
                "query": results.query,
                "spaces_searched": results.spaces_searched,
                "total_searched": results.total_searched,
                "results": [
                    {
                        "file_path": r.file_path,
                        "space": r.space,
                        "score": r.score,
                        "excerpt": r.chunk_text
                    }
                    for r in results.results
                ]
            }
            print(json.dumps(output, indent=2))
        else:
            print(f'Archive Search: "{results.query}"')
            print("=" * 50)
            print(f"Searched {results.total_searched} chunks in {results.spaces_searched}")
            print()

            if not results.results:
                print("No results found.")
            else:
                for i, r in enumerate(results.results, 1):
                    print(f"{i}. {r.space}-archive/{r.file_path}")
                    print(f"   Score: {r.score:.2f}")
                    print(f"   {r.chunk_text}")
                    print()

    elif args.command == "index":
        if not DATACORTEX_AVAILABLE:
            print("Error: Datacortex not available - cannot generate embeddings")
            sys.exit(1)

        indexer = ArchiveIndexer(config)
        archive_path = Path(args.archive_path).expanduser()

        print(f"Indexing {args.space} archive at {archive_path}...")
        files, chunks = indexer.index_archive(args.space, archive_path)
        print(f"Indexed {files} files, created {chunks} chunks")

    elif args.command == "sync":
        syncer = ServerSnapshotSync(config)

        if args.space:
            success = syncer.sync_snapshot(args.space)
            status = "OK" if success else "FAILED"
            print(f"{args.space}: {status}")
        else:
            results = syncer.sync_all()
            for space, success in results.items():
                status = "OK" if success else "FAILED"
                print(f"{space}: {status}")


if __name__ == "__main__":
    main()
