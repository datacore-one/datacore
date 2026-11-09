"""MEM-16: Team standups and digests cover only their own space, and never name people from
outside the team.

Kind: deterministic -- the real code on the path to a team surface, against tmp fixtures:
  * post_standup.py (posts the "## Standup" section of the owner's PERSONAL journal, which spans
    every space, as a comment on the 1-datafund team issue), run with --dry-run;
  * standup_inputs.build (the per-space input builder for the journal-entry-writer).
No network: --dry-run never calls gh; JOURNAL_DIR is pointed at tmp.

Seeded failure: a personal-journal standup that mixes 1-datafund work with 5-plur and 3-fds work
and names an outside contact ("Marta Rivas", not a member of the team), and accomplishments
passed to the 1-datafund builder that belong to another space.
Red today: post_standup.py posts the section verbatim -- other spaces' work and the outsider's
name go to the datafund team issue. The builder passes foreign accomplishments through.
"""
import io
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import post_standup  # noqa: E402
import standup_inputs  # noqa: E402

DAY = date(2026, 9, 25)
JOURNAL = f"""# {DAY.isoformat()}

## Standup

#### Yesterday
- [1-datafund] Sent the grant budget table to the consortium
- [5-plur] Cut the plur 0.20.1 release and fixed the recall ranking bug
- [3-fds] Reviewed Fairdrop upload retry PR
- [1-datafund] Call with Marta Rivas about the pilot data room

#### Today
- [1-datafund] Finish the grant narrative

## Notes
private reflections
"""
FOREIGN = ("5-plur", "3-fds", "plur 0.20.1", "Fairdrop")
OUTSIDER = "Marta Rivas"


def _dry_run(tmp_path, monkeypatch) -> str:
    (tmp_path / f"{DAY.isoformat()}.md").write_text(JOURNAL)
    monkeypatch.setattr(post_standup, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["post_standup.py", "--date", DAY.isoformat(), "--dry-run"])
    monkeypatch.setattr(post_standup, "post_comment",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("dry run posted")))
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = post_standup.main()
    assert rc == 0, buf.getvalue()
    return buf.getvalue()


def test_team_standup_carries_only_its_own_space(tmp_path, monkeypatch):
    out = _dry_run(tmp_path, monkeypatch)
    assert "grant narrative" in out, "own-space work missing -- the check would be vacuous"
    leaked = [f for f in FOREIGN if f in out]
    assert not leaked, (f"the post to {post_standup.REPO}#{post_standup.ISSUE} carries other spaces' "
                        f"work: {leaked}")


def test_team_standup_names_no_outsider(tmp_path, monkeypatch):
    out = _dry_run(tmp_path, monkeypatch)
    assert OUTSIDER not in out, "the team standup names a person from outside the team"


def test_space_builder_drops_other_spaces_work(tmp_path):
    org = tmp_path / "1-datafund" / "org"
    org.mkdir(parents=True)
    (org / "next_actions.org").write_text(
        "* DONE Send the grant budget table to the consortium\n"
        "  CLOSED: [2026-09-24 Thu]\n  :PROPERTIES:\n  :ID: t-1\n  :END:\n")
    got = standup_inputs.build(space=str(tmp_path / "1-datafund"), contributor="plur9",
                               accomplishments=["Sent the grant budget table to the consortium",
                                                "[5-plur] Cut the plur 0.20.1 release"])
    blob = repr(got)
    assert "grant budget" in blob
    assert "plur 0.20.1" not in blob, "the 1-datafund standup input carries 5-plur work"
