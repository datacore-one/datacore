# .datacore/lib/test_knowledge_lint.py
"""Tests for knowledge_lint.py"""
from pathlib import Path
from knowledge_lint import (
    check_orphan_zettels,
    check_literature_completeness,
    check_staleness,
    LintIssue,
)


def test_orphan_detection(tmp_path):
    """Zettels not referenced by any other file are orphans."""
    zettel_dir = tmp_path / "zettel"
    zettel_dir.mkdir()
    (zettel_dir / "Connected Concept.md").write_text("# Connected Concept\nSome content")
    (zettel_dir / "Orphan Concept.md").write_text("# Orphan Concept\nAlone")

    # A literature note links to Connected but not Orphan
    lit_dir = tmp_path / "literature"
    lit_dir.mkdir()
    (lit_dir / "Some Paper.md").write_text("Discusses [[Connected Concept]] in detail.")

    issues = check_orphan_zettels(tmp_path)
    orphan_paths = [i.path for i in issues]
    assert zettel_dir / "Orphan Concept.md" in orphan_paths
    assert zettel_dir / "Connected Concept.md" not in orphan_paths


def test_literature_completeness(tmp_path):
    """Literature notes must have Summary and Key Insights sections."""
    lit_dir = tmp_path / "literature"
    lit_dir.mkdir()

    # Complete note
    (lit_dir / "Good Paper.md").write_text(
        "# Good Paper\n\n## Summary\nGood stuff.\n\n## Key Insights\n- Insight 1\n"
    )
    # Incomplete note — missing Key Insights
    (lit_dir / "Bad Paper.md").write_text(
        "# Bad Paper\n\n## Summary\nSome stuff.\n"
    )

    issues = check_literature_completeness(tmp_path)
    assert len(issues) == 1
    assert "Bad Paper" in str(issues[0].path)
    assert "Key Insights" in issues[0].message


def test_staleness_detection(tmp_path):
    """Files not modified in 180+ days are flagged as stale."""
    zettel_dir = tmp_path / "zettel"
    zettel_dir.mkdir()

    stale = zettel_dir / "Old Concept.md"
    stale.write_text("---\nmaturity: seedling\n---\n# Old Concept\nStale content")

    import os, time
    old_time = time.time() - (200 * 86400)  # 200 days ago
    os.utime(stale, (old_time, old_time))

    issues = check_staleness(tmp_path, max_age_days=180)
    assert len(issues) == 1
    assert "Old Concept" in str(issues[0].path)
