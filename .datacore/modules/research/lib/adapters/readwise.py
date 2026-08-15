"""
Readwise Reader Adapter - Fetches documents from Readwise Reader API v3.

API Documentation: https://readwise.io/reader_api
"""

import os

# DIP-0047: third-party action, previously unrecorded.
from datacore.ledger import attests
import requests
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class ReaderDocument:
    """Represents a Readwise Reader document."""
    id: str
    url: str
    title: str
    author: str
    category: str
    location: str
    tags: List[str]
    summary: str
    updated_at: str
    saved_at: str
    reading_progress: float
    highlights: List[Dict[str, Any]]

    @classmethod
    def from_api(cls, data: Dict) -> "ReaderDocument":
        """Create from API response data."""
        # Tags can be: empty dict {}, dict with tag names as keys, or list
        raw_tags = data.get("tags", {})
        if isinstance(raw_tags, dict):
            tags = list(raw_tags.keys()) if raw_tags else []
        elif isinstance(raw_tags, list):
            tags = [str(t) for t in raw_tags]
        else:
            tags = []

        return cls(
            id=data.get("id", ""),
            url=data.get("source_url") or data.get("url", ""),
            title=data.get("title", "Untitled"),
            author=data.get("author", ""),
            category=data.get("category", ""),
            location=data.get("location", ""),
            tags=tags,
            summary=data.get("summary", ""),
            updated_at=data.get("updated_at", ""),
            saved_at=data.get("saved_at", ""),
            reading_progress=data.get("reading_progress", 0),
            highlights=[]
        )


class ReadwiseAdapter:
    """Adapter for Readwise Reader API v3."""

    BASE_URL = "https://readwise.io/api/v3"
    AUTH_URL = "https://readwise.io/api/v2/auth/"

    def __init__(self, data_root: Path = None):
        self._data_root = data_root or Path.home() / "Data"
        self._token = None

    @property
    def name(self) -> str:
        return "Readwise Reader"

    def _get_token(self) -> Optional[str]:
        """Load token from .datacore/env/readwise.env"""
        if self._token:
            return self._token

        env_file = self._data_root / ".datacore" / "env" / "readwise.env"
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("READWISE_ACCESS_TOKEN="):
                        self._token = line.split("=", 1)[1].strip().strip("\"'")
                        return self._token

        # Fallback to environment variable
        self._token = os.environ.get("READWISE_ACCESS_TOKEN")
        return self._token

    def is_configured(self) -> bool:
        """Check if API token is available."""
        return bool(self._get_token())

    def test_connection(self) -> bool:
        """Validate token against Readwise API."""
        token = self._get_token()
        if not token:
            return False
        try:
            resp = requests.get(
                self.AUTH_URL,
                headers={"Authorization": f"Token {token}"},
                timeout=10
            )
            return resp.status_code == 204
        except Exception:
            return False

    def _request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make authenticated request to Reader API."""
        token = self._get_token()
        if not token:
            raise ValueError("Readwise API token not configured")

        url = f"{self.BASE_URL}/{endpoint}"
        headers = {"Authorization": f"Token {token}"}

        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def list_documents(
        self,
        updated_after: Optional[str] = None,
        location: Optional[str] = None,
        category: Optional[str] = None
    ) -> List[ReaderDocument]:
        """
        Fetch documents from Reader with optional filters.

        Args:
            updated_after: ISO 8601 timestamp (e.g., "2025-01-01T00:00:00Z")
            location: new, later, shortlist, archive, feed
            category: article, email, rss, pdf, epub, tweet, video

        Returns:
            List of ReaderDocument objects
        """
        documents = []
        cursor = None

        while True:
            params = {}
            if updated_after:
                params["updatedAfter"] = updated_after
            if location:
                params["location"] = location
            if category:
                params["category"] = category
            if cursor:
                params["pageCursor"] = cursor

            data = self._request("list/", params=params)

            for doc in data.get("results", []):
                # Skip highlights/notes (have parent_id)
                if not doc.get("parent_id"):
                    documents.append(ReaderDocument.from_api(doc))

            cursor = data.get("nextPageCursor")
            if not cursor:
                break

        return documents

    def get_document_highlights(self, document_id: str) -> List[Dict[str, str]]:
        """Fetch highlights for a specific document."""
        highlights = []
        cursor = None

        while True:
            params = {"parent_id": document_id}
            if cursor:
                params["pageCursor"] = cursor

            data = self._request("list/", params=params)

            for item in data.get("results", []):
                if item.get("category") == "highlight":
                    highlights.append({
                        "text": item.get("content", ""),
                        "note": item.get("notes", ""),
                        "location": item.get("reading_progress", 0)
                    })

            cursor = data.get("nextPageCursor")
            if not cursor:
                break

        return highlights

    def count_by_location(self) -> Dict[str, int]:
        """Get document counts by location."""
        counts = {"new": 0, "later": 0, "shortlist": 0, "archive": 0, "feed": 0}

        for location in counts.keys():
            docs = self.list_documents(location=location)
            counts[location] = len(docs)

        return counts

    @attests("readwise.delete", ref=lambda r: "")
    def delete_document(self, document_id: str) -> bool:
        """Delete a document from Readwise Reader. This is permanent."""
        token = self._get_token()
        if not token:
            raise ValueError("Readwise API token not configured")

        url = f"{self.BASE_URL}/delete/{document_id}/"
        headers = {"Authorization": f"Token {token}"}

        resp = requests.delete(url, headers=headers, timeout=30)
        return resp.status_code == 204

    def delete_documents(self, document_ids: List[str], rate_limit_pause: int = 65) -> Dict[str, int]:
        """
        Delete multiple documents with rate limit handling.

        Returns dict with 'deleted', 'errors', 'rate_limits' counts.
        """
        import time

        results = {"deleted": 0, "errors": 0, "rate_limits": 0}

        for doc_id in document_ids:
            try:
                if self.delete_document(doc_id):
                    results["deleted"] += 1
                else:
                    results["errors"] += 1
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    results["rate_limits"] += 1
                    time.sleep(rate_limit_pause)
                    # Retry once
                    try:
                        if self.delete_document(doc_id):
                            results["deleted"] += 1
                        else:
                            results["errors"] += 1
                    except:
                        results["errors"] += 1
                elif e.response.status_code == 404:
                    # Already deleted
                    results["deleted"] += 1
                else:
                    results["errors"] += 1
            except Exception:
                results["errors"] += 1

        return results
