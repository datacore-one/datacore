"""An outbox holding only its .gitkeep has nothing to archive.

nightshift-outbox failed every night with "Archive repo not found for 8-firm":
the space's 4-outbox/archive/ held nothing but the .gitkeep that keeps the
directory in git, discovery counted it as content, and the scanner tried to
archive the placeholder into a repo that does not exist (2026-09-17).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".datacore" / "modules" / "outbox" / "lib"))

from archive_sync import ArchiveScanner  # noqa: E402


def _outbox(root, space, files):
    base = root / space / "4-outbox" / "archive"
    base.mkdir(parents=True)
    for rel, text in files.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text)


def test_a_placeholder_only_outbox_is_not_a_space_to_archive(tmp_path):
    _outbox(tmp_path, "8-firm", {".gitkeep": ""})
    _outbox(tmp_path, "1-a", {".gitkeep": "", ".DS_Store": "x", "docs/report.md": "real"})
    scanner = ArchiveScanner(tmp_path)
    assert scanner.discover_spaces() == ["1-a"]
    assert scanner.scan_space("8-firm") == []
    assert [i.relative_path for i in scanner.scan_space("1-a")] == ["docs/report.md"]
