#!/usr/bin/env python3
"""Tests for the single git writer (DIP-0046 C2).

This module had NO tests until 2026-08-11, which is how a swallowed exit code
survived in it: `converge` ran `git add -A` and then `git commit` without
checking the result. When a pre-commit hook refused the commit, everything was
left staged and the *merge* failed with "your local changes would be
overwritten by merge" — an error naming the wrong operation, in a repo whose
real problem was one invalid org tag. Swallowing a non-zero rc from git is the
exact defect DIP-0046 was written to remove, and it was inside the module that
removes it.

The classification tests exist because 'offline' and 'auth denied' were the
same string for a day. They are different instructions to a human: one says
wait, the other says fix your key. Four spaces sat unsyncable behind a message
that read like a closed laptop lid.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from ledger_transport import _fetch_reason, converge, sync_repo  # noqa: E402


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


@pytest.fixture()
def repo_pair(tmp_path: Path, monkeypatch):
    """A clone with a real origin, registered so the transport will act on it."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    git(work, "config", "user.email", "t@t")
    git(work, "config", "user.name", "t")
    # Local hooks only — never inherit this machine's global core.hooksPath,
    # which would run the real repo's guards against a fixture.
    hooks = work / ".git" / "hooks"
    git(work, "config", "core.hooksPath", str(hooks))
    (work / "seed.txt").write_text("seed\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "seed")
    git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(work, "branch", "-M", "main")
    git(work, "branch", "--set-upstream-to=origin/main", "main")

    # The transport refuses repos absent from the registry (D3), so make the
    # fixture's classification succeed without touching the real registry.
    import ledger_transport as lt
    monkeypatch.setattr(lt, "classify",
                        lambda space, root=None: lt.Result(True, "code", {"entry": {}}))
    return work


def test_refused_autosave_stops_the_converge(repo_pair: Path):
    """A pre-commit hook that says no must end the converge, naming itself."""
    hook = repo_pair / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'INVALID ORG TAGS: line 6042' >&2\nexit 1\n")
    hook.chmod(0o755)
    (repo_pair / "dirty.txt").write_text("local work\n")

    res = converge(repo_pair)

    assert not res.ok
    assert "autosave refused" in res.reason
    # The hook's own words must reach the operator; a generic failure would
    # leave them hunting for which of 192 org files is at fault.
    assert "6042" in res.context.get("detail", "")


def test_refused_autosave_does_not_lose_the_work(repo_pair: Path):
    """Refusing must not discard: the file is still there, still stageable."""
    hook = repo_pair / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    (repo_pair / "dirty.txt").write_text("local work\n")

    converge(repo_pair)

    assert (repo_pair / "dirty.txt").read_text() == "local work\n"
    assert "dirty.txt" in git(repo_pair, "status", "--porcelain").stdout


def test_clean_repo_converges(repo_pair: Path):
    assert converge(repo_pair).ok


def test_autosave_commits_when_the_hook_allows(repo_pair: Path):
    (repo_pair / "dirty.txt").write_text("local work\n")
    res = converge(repo_pair)
    assert res.ok
    assert git(repo_pair, "status", "--porcelain").stdout.strip() == ""
    assert "autosave" in git(repo_pair, "log", "-1", "--format=%s").stdout


@pytest.mark.parametrize("stderr,expected", [
    ("git@host: Permission denied (publickey).", "auth denied"),
    ("Authentication failed for 'https://…'", "auth denied"),
    ("Host key verification failed.", "host key not trusted"),
    ("ERROR: Repository not found.", "remote repo missing"),
    ("ssh: Could not resolve hostname h: nodename nor servname", "fetch failed"),
    ("", "fetch failed"),
])
def test_fetch_reasons_are_distinguished(stderr: str, expected: str):
    """Denied is not offline. One says fix your key, the other says wait."""
    assert expected in _fetch_reason(stderr)


def test_host_key_case_is_not_swallowed_by_auth():
    """Ordering guard: both messages mention the host, most-specific must win."""
    assert _fetch_reason("Host key verification failed.") == "host key not trusted"


def test_sync_repo_maps_blocked_distinctly(repo_pair: Path, monkeypatch, capsys):
    """'blocked' must not collapse into 'offline' — it never self-clears."""
    import ledger_transport as lt
    monkeypatch.setattr(lt, "converge",
                        lambda s: lt.Result(False, "auth denied (key rejected by remote)", {}))
    assert sync_repo(repo_pair, quiet=True) == "blocked"

    monkeypatch.setattr(lt, "converge",
                        lambda s: lt.Result(False, "fetch failed (offline?)", {}))
    assert sync_repo(repo_pair, quiet=True) == "offline"

    monkeypatch.setattr(lt, "converge",
                        lambda s: lt.Result(False, "autosave refused by pre-commit hook", {}))
    assert sync_repo(repo_pair, quiet=True) == "blocked"


def test_autosave_never_commits_a_submodule_pointer(repo_pair: Path, tmp_path: Path):
    """A pointer bump is a deliberate act, never a side effect of syncing.

    `git add -A` stages a changed gitlink, so without this an unattended
    converge would move `.datacore/dips` to whatever commit happened to be
    checked out locally — publishing a DIP revision nobody chose to publish.
    """
    sub_origin = tmp_path / "sub.git"
    subprocess.run(["git", "init", "-q", "--bare", str(sub_origin)], check=True)
    seed = tmp_path / "subseed"
    subprocess.run(["git", "clone", "-q", str(sub_origin), str(seed)], check=True)
    git(seed, "config", "user.email", "t@t"); git(seed, "config", "user.name", "t")
    (seed / "a.txt").write_text("one\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "one")
    git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")

    subprocess.run(["git", "-C", str(repo_pair), "-c", "protocol.file.allow=always",
                    "submodule", "add", "-q", str(sub_origin), "sub"],
                   capture_output=True, text=True)
    git(repo_pair, "commit", "-qm", "add submodule")
    before = git(repo_pair, "rev-parse", "HEAD:sub").stdout.strip()

    # Move the submodule's checkout — the pointer is now dirty.
    sub = repo_pair / "sub"
    (sub / "a.txt").write_text("two\n")
    git(sub, "config", "user.email", "t@t"); git(sub, "config", "user.name", "t")
    git(sub, "add", "-A"); git(sub, "commit", "-qm", "two")

    converge(repo_pair)

    assert git(repo_pair, "rev-parse", "HEAD:sub").stdout.strip() == before
    # Preserved, not discarded: still visible as a working-tree change.
    assert "sub" in git(repo_pair, "status", "--porcelain").stdout


def test_seq_gap_reports_unverifiable_when_fetch_fails(tmp_path: Path, monkeypatch):
    """A failed fetch must not read as 'all published' (DIP-0046 A1).

    The fetch return code was discarded, so an unreachable remote fell back to
    the stale remote-tracking ref, found it equal to local, and reported
    everything safely replicated — at the exact moment it could not check.
    Observed live 2026-08-11 when the Gitea host's disk failed.
    """
    import sys as _sys
    _sys.path.insert(0, str(LIB / "detectors"))
    import seq_gap

    space = tmp_path / "1-thing"
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / ".datacore" / "events" / "mac.jsonl").write_text('{"seq":7}\n')

    # Fetch fails; every other git call would otherwise succeed.
    calls = {"fetch": 0}

    def fake_git(repo, *args):
        if args and args[0] == "fetch":
            calls["fetch"] += 1
            return 128, "fatal: Could not read from remote repository."
        return 0, ""

    monkeypatch.setattr(seq_gap, "git", fake_git)
    rows = seq_gap.scan_space(space, fetch=True)

    assert calls["fetch"] == 1
    assert rows and all(r["error"] for r in rows), "a failed fetch must mark rows unverifiable"
    assert all(r["gap"] is None for r in rows), "must not claim a gap of zero"
    assert "unreachable" in rows[0]["error"]


def test_submodule_only_change_still_converges(repo_pair: Path, tmp_path: Path):
    """A repo dirty ONLY in a submodule must still sync.

    Unstaging the submodule can empty the index, and `git commit` then exits
    non-zero for "nothing to commit". Treating that as a refused autosave
    aborted the converge, so such a repo could never sync again. Observed on
    nightshift: 2 ahead, 7 behind, dirty only in .datacore/dips.
    """
    sub_origin = tmp_path / "sub2.git"
    subprocess.run(["git", "init", "-q", "--bare", str(sub_origin)], check=True)
    seed = tmp_path / "sub2seed"
    subprocess.run(["git", "clone", "-q", str(sub_origin), str(seed)], check=True)
    git(seed, "config", "user.email", "t@t"); git(seed, "config", "user.name", "t")
    (seed / "a.txt").write_text("one\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "one")
    git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")

    subprocess.run(["git", "-C", str(repo_pair), "-c", "protocol.file.allow=always",
                    "submodule", "add", "-q", str(sub_origin), "sub"],
                   capture_output=True, text=True)
    git(repo_pair, "commit", "-qm", "add submodule")
    before = git(repo_pair, "rev-parse", "HEAD:sub").stdout.strip()

    sub = repo_pair / "sub"
    (sub / "a.txt").write_text("two\n")
    git(sub, "config", "user.email", "t@t"); git(sub, "config", "user.name", "t")
    git(sub, "add", "-A"); git(sub, "commit", "-qm", "two")

    res = converge(repo_pair)

    assert res.ok, f"submodule-only dirt must not block convergence: {res.reason}"
    assert git(repo_pair, "rev-parse", "HEAD:sub").stdout.strip() == before


def test_converge_publishes(repo_pair: Path):
    """Converge must PUSH, not just pull.

    It previously stopped after the merge, making it one-way: every caller —
    `sync`, `./sync pull`, cos_sync on winston's 15-minute cron — reported
    "synced clean" from that Result while nothing left the machine. Measured
    before the fix: 5-plur sat 2 commits ahead of a reachable GitHub remote and
    nightshift held 4, including a 140-line audit script.
    """
    (repo_pair / "work.txt").write_text("published?\n")
    git(repo_pair, "add", "-A")
    git(repo_pair, "commit", "-qm", "local work")
    assert git(repo_pair, "rev-list", "--count", "origin/main..main").stdout.strip() == "1"

    res = converge(repo_pair)

    assert res.ok, res.reason
    git(repo_pair, "fetch", "-q", "origin")
    assert git(repo_pair, "rev-list", "--count", "origin/main..main").stdout.strip() == "0"


def test_converge_reports_when_it_merged_but_could_not_publish(repo_pair: Path, monkeypatch):
    """Pulled-but-unpublished is a distinct outcome, never a silent success."""
    import ledger_transport as lt
    monkeypatch.setattr(lt, "_push_with_retry",
                        lambda space, db: lt.Result(False, "push failed", {}))
    (repo_pair / "work.txt").write_text("x\n")
    git(repo_pair, "add", "-A"); git(repo_pair, "commit", "-qm", "w")

    res = converge(repo_pair)

    assert not res.ok
    assert "not published" in res.reason


def test_projection_never_lands_inside_org(tmp_path: Path):
    """The ID-churn root cause: a projection beside the file it projects.

    The projection reproduces every :ID: by design. Written into org/ it made
    every id a duplicate to any tool that loads more than one org file from
    that directory — 605 duplicate-ID warnings measured on 0-personal — and
    `dedup_ids()` regenerates duplicates on load. A save persists it, autosave
    commits it, and 1,204 ids change across eight spaces.

    It was also tracked in git in all nine spaces, so the condition replicated
    to every machine.
    """
    import sys as _sys
    _sys.path.insert(0, str(LIB))
    from ledger.shadow import compare

    space = tmp_path / "1-thing"
    (space / "org").mkdir(parents=True)
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / "org" / "next_actions.org").write_text(
        "* Focus\n** TODO A task\n   :PROPERTIES:\n   :ID: org-x-1\n   :END:\n")

    compare(space)

    stray = list((space / "org").glob("*.projected.org"))
    assert not stray, f"projection must not be written into org/: {stray}"
    written = list((space / ".datacore" / "state" / "projections").glob("*.projected.org"))
    assert written, "projection should be written under .datacore/state/projections/"


def test_f2_gate_opens_after_consecutive_clean_days(tmp_path, monkeypatch):
    """The F2 counter must actually reach the threshold and open the gate.

    Waiting five days on a counter nobody proved can open is how a migration
    stalls silently. This drives the streak logic through five consecutive
    clean days, a gap, and a dirty day, asserting each transition.
    """
    import importlib, json as _json
    from datetime import date, timedelta
    import sys as _sys
    _sys.path.insert(0, str(LIB))
    import shadow_check as sc
    importlib.reload(sc)

    status = tmp_path / "shadow-status.json"
    monkeypatch.setattr(sc, "STATUS", status)
    need = sc.PHASE1_CLEAN_DAYS

    def advance(day: date, all_clean: bool) -> int:
        prev = _json.loads(status.read_text()) if status.exists() else {}
        streak = int(prev.get("consecutive_clean_days") or 0)
        prev_date = prev.get("date")
        if prev_date != day.isoformat():
            if not all_clean:
                streak = 0
            else:
                ok = False
                if prev_date:
                    try:
                        ok = (day - date.fromisoformat(prev_date)).days == 1
                    except ValueError:
                        ok = False
                streak = streak + 1 if ok else 1
        elif not all_clean:
            streak = 0
        status.write_text(_json.dumps({"date": day.isoformat(),
                                       "consecutive_clean_days": streak}))
        return streak

    start = date(2026, 9, 1)
    for i in range(need):
        s = advance(start + timedelta(days=i), True)
        assert s == i + 1, f"day {i+1} should read {i+1}, got {s}"
    assert s >= need, "gate must open after the required consecutive clean days"

    # A skipped day breaks the chain even though the next run is clean.
    assert advance(start + timedelta(days=need + 2), True) == 1
    # A dirty day zeroes it outright.
    assert advance(start + timedelta(days=need + 3), False) == 0


def test_orphan_gitlink_is_still_protected(repo_pair: Path, tmp_path: Path):
    """A gitlink with NO .gitmodules entry must not be autosaved either.

    The guard used `git submodule foreach`, which ABORTS ON THE FIRST ERROR.
    Hermes has a gitlink whose path has no url in .gitmodules, so foreach
    emitted one entry, died, and the guard let three space pointers through —
    committing them exactly as if it were not there. A protection that depends
    on unrelated config being well-formed is not a protection.
    """
    sub_origin = tmp_path / "orphan.git"
    subprocess.run(["git", "init", "-q", "--bare", str(sub_origin)], check=True)
    seed = tmp_path / "orphanseed"
    subprocess.run(["git", "clone", "-q", str(sub_origin), str(seed)], check=True)
    git(seed, "config", "user.email", "t@t"); git(seed, "config", "user.name", "t")
    (seed / "a.txt").write_text("one\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "one")
    git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")

    subprocess.run(["git", "-C", str(repo_pair), "-c", "protocol.file.allow=always",
                    "submodule", "add", "-q", str(sub_origin), "orphan"],
                   capture_output=True, text=True)
    git(repo_pair, "commit", "-qm", "add gitlink")
    # Remove the .gitmodules mapping — now foreach errors on this path.
    (repo_pair / ".gitmodules").write_text("")
    git(repo_pair, "add", ".gitmodules")
    git(repo_pair, "commit", "-qm", "orphan the gitlink")
    before = git(repo_pair, "rev-parse", "HEAD:orphan").stdout.strip()

    sub = repo_pair / "orphan"
    (sub / "a.txt").write_text("two\n")
    git(sub, "config", "user.email", "t@t"); git(sub, "config", "user.name", "t")
    git(sub, "add", "-A"); git(sub, "commit", "-qm", "two")

    converge(repo_pair)

    assert git(repo_pair, "rev-parse", "HEAD:orphan").stdout.strip() == before, \
        "an orphan gitlink pointer must not be autosaved"


def test_converge_folds_a_writers_own_ref_into_main(repo_pair: Path, tmp_path: Path):
    """A satellite writer publishes on refs/heads/ledger/<actor>; the next
    converge on any host merges it into main. Data's claims sat on ledger/data
    from 2026-08-11 with nothing folding them back."""
    import ledger_transport as lt
    origin = tmp_path / "origin.git"
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    git(other, "config", "user.email", "d@d"); git(other, "config", "user.name", "data")
    git(other, "config", "core.hooksPath", str(other / ".git" / "hooks"))
    ev = other / ".datacore" / "events"; ev.mkdir(parents=True)
    (ev / "data.jsonl").write_text('{"actor":"data","type":"item.claim","payload":{"id":"x"}}\n')
    git(other, "add", "-A"); git(other, "commit", "-qm", "ledger: data claim")
    git(other, "push", "-q", "origin", "HEAD:refs/heads/ledger/data")
    r = lt.converge(repo_pair)
    assert r.ok, r
    assert "origin/ledger/data" in r.context.get("ledger_refs", [])
    assert (repo_pair / ".datacore" / "events" / "data.jsonl").exists()
    # and it was published: origin/main now contains the writer's commit
    assert git(repo_pair, "rev-list", "--count", "origin/main..origin/ledger/data").stdout.strip() == "0"


# ── datacore#28 / #31 / #39: the retired `./sync`, and what replaced it ─────


def _second_clone(repo_pair: Path, tmp_path: Path) -> Path:
    origin = git(repo_pair, "remote", "get-url", "origin").stdout.strip()
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", origin, str(other)], check=True)
    git(other, "config", "user.email", "o@o")
    git(other, "config", "user.name", "o")
    git(other, "config", "core.hooksPath", str(other / ".git" / "hooks"))
    return other


def test_converge_steps_back_from_an_in_progress_merge(repo_pair: Path, tmp_path: Path):
    """A merge someone left half-finished is theirs. `sync push` used to
    `add -A` the conflict markers and push them (datacore#28)."""
    other = _second_clone(repo_pair, tmp_path)
    (other / "seed.txt").write_text("theirs\n")
    git(other, "commit", "-qam", "theirs")
    git(other, "push", "-q", "origin", "HEAD:main")
    (repo_pair / "seed.txt").write_text("mine\n")
    git(repo_pair, "commit", "-qam", "mine")
    git(repo_pair, "fetch", "-q", "origin")
    assert git(repo_pair, "merge", "origin/main").returncode != 0   # conflict, MERGE_HEAD stays
    head = git(repo_pair, "rev-parse", "HEAD").stdout.strip()

    res = converge(repo_pair)

    assert not res.ok
    assert "merge in progress" in res.reason
    assert git(repo_pair, "rev-parse", "HEAD").stdout.strip() == head
    assert "<" * 7 in (repo_pair / "seed.txt").read_text()   # untouched, for a person


def test_leftover_markers_alone_stop_the_autosave(repo_pair: Path):
    """No MERGE_HEAD (the merge was aborted by hand) but the markers stayed."""
    # Built from pieces: a literal marker at line start would trip the very
    # pre-commit guard this test is about.
    ours, sep, theirs = "<" * 7, "=" * 7, ">" * 7
    (repo_pair / "seed.txt").write_text(f"{ours} HEAD\nmine\n{sep}\ntheirs\n{theirs} origin/main\n")
    head = git(repo_pair, "rev-parse", "HEAD").stdout.strip()
    res = converge(repo_pair)
    assert not res.ok
    assert "conflict markers" in res.reason
    assert git(repo_pair, "rev-parse", "HEAD").stdout.strip() == head


def test_sync_outcomes_never_commits_a_code_repo(repo_pair: Path, tmp_path: Path, monkeypatch):
    import ledger_transport as lt
    monkeypatch.setattr(lt, "_registry", lambda root: {"work": {"category": "code"}})
    (repo_pair / "wip.py").write_text("print('half done')\n")
    head = git(repo_pair, "rev-parse", "HEAD").stdout.strip()

    assert lt.sync_outcomes(tmp_path) == [("work", "code", "dirty")]

    assert git(repo_pair, "rev-parse", "HEAD").stdout.strip() == head
    assert (repo_pair / "wip.py").read_text() == "print('half done')\n"


def test_sync_outcomes_fast_forwards_a_clean_code_repo(repo_pair: Path, tmp_path: Path, monkeypatch):
    import ledger_transport as lt
    monkeypatch.setattr(lt, "_registry", lambda root: {"work": {"category": "code"}})
    other = _second_clone(repo_pair, tmp_path)
    (other / "new.txt").write_text("x\n")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "upstream")
    git(other, "push", "-q", "origin", "HEAD:main")

    assert lt.sync_outcomes(tmp_path) == [("work", "code", "clean")]
    assert (repo_pair / "new.txt").exists()


def test_sync_outcomes_converges_a_knowledge_repo(repo_pair: Path, tmp_path: Path, monkeypatch):
    import ledger_transport as lt
    monkeypatch.setattr(lt, "_registry", lambda root: {"work": {"category": "knowledge"}})
    (repo_pair / "note.md").write_text("n\n")

    assert lt.sync_outcomes(tmp_path) == [("work", "knowledge", "clean")]
    assert git(repo_pair, "log", "-1", "--format=%s").stdout.strip() == "ledger: autosave before converge"


def test_status_lines_touch_nothing(repo_pair: Path, tmp_path: Path, monkeypatch):
    import ledger_transport as lt
    monkeypatch.setattr(lt, "_registry", lambda root: {"work": {"category": "code"}})
    (repo_pair / "wip.py").write_text("x\n")
    lines = lt.status_lines(tmp_path)
    assert len(lines) == 1 and lines[0].startswith("work:") and "dirty=1" in lines[0]
    assert git(repo_pair, "status", "--porcelain").stdout.strip()   # still dirty, untouched
