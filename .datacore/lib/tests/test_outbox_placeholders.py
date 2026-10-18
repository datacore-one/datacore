"""An outbox holding only its .gitkeep has nothing to archive.

nightshift-outbox failed every night with "Archive repo not found for 8-firm":
the space's archive queue held nothing but the .gitkeep that keeps the
directory in git, discovery counted it as content, and the scanner tried to
archive the placeholder into a repo that does not exist (2026-09-17).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".datacore" / "modules" / "outbox" / "lib"))

from archive_sync import ArchiveScanner  # noqa: E402


def _outbox(root, space, files):
    # The queue is 4-archive/ since the owner folded 4-outbox into it (2026-10-18).
    base = root / space / "4-archive"
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


def test_the_archive_folders_own_index_and_readme_are_not_archived(tmp_path):
    """4-archive/_index.md and README.md describe the folder; shipping them
    would leave the space's archive folder without its own index."""
    _outbox(tmp_path, "1-a", {"_index.md": "index", "README.md": "about",
                              "done/2026-09.org": "real", "old/_index.md": "kept with old/"})
    scanner = ArchiveScanner(tmp_path)
    assert sorted(i.relative_path for i in scanner.scan_space("1-a")) == ["done/2026-09.org", "old/_index.md"]
    _outbox(tmp_path, "2-b", {"_index.md": "index", "README.md": "about"})
    assert scanner.discover_spaces() == ["1-a"]


def test_a_space_with_only_a_leftover_outbox_is_not_scanned(tmp_path):
    (tmp_path / "1-a" / "4-outbox" / "archive").mkdir(parents=True)
    (tmp_path / "1-a" / "4-outbox" / "archive" / "x.md").write_text("old layout")
    assert ArchiveScanner(tmp_path).discover_spaces() == []
