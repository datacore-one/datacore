"""TSK-4: When a GitHub issue or PR is closed, its task closes too, with the
title left unchanged.

Kind: deterministic. The real gh_reconcile.reconcile_file on a tmp org file;
only the `gh api` process is faked (canned GitHub JSON, no network), so the
real response parsing, the close decision, the writer and mark_task_done run.

Seeded failure: tasks referencing a closed issue, a merged PR, a PR closed
without merging, and a merged PR on a task in REVIEW (the "in review" task the
2026-09-25 briefing kept showing as open), next to an open issue and a lookup
that fails. Every closed one must end terminal with its title byte-identical;
the open and unknown ones must stay open. Verified red by reintroducing the
double-separator heading rewrite (`* DONE  Title`).
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

GH = {  # api path -> canned `--jq` output (None = the call fails)
    "repos/o/r/issues/1": {"state": "closed", "closed_at": "2026-09-20T10:00:00Z",
                           "state_reason": "completed", "pull_request": None},
    "repos/o/r/pulls/2": {"state": "closed", "merged_at": "2026-09-21T10:00:00Z", "merged": True},
    "repos/o/r/pulls/3": {"state": "closed", "merged_at": None, "merged": False},
    "repos/o/r/pulls/4": {"state": "closed", "merged_at": "2026-09-22T10:00:00Z", "merged": True},
    "repos/o/r/issues/5": {"state": "open", "closed_at": None, "state_reason": None,
                           "pull_request": None},
    "repos/o/r/issues/6": None,
}

TASKS = [  # (state, title, url, must_close)
    ("NEXT", "Fix recall for scoped engrams", "https://github.com/o/r/issues/1", True),
    ("TODO", "Land the sync patch", "https://github.com/o/r/pull/2", True),
    ("WAITING", "Try the abandoned approach", "https://github.com/o/r/pull/3", True),
    ("REVIEW", "Review go-live blocker B1", "https://github.com/o/r/pull/4", True),
    ("TODO", "Still-open bug", "https://github.com/o/r/issues/5", False),
    ("TODO", "Lookup fails here", "https://github.com/o/r/issues/6", False),
]
HEADER = "#+SEQ_TODO: TODO(t) NEXT(n) WAITING(w@) REVIEW(r!) | DONE(d!) DEFERRED(f@) CANCELLED(c@)\n"


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


@pytest.fixture
def org(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    fake = types.SimpleNamespace(**{k: getattr(subprocess, k) for k in dir(subprocess)
                                    if not k.startswith("__")})
    fake.run = _fake_gh(subprocess.run)
    monkeypatch.setattr(gh_reconcile, "subprocess", fake)
    gh_reconcile._api_cache.clear()
    f = tmp_path / "5-evals" / "org" / "next_actions.org"
    f.parent.mkdir(parents=True)
    body = "".join(f"* {s} {t} :work:\n:PROPERTIES:\n:ID: t-{i}\n:END:\nSee {u}\n"
                   for i, (s, t, u, _) in enumerate(TASKS))
    f.write_text(HEADER + body, encoding="utf-8")
    return f


def _headings(f: Path) -> dict[str, tuple[str, str]]:
    """{id: (state, title as written)} straight from the bytes."""
    out, cur = {}, None
    for line in f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\* ([A-Z]+) (.*?)(?:\s+:[\w:]+:)?$", line)
        if m:
            cur = (m.group(1), m.group(2))
        m = re.match(r"^\s*:ID:\s+(\S+)", line)
        if m and cur:
            out[m.group(1)] = cur
    return out


def test_a_closed_issue_or_pr_closes_its_task_and_nothing_else(org):
    gh_reconcile.reconcile_file(org, "o", "r", dry_run=False)
    after = _headings(org)
    wrong = []
    for i, (state, title, url, must_close) in enumerate(TASKS):
        got_state, got_title = after[f"t-{i}"]
        if got_title != title:
            wrong.append(f"{url}: title changed {title!r} -> {got_title!r}")
        closed = got_state in ("DONE", "CANCELLED")
        if closed != must_close:
            wrong.append(f"{url} ({state}): expected {'closed' if must_close else 'open'}, "
                         f"task is {got_state}")
    assert not wrong, "\n".join(wrong)


def test_a_closed_task_says_when_and_why(org):
    gh_reconcile.reconcile_file(org, "o", "r", dry_run=False)
    text = org.read_text(encoding="utf-8")
    block = text.split("* DONE Fix recall for scoped engrams", 1)[1].split("\n* ", 1)[0]
    assert ":CLOSED:" in block or "CLOSED: [" in block
    assert "o/r#1 closed" in block
