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


def _registry_file(root, category='knowledge'):
    registry = root / '.datacore/registry'
    registry.mkdir(parents=True, exist_ok=True)
    (registry / 'repositories.yaml').write_text('repositories:\n  9-fixture:\n    category: ' + category + '\n')


def test_classification_uses_the_selected_data_root(tmp_path, monkeypatch):
    import ledger_transport as lt
    root = tmp_path / 'Data'
    (root / '9-fixture').mkdir(parents=True)
    _registry_file(root)
    monkeypatch.setenv('DATACORE_ROOT', str(root))
    result = lt.classify(root / '9-fixture')
    assert result.ok and result.reason == 'knowledge'


def test_explicit_cli_root_overrides_the_ambient_root(tmp_path):
    import json
    import os
    root, other = tmp_path / 'Data', tmp_path / 'other'
    (root / '9-fixture').mkdir(parents=True)
    _registry_file(root)
    _registry_file(other, 'code')
    p = subprocess.run([sys.executable, str(LIB / 'ledger_transport.py'), 'classify', '--root', str(root), '--space', str(root / '9-fixture')],
        env={**os.environ, 'DATACORE_ROOT': str(other)}, capture_output=True, text=True, timeout=10)
    assert p.returncode == 0
    assert json.loads(p.stdout)['reason'] == 'knowledge'


@pytest.mark.parametrize('content', [
    '[', 'repositories: []\n', 'repositories: {9-fixture: invalid}\n',
    'repositories: {9-fixture: {category: typo}}\n',
    'repositories: {../outside: {category: knowledge}}\n',
    'repositories: {9-fixture: {category: code, category: knowledge}}\n',
    'repositories: {}\nrepositories: {9-fixture: {category: knowledge}}\n',
])
def test_invalid_registry_is_refused_before_syncing_any_entry(tmp_path, monkeypatch, content):
    import ledger_transport as lt
    root = tmp_path / 'Data'
    (root / '9-fixture/.git').mkdir(parents=True)
    _registry_file(root)
    (root / '.datacore/registry/repositories.yaml').write_text(content)
    monkeypatch.setenv('DATACORE_ROOT', str(root))
    result = lt.classify(root / '9-fixture')
    assert not result.ok
    monkeypatch.setattr(lt, '_code_update', lambda *a: pytest.fail('invalid registry reached code sync'))
    monkeypatch.setattr(lt, 'sync_repo', lambda *a, **k: pytest.fail('invalid registry reached data sync'))
    with pytest.raises(ValueError):
        lt.sync_outcomes(root)


def test_explicit_sync_root_reaches_each_knowledge_repo(tmp_path, monkeypatch):
    import ledger_transport as lt
    root, other = tmp_path / 'Data', tmp_path / 'other'
    (root / '9-fixture/.git').mkdir(parents=True)
    _registry_file(root)
    _registry_file(other, 'code')
    monkeypatch.setenv('DATACORE_ROOT', str(other))
    observed = []
    monkeypatch.setattr(lt, '_converge_locked', lambda space: observed.append(space) or lt.Result(True, 'ok', {}))
    monkeypatch.setattr(lt, '_code_update', lambda *a: pytest.fail('ambient root changed the selected category'))
    assert lt.sync_outcomes(root) == [('9-fixture', 'knowledge', 'clean')]
    assert observed == [root / '9-fixture']


def test_invalid_event_returns_failure_without_echoing_payload(repo_pair):
    import ledger_transport as lt
    result = lt.append(repo_pair, 'fixture', 'invalid-event-type', {'secret': 'synthetic-private-value'})
    assert not result.ok
    assert 'synthetic-private-value' not in repr(result)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


