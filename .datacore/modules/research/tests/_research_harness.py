"""Shared fixture for the research promise evals (KNW-2..KNW-5).

Loads the real research_orchestrator with the agent SDK stubbed, and points
every path it writes at a tmp tree. Nothing reaches the network, git, Telegram
or the real spaces: publication, fetch, analysis and HTTP are replaced.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

MODULE = Path(__file__).resolve().parents[1]
LIB = MODULE / "lib"
ROOT = MODULE.parents[2]

for name in ("claude_agent_sdk", "claude_agent_sdk.types"):
    if name not in sys.modules:
        sys.modules[name] = MagicMock()


def load():
    spec = importlib.util.spec_from_file_location("research_orchestrator_eval", LIB / "research_orchestrator.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class NoPublish:
    def __init__(self, repo):
        self.paths = {}

    def record(self, path, content):
        self.paths[Path(path)] = content

    def publish(self, message, push=True):
        return ""


def point_at(R, tmp_path, monkeypatch):
    """Redirect every output path into tmp_path; return the tree."""
    tmp_path = Path(tmp_path).resolve()
    data = tmp_path / "Data"
    personal = data / "0-personal"
    (personal / "org").mkdir(parents=True)
    state = tmp_path / "state"
    monkeypatch.setenv("DATACORE_STATE", str(state))
    monkeypatch.setattr(R, "DATA_DIR", data)
    monkeypatch.setattr(R, "PERSONAL", personal)
    monkeypatch.setattr(R, "ACME", data / "1-acme")
    monkeypatch.setattr(R, "RESEARCH_ORG", personal / "org" / "research_learning.org")
    monkeypatch.setattr(R, "DAILY_NEWS_ORG", personal / "org" / "daily_news.org")
    monkeypatch.setattr(R, "LITERATURE_DIR", personal / "3-knowledge" / "literature")
    monkeypatch.setattr(R, "ZETTEL_DIR", personal / "3-knowledge" / "zettel")
    monkeypatch.setattr(R, "COMPANIES_DIR", personal / "3-knowledge" / "reference" / "companies")
    monkeypatch.setattr(R, "PEOPLE_DIR_PERSONAL", personal / "3-knowledge" / "reference" / "people")
    monkeypatch.setattr(R, "PEOPLE_DIR_DF", data / "1-acme" / "3-knowledge" / "reference" / "people")
    monkeypatch.setattr(R, "LANDSCAPE_FILE", personal / "3-knowledge" / "reference" / "Industry landscape.md")
    monkeypatch.setattr(R, "JOURNAL_DIR", personal / "notes" / "journals")
    monkeypatch.setattr(R, "PODCAST_DIR", personal / "content" / "podcasts")
    monkeypatch.setattr(R, "REPORTS_DIR", personal / "content" / "reports")
    monkeypatch.setattr(R, "AUDIO_STATE", state / "nlm-audio-availability.json")
    monkeypatch.setattr(R, "PublicationManifest", NoPublish)
    return types.SimpleNamespace(data=data, personal=personal, state=state,
                                 org=personal / "org" / "research_learning.org")


def capture_http(monkeypatch):
    """Replace secret_http.urlopen; return the list of (url, form fields) posted."""
    import urllib.parse
    posts = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def urlopen(req, timeout=None):
        data = getattr(req, "data", None) or b""
        fields = dict(urllib.parse.parse_qsl(data.decode("utf-8"))) if data else {}
        posts.append((getattr(req, "full_url", str(req)), fields))
        return Resp()

    fake = types.ModuleType("secret_http")
    fake.urlopen = urlopen
    monkeypatch.setitem(sys.modules, "secret_http", fake)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return posts


def analysis(title):
    slug = title.lower().replace(" ", "-")
    return {
        "literature_note": {"filename": f"{slug}.md", "content": f"# {title}\n\nSummary."},
        "zettels": [{"filename": f"{slug}-concept.md", "content": "# Concept\n\nOne idea."}],
        "entities": {"companies": [], "people": []},
        "summary": f"{title} summary",
    }


def run_main(R, monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["research_orchestrator.py", "--no-daily-news", *args])
    return R.main()
