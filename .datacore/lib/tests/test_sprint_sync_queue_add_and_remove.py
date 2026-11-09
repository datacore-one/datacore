"""sprint_sync: one run can add a queue entry and remove another.

On 2026-10-16 the --apply run for enterprise sprint 13 crashed with
StaleNodeError: sync_queue held the existing nightshift.org entries as live
NodeViews, created G1's new entry (which bumps the file's generation), and then
read `.properties` on the old views to decide removals. The transaction rolled
back, so G1 was never queued.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import sprint_sync  # noqa: E402
from org_transaction import SafeOrgWorkspace, serialized  # noqa: E402

HEADER = sprint_sync.QUEUE_HEADER.format(space="9-test", slug="9-test")


def entry(qid, source, sprint):
    return (f"** NEXT Work for {source} :AI:\n"
            f"  :PROPERTIES:\n"
            f"  :ID: {qid}\n"
            f"  :SOURCE_ID: {source}\n"
            f"  :SPACE: 9-test\n"
            f"  :SPRINT: {sprint}\n"
            f"  :END:\n"
            f"  Reference entry. The specification lives on {source}.\n")


def ids_in(path):
    return [line.split(":ID:")[1].strip() for line in path.read_text().splitlines()
            if ":ID:" in line]


def test_add_one_and_remove_one_in_the_same_run(tmp_path, monkeypatch):
    org = tmp_path / "9-test" / "org"
    org.mkdir(parents=True)
    q = org / "nightshift.org"
    # Entries of a sprint this run cannot see stay, as in the real queue.
    others = "".join(entry(f"other-{i}-q", f"other-{i}", "S2") for i in range(8))
    q.write_text(HEADER + others + entry("old-1-q", "old-1", "S1")
                 + entry("old-2-q", "old-2", "S1"))
    monkeypatch.setattr(sprint_sync, "REPO", tmp_path)

    wanted = [{"id": "new-1", "title": "New work", "tags": [], "sprint": "S1",
               "assignee": "miles", "surface": "research"}]
    @serialized
    def run():
        ws = SafeOrgWorkspace()
        result = sprint_sync.sync_queue(ws, "9-test", wanted, True,
                                        seen_sprints={"S1"})
        ws.save_all()
        return result

    added, removed, _ = run()

    assert [a[0] for a in added] == ["new-1-q"]
    assert sorted(r[0] for r in removed) == ["old-1-q", "old-2-q"]
    left = ids_in(q)
    assert "new-1-q" in left
    assert "old-1-q" not in left and "old-2-q" not in left
    assert all(f"other-{i}-q" in left for i in range(8))