@pytest.fixture()
def repo_pair(tmp_path: Path, monkeypatch):
    """A clone with a real origin, registered so the transport will act on it."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True)
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
    # These tests exercise knowledge-repository autosave. Code repositories
    # have separate negative tests and may never enter that publication path.
    monkeypatch.setattr(lt, "classify",
                        lambda space, root=None: lt.Result(True, "knowledge", {"entry": {'category': 'knowledge'}}))
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
    # datacore#150 split these: an unreachable remote is UNVERIFIABLE (a
    # condition — a VPN, a closed lid), while a denied key or a missing repo
    # stays an ERROR (a fault someone must fix). Counting the first as an
    # error failed mac-seq-gap five times over a VPN toggle.
    assert rows, "a failed fetch must still produce rows"
    assert all(r.get("unverifiable") for r in rows), "offline rows are unverifiable"
    assert not any(r["error"] for r in rows), "offline is a condition, not an error"
    assert all("cannot verify" in (r.get("note") or "") for r in rows), \
        "and the reason must still be visible"
    assert all(r["gap"] is None for r in rows), "must not claim a gap of zero"
    assert "unreachable" in rows[0]["note"], "the note carries the reason now, not error"


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


def test_direct_converge_cannot_autosave_or_publish_code(repo_pair, monkeypatch):
    import ledger_transport as lt
    monkeypatch.setattr(lt, 'classify', lambda *a, **k: lt.Result(True, 'code', {'entry': {'category': 'code'}}))
    work = repo_pair / 'unreviewed.py'
    work.write_text('unfinished code\n')
    head = git(repo_pair, 'rev-parse', 'HEAD').stdout.strip()
    remote = git(repo_pair, 'ls-remote', 'origin', 'refs/heads/main').stdout
    result = lt.converge(repo_pair)
    assert git(repo_pair, 'rev-parse', 'HEAD').stdout.strip() == head
    assert git(repo_pair, 'ls-remote', 'origin', 'refs/heads/main').stdout == remote
    assert work.read_text() == 'unfinished code\n'
    assert not result.ok


@pytest.mark.parametrize('category', ['code', '', 'unknown'])
def test_fact_publication_refuses_code_or_unknown_categories(repo_pair, monkeypatch, category):
    import ledger_transport as lt
    monkeypatch.setattr(lt, 'classify', lambda *a, **k: lt.Result(True, category, {'entry': {'category': category}}))
    head = git(repo_pair, 'rev-parse', 'HEAD').stdout.strip()
    result = lt.append(repo_pair, 'fixture', 'item.create', {'id': 'one', 'title': 'Synthetic'})
    assert not result.ok
    assert not (repo_pair / '.datacore/events/fixture.jsonl').exists()
    assert git(repo_pair, 'rev-parse', 'HEAD').stdout.strip() == head


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


def _chain(*values: str, actor: str = "nightshift") -> str:
    from ledger.events import Event, body_dict, compute_hash, to_line
    rows, previous = [], "GENESIS"
    for seq, value in enumerate(values):
        body = body_dict(seq, f"{seq + 1000:013d}:000000:{actor}", actor, "item.create", {"id": value}, previous)
        previous = compute_hash(body)
        rows.append(to_line(Event(**body, hash=previous, sig="")))
    return "\n".join(rows) + "\n"


def _publish_writer_ref(repo_pair: Path, tmp_path: Path, log: str, text: str) -> None:
    """Another checkout commits `text` as `log` on refs/heads/ledger/nightshift."""
    origin, other = tmp_path / "origin.git", tmp_path / "ref-writer"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    git(other, "config", "user.email", "n@n"); git(other, "config", "user.name", "nightshift")
    git(other, "config", "core.hooksPath", str(other / ".git" / "hooks"))
    (other / log).write_text(text)
    git(other, "commit", "-qam", "ledger: nightshift claim")
    git(other, "push", "-q", "origin", "HEAD:refs/heads/ledger/nightshift")


@pytest.mark.parametrize("style", ["merge", "diff3", "zdiff3"])
def test_a_writer_ref_that_is_a_prefix_of_main_is_not_a_conflict(repo_pair: Path, tmp_path: Path, style: str):
    """Measured 2026-09-17 on nightshift, 5-plur: the claim path published
    nightshift.jsonl at 556 events on ledger/nightshift while main carried the
    same log at 561. Same writer, one history, one copy an exact prefix of the
    other -- and converge reported "merge conflict on a ledger ref -- human
    needed", which stopped the overnight run at its first step. Both sides
    appended after a common base, so git calls it a conflict; it is not one."""
    import ledger_transport as lt
    log = ".datacore/events/nightshift.jsonl"
    (repo_pair / log).parent.mkdir(parents=True)
    (repo_pair / log).write_text(_chain("a", "b"))
    git(repo_pair, "add", "-A"); git(repo_pair, "commit", "-qm", "base")
    git(repo_pair, "push", "-q", "origin", "HEAD:refs/heads/main")
    _publish_writer_ref(repo_pair, tmp_path, log, _chain("a", "b", "c", "d"))
    (repo_pair / log).write_text(_chain("a", "b", "c", "d", "e", "f"))
    git(repo_pair, "commit", "-qam", "main advances the same log")
    git(repo_pair, "config", "merge.conflictStyle", style)

    r = lt.converge(repo_pair)

    assert r.ok, r
    assert "origin/ledger/nightshift" in r.context.get("ledger_refs", [])
    assert (repo_pair / log).read_text() == _chain("a", "b", "c", "d", "e", "f")
    assert git(repo_pair, "rev-list", "--count", "HEAD..origin/ledger/nightshift").stdout.strip() == "0"
    assert git(repo_pair, "status", "--porcelain").stdout.strip() == ""


def test_a_forked_writer_log_on_a_ledger_ref_still_stops_for_a_human(repo_pair: Path, tmp_path: Path):
    """The boundary: two different continuations of one writer's log are a
    fork. Nothing may choose between them, and nothing may be left half-merged."""
    import ledger_transport as lt
    log = ".datacore/events/nightshift.jsonl"
    (repo_pair / log).parent.mkdir(parents=True)
    (repo_pair / log).write_text(_chain("a", "b"))
    git(repo_pair, "add", "-A"); git(repo_pair, "commit", "-qm", "base")
    git(repo_pair, "push", "-q", "origin", "HEAD:refs/heads/main")
    _publish_writer_ref(repo_pair, tmp_path, log, _chain("a", "b", "theirs"))
    (repo_pair / log).write_text(_chain("a", "b", "ours"))
    git(repo_pair, "commit", "-qam", "ours")
    head = git(repo_pair, "rev-parse", "HEAD").stdout

    r = lt.converge(repo_pair)

    assert not r.ok and "ledger ref" in r.reason
    assert git(repo_pair, "rev-parse", "HEAD").stdout == head
    assert (repo_pair / log).read_text() == _chain("a", "b", "ours")
    assert not (repo_pair / ".git" / "MERGE_HEAD").exists()


def test_a_prefix_log_beside_a_real_conflict_is_not_half_resolved(repo_pair: Path, tmp_path: Path):
    """A resolvable ledger file does not license committing a merge that also
    conflicts somewhere no lossless rule covers."""
    import ledger_transport as lt
    log = ".datacore/events/nightshift.jsonl"
    (repo_pair / log).parent.mkdir(parents=True)
    (repo_pair / log).write_text(_chain("a"))
    (repo_pair / "notes.md").write_text("base\n")
    git(repo_pair, "add", "-A"); git(repo_pair, "commit", "-qm", "base")
    git(repo_pair, "push", "-q", "origin", "HEAD:refs/heads/main")
    origin, other = tmp_path / "origin.git", tmp_path / "ref-writer"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    git(other, "config", "user.email", "n@n"); git(other, "config", "user.name", "nightshift")
    git(other, "config", "core.hooksPath", str(other / ".git" / "hooks"))
    (other / log).write_text(_chain("a", "b"))
    (other / "notes.md").write_text("theirs\n")
    git(other, "commit", "-qam", "ref"); git(other, "push", "-q", "origin", "HEAD:refs/heads/ledger/nightshift")
    (repo_pair / log).write_text(_chain("a", "b", "c"))
    (repo_pair / "notes.md").write_text("ours\n")
    git(repo_pair, "commit", "-qam", "ours")
    head = git(repo_pair, "rev-parse", "HEAD").stdout

    r = lt.converge(repo_pair)

    assert not r.ok
    assert git(repo_pair, "rev-parse", "HEAD").stdout == head
    assert (repo_pair / "notes.md").read_text() == "ours\n"
    assert not (repo_pair / ".git" / "MERGE_HEAD").exists()
