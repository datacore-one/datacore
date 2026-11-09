"""Findings from the Lean model DatacoreSpec/Reconcile.lean, replayed for real.

gh_reconcile promises "Only transitions to DONE when state is provably
terminal ... Never a false DONE." Each test here is a counterexample the model
found against the old code, run through the real reconcile_file with the
GitHub CLI stubbed (no network):

* a ref whose lookup failed was skipped, so one merged PR plus one unreachable
  ref closed the task;
* an issue closed as "not planned" became DONE, though DIP-0009 rules "will not
  do" is CANCELLED (dismissed as `dropped`, never counted as finished);
* a human edit made to the org file while the lookups ran was overwritten.

The sync/conflict.py finding was settled by removing the two-way detector
(decision P7, 2026-09-23); its strict xfail went with it, and the three-way
rule it pointed to stays in Reconcile.lean as the spec for a future engine.
"""
import json
import re
import sys
import types
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import gh_reconcile  # noqa: E402

MERGED = {"state": "closed", "merged_at": "2026-09-01T00:00:00Z", "merged": True}
OPEN_PR = {"state": "open", "merged_at": None, "merged": False}


def _issue(reason):
    return {"state": "closed", "closed_at": "2026-09-01T00:00:00Z",
            "state_reason": reason, "pull_request": None}


@pytest.fixture
def gh(monkeypatch, tmp_path):
    """Stub `gh api`: answers from a table, applying the caller's --jq keys."""
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path / "data"))
    table = {}

    def fake_run(cmd, **kw):
        assert cmd[:2] == ["gh", "api"], cmd
        m = re.match(r"repos/(.+?)/(.+?)/(pulls|issues)/(\d+)", cmd[2])
        obj = table.get(f"{m.group(1)}/{m.group(2)}/{m.group(3)}/{m.group(4)}")
        if obj is None:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="HTTP 502")
        keys = re.findall(r"(\w+): \.(\w+)", cmd[cmd.index("--jq") + 1])
        out = json.dumps({k: obj.get(v) for k, v in keys})
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(gh_reconcile.subprocess, "run", fake_run)
    gh_reconcile._api_cache.clear()
    yield table
    gh_reconcile._api_cache.clear()


def _run(tmp_path, text, **kw):
    f = tmp_path / "next_actions.org"
    f.write_text(text, encoding="utf-8")
    n = gh_reconcile.reconcile_file(f, "o", "r", False, **kw)
    return n, f.read_text(encoding="utf-8")


def test_an_unreachable_ref_blocks_the_close(gh, tmp_path):
    gh["o/r/pulls/1"] = MERGED  # issues/2 is absent: its lookup fails
    n, text = _run(tmp_path, "* TODO Ship\n  https://github.com/o/r/pull/1\n"
                             "  https://github.com/o/r/issues/2\n")
    assert n == 0
    assert text.startswith("* TODO Ship")


def test_all_refs_terminal_still_closes(gh, tmp_path):
    gh["o/r/pulls/1"] = MERGED
    gh["o/r/issues/2"] = _issue("completed")
    n, text = _run(tmp_path, "* TODO Ship\n  https://github.com/o/r/pull/1\n"
                             "  https://github.com/o/r/issues/2\n")
    assert n == 1
    assert text.startswith("* DONE Ship")


def test_an_issue_closed_not_planned_is_cancelled_not_done(gh, tmp_path):
    gh["o/r/issues/4"] = _issue("not_planned")
    n, text = _run(tmp_path, "* TODO Do it\n  https://github.com/o/r/issues/4\n")
    assert n == 1
    assert text.startswith("* CANCELLED Do it")
    assert ":CANCEL_REASON:" in text


@pytest.mark.parametrize("reason", ["completed", None])
def test_an_issue_closed_completed_or_legacy_is_done(gh, tmp_path, reason):
    gh["o/r/issues/4"] = _issue(reason)
    n, text = _run(tmp_path, "* TODO Do it\n  https://github.com/o/r/issues/4\n")
    assert n == 1
    assert text.startswith("* DONE Do it")
    assert ":CANCEL_REASON:" not in text


def test_a_duplicate_close_cancels(gh, tmp_path):
    """Decision P6 (2026-09-23): a duplicate is CANCELLED with a reason, where
    it used to be left open as proving neither."""
    gh["o/r/issues/4"] = _issue("duplicate")
    n, text = _run(tmp_path, "* TODO Do it\n  https://github.com/o/r/issues/4\n")
    assert n == 1
    assert text.startswith("* CANCELLED Do it")
    assert ":CANCEL_REASON: Issue o/r#4 closed as a duplicate" in text


def test_done_and_dropped_refs_together_are_left_for_a_human(gh, tmp_path):
    gh["o/r/pulls/1"] = MERGED
    gh["o/r/issues/4"] = _issue("not_planned")
    n, text = _run(tmp_path, "* TODO Mixed\n  https://github.com/o/r/pull/1\n"
                             "  https://github.com/o/r/issues/4\n")
    assert n == 0
    assert text.startswith("* TODO Mixed")


def test_an_edit_made_during_the_lookups_is_not_overwritten(gh, tmp_path, monkeypatch):
    gh["o/r/pulls/5"] = MERGED
    f = tmp_path / "next_actions.org"
    real = gh_reconcile.check_github_ref

    def human_edits_then_lookup(ref):
        f.write_text(f.read_text(encoding="utf-8") + "* TODO captured mid-run\n",
                     encoding="utf-8")
        return real(ref)

    monkeypatch.setattr(gh_reconcile, "check_github_ref", human_edits_then_lookup)
    n, text = _run(tmp_path, "* TODO Ship\n  https://github.com/o/r/pull/5\n")
    assert "* TODO captured mid-run" in text
    assert n == 0  # stale decision discarded; the next run closes it


def test_an_open_ref_still_blocks(gh, tmp_path):
    gh["o/r/pulls/1"] = MERGED
    gh["o/r/pulls/3"] = OPEN_PR
    n, text = _run(tmp_path, "* TODO Two\n  https://github.com/o/r/pull/1\n"
                             "  https://github.com/o/r/pull/3\n")
    assert n == 0
