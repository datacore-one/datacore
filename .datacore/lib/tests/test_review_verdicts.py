"""A task in review closes when its own pull request is settled, and only then.

WHY. The overnight agent leaves finished code work in REVIEW with the pull
request it opened in :NIGHTSHIFT_PR:. The owner's verdict on that work is what
happens to the pull request. Nothing wrote the verdict back for the queue file
(org/nightshift.org): on 2026-11-16 43 review tasks whose pull request was long
merged or closed were closed by hand, 40 of them in that file. The weekly
GitHub reconciler never reads it, and its rule is wider on purpose (every
reference a task names must be settled), so the issue behind a pull request
kept such tasks open even where it did read them.

Kind: deterministic. The real review_verdicts.run on tmp org files, writing
through the real task tool (org_workspace_adapter.py as a child process). Only
the `gh` process is faked (canned GitHub JSON, no network).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIB))

import gh_reconcile  # noqa: E402
import review_verdicts  # noqa: E402

MERGED = {"state": "closed", "merged_at": "2026-11-09T10:00:00Z", "merged": True,
          "closed_at": "2026-11-09T10:00:00Z"}
GH = {  # api path -> canned `--jq` output (None = the call fails)
    "repos/o/r/pulls/1": MERGED,
    "repos/o/r/pulls/2": {"state": "closed", "merged_at": None, "merged": False,
                          "closed_at": "2026-10-13T08:00:00Z"},
    "repos/o/r/pulls/3": {"state": "open", "merged_at": None, "merged": False, "closed_at": None},
    "repos/o/r/pulls/4": None,
    "repos/o/r/pulls/5": MERGED,
    "repos/o/r/issues/77": {"state": "open", "closed_at": None, "state_reason": None,
                            "pull_request": None},
}
PR = "https://github.com/o/r/pull/"
HEADER = "#+SEQ_TODO: TODO(t) NEXT(n) WAITING(w@) REVIEW(r!) | DONE(d!) DEFERRED(f@) CANCELLED(c@)\n"

QUEUE = [  # (id, state, title, extra property lines, state afterwards)
    ("q-merged", "REVIEW", "Add the round-trip check", f":NIGHTSHIFT_PR: {PR}1\n", "DONE"),
    ("q-rejected", "REVIEW", "Try the abandoned approach", f":NIGHTSHIFT_PR: {PR}2\n", "CANCELLED"),
    ("q-open", "REVIEW", "Still waiting for the verdict", f":NIGHTSHIFT_PR: {PR}3\n", "REVIEW"),
    ("q-unknown", "REVIEW", "The lookup fails here", f":NIGHTSHIFT_PR: {PR}4\n", "REVIEW"),
    # The issue behind the pull request is still open: the pull request decides.
    ("q-other-ref", "REVIEW", "Fix the importer (issue #77)",
     f":ISSUE: https://github.com/o/r/issues/77\n:NIGHTSHIFT_PR: {PR}5\n", "DONE"),
]
TASK_LIST = [
    ("l-merged", "REVIEW", "Land the sync patch", f":NIGHTSHIFT_PR: {PR}1\n", "DONE"),
    ("l-not-in-review", "TODO", "Mentions a merged pull request", f":NIGHTSHIFT_PR: {PR}1\n", "TODO"),
    ("l-no-pull-request", "REVIEW", "A report waiting to be read", "", "REVIEW"),
]
EVERY = QUEUE + TASK_LIST


def _fake_gh(real_run):
    def run(args, *a, **kw):
        if not args or args[0] != "gh":
            return real_run(args, *a, **kw)
        path = next(x for x in args if x.startswith("repos/"))
        data = GH.get(path)
        if data is None:
            return subprocess.CompletedProcess(args, 1, "", "HTTP 502")
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
    return run


def _org(tasks) -> str:
    return HEADER + "".join(
        f"* {state} {title} :work:\n:PROPERTIES:\n:ID: {tid}\n{extra}:END:\nNotes for {tid}.\n"
        for tid, state, title, extra, _ in tasks)


@pytest.fixture
def root(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    fake = types.SimpleNamespace(**{k: getattr(subprocess, k) for k in dir(subprocess)
                                    if not k.startswith("__")})
    fake.run = _fake_gh(subprocess.run)
    monkeypatch.setattr(gh_reconcile, "subprocess", fake)
    gh_reconcile._api_cache.clear()
    monkeypatch.setattr(review_verdicts, "_gh_ready", lambda: True)
    org = tmp_path / "Data" / "5-evals" / "org"
    org.mkdir(parents=True)
    (org / "nightshift.org").write_text(_org(QUEUE), encoding="utf-8")
    (org / "next_actions.org").write_text(_org(TASK_LIST), encoding="utf-8")
    return tmp_path / "Data"


def _files(root: Path) -> list[Path]:
    return [root / "5-evals" / "org" / n for n in ("nightshift.org", "next_actions.org")]


def _headings(root: Path) -> dict[str, tuple[str, str]]:
    """{id: (state, title as written)} straight from the bytes."""
    out = {}
    for f in _files(root):
        cur = None
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\* ([A-Z]+) (.*?)(?:\s+:[\w:]+:)?$", line)
            if m:
                cur = (m.group(1), m.group(2))
            m = re.match(r"^\s*:ID:\s+(\S+)", line)
            if m and cur:
                out[m.group(1)] = cur
    return out


def _block(root: Path, title: str) -> str:
    text = "".join(f.read_text(encoding="utf-8") for f in _files(root))
    return text.split(title, 1)[1].split("\n* ", 1)[0]


def test_a_settled_pull_request_closes_its_review_task_and_nothing_else(root):
    review_verdicts.run(root)
    after = _headings(root)
    wrong = []
    for tid, state, title, _, want in EVERY:
        got_state, got_title = after[tid]
        if got_title != title:
            wrong.append(f"{tid}: title changed {title!r} -> {got_title!r}")
        if got_state != want:
            wrong.append(f"{tid} ({state}): expected {want}, task is {got_state}")
    assert not wrong, "\n".join(wrong)


def test_the_close_names_the_pull_request_and_the_verdict(root):
    review_verdicts.run(root)
    merged = _block(root, "Add the round-trip check")
    assert "o/r#1" in merged and "merged" in merged
    rejected = _block(root, "Try the abandoned approach")
    assert ":CANCEL_REASON:" in rejected and "o/r#2" in rejected
    assert "closed without merging" in rejected


def test_the_result_counts_what_closed_what_waits_and_what_could_not_be_checked(root):
    result = review_verdicts.run(root)
    assert result["ok"] is True
    assert (result["done"], result["cancelled"]) == (3, 1)
    assert (result["open"], result["unverified"], result["left"]) == (1, 1, 0)
    assert result["line"].startswith("OK review-verdicts: closed 4 (done 3, cancelled 1)")
    assert "unverified 1" in result["line"]


def test_a_dry_run_changes_nothing_and_says_what_it_would_do(root):
    before = [f.read_bytes() for f in _files(root)]
    result = review_verdicts.run(root, dry_run=True)
    assert [f.read_bytes() for f in _files(root)] == before
    assert result["ok"] is True and "would close 4" in result["line"]


def test_the_cap_leaves_the_rest_for_the_next_run(root):
    result = review_verdicts.run(root, max_closes=1)
    closed = [tid for tid, (state, _) in _headings(root).items() if state in ("DONE", "CANCELLED")]
    assert len(closed) == 1
    assert result["left"] == 3 and "left for the next run 3" in result["line"]


def test_without_a_github_login_it_fails_loudly_and_changes_nothing(root, monkeypatch):
    monkeypatch.setattr(review_verdicts, "_gh_ready", lambda: False)
    before = [f.read_bytes() for f in _files(root)]
    result = review_verdicts.run(root)
    assert result["ok"] is False and result["line"].startswith("FAIL review-verdicts:")
    assert [f.read_bytes() for f in _files(root)] == before


def test_main_writes_the_status_line_and_its_exit_code_follows_it(root, monkeypatch, capsys):
    status = Path(review_verdicts.status_file())
    assert review_verdicts.main(["--data-dir", str(root)]) == 0
    assert status.read_text(encoding="utf-8").startswith("OK review-verdicts: closed 4")
    assert "OK review-verdicts" in capsys.readouterr().out
    monkeypatch.setattr(review_verdicts, "_gh_ready", lambda: False)
    assert review_verdicts.main(["--data-dir", str(root)]) == 1
    assert status.read_text(encoding="utf-8").startswith("FAIL review-verdicts:")
