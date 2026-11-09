"""KNW-10: Broken links and orphaned notes in my knowledge base are found and reported to
me, not left to rot.

Kind: deterministic (knowledge_lint.lint_knowledge on a tmp knowledge base) plus a
contract on the one declared list of scheduled jobs (lib/jobs/manifest.yaml): the
finding reaches the owner only if some declared job runs it and alerts on it.

Seeded failure: a [[link]] to a note that does not exist is not reported (the lint
has no broken-link check); an orphan zettel is not reported; or the lint exists but
no scheduled job ever runs it (knowledge_lint.py has no entry point and no job).
"""
import sys
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import knowledge_lint as K  # noqa: E402


def _kb(tmp_path):
    kb = tmp_path / "3-knowledge"
    (kb / "zettel").mkdir(parents=True)
    (kb / "literature").mkdir()
    (kb / "zettel" / "Linked idea.md").write_text("# Linked idea\n\nSee [[Vanished note]] for more.\n")
    (kb / "zettel" / "Lonely idea.md").write_text("# Lonely idea\n\nNobody links here.\n")
    (kb / "literature" / "Source.md").write_text(
        "# Source\n\n## Summary\n\nText.\n\n## Key Insights\n\n- [[Linked idea]]\n")
    return kb


def test_orphaned_notes_are_found(tmp_path):
    issues = K.lint_knowledge(_kb(tmp_path))
    orphans = [i.path.name for i in issues if i.check == "orphan"]
    assert orphans == ["Lonely idea.md"], orphans


def test_broken_links_are_found(tmp_path):
    issues = K.lint_knowledge(_kb(tmp_path))
    hits = [i for i in issues if "Vanished note" in (i.message + " " + i.suggestion)]
    assert hits, ("a [[link]] to a note that does not exist is not reported; the lint found only: "
                  + repr([(i.check, i.path.name) for i in issues]))


def test_a_declared_job_runs_the_knowledge_check_and_alerts():
    jobs = (yaml.safe_load((LIB / "jobs" / "manifest.yaml").read_text(encoding="utf-8")) or {}).get("jobs") or []
    runs = [j for j in jobs if isinstance(j, dict)
            and any(k in str(j.get("cmd", "")) for k in ("knowledge_lint", "datacortex", "orphan"))]
    assert runs, ("no job in lib/jobs/manifest.yaml runs the knowledge lint (or datacortex orphan "
                  "detection), so its findings never reach the owner")
    assert any(j.get("on_fail") for j in runs), f"the knowledge check alerts nobody: {runs}"
