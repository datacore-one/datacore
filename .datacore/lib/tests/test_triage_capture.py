"""Triage captures into inbox.org and never re-captures an item already moved on."""
from pathlib import Path

import triage_utils as T


def test_an_item_moved_to_next_actions_is_not_captured_again(tmp_path):
    org = tmp_path / "org"; org.mkdir()
    (org / "inbox.org").write_text("#+TITLE: Inbox\n")
    (org / "next_actions.org").write_text(
        "* TODO Respond to o/r#19\n:PROPERTIES:\n:TRIAGE_ID: gh-o-r-19\n:END:\n")
    r = T.create_triage_task(org_file=org / "inbox.org", heading="Respond to o/r#19", tags=["AI", "github"],
                             properties={"TRIAGE_ID": "gh-o-r-19"}, context_body="x")
    assert r.get("skipped") and r["success"]
    assert "gh-o-r-19" not in (org / "inbox.org").read_text()
