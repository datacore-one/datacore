#!/usr/bin/env python3
"""The one writer. Publish facts, receive facts, report what is unpublished.

Sixteen call sites each invented their own commit/push/error handling, which is
why `git push … || true` looked reasonable in three of them and why the same
defect had to be fixed four times and in practice was not. This is the single
place any of that happens.

    append(space, actor, type, payload) -> Result   publish a fact
    converge(space)                     -> Result   receive others' facts
    gaps(space)                         -> Result   what exists only here

THREE PROPERTIES, each traceable to a specific incident.

**Expected failures return; they never raise.** A non-fast-forward push, an
offline remote, a missing ref — outcomes, not exceptions. Every one carries
`ok`, a `reason`, and `context` enough to act. Callers branch on `ok` instead of
wrapping every call, which is what made sixteen partial error handlers. Only
*unexpected* failures raise, because a caller that cannot tell "the remote is
down" from "the repository is corrupt" retries both, and retrying a corrupt
repository in a loop is worse than stopping.

**Merge, never rebase.** Per-writer logs are disjoint files, so a merge is a
union and cannot conflict. Rebase buys nothing here and is the operation that
stranded 610 commits on a parked branch and 645 across 74 run branches.

**The lock is not the whole story, and pretending otherwise is the trap.**
`flock` serialises writers on THIS machine. It cannot serialise two machines
pushing to one remote — the race the architecture exists to make safe. That one
is handled by an explicit bounded fetch-merge-retry on non-fast-forward
rejection. A test using two local processes passes without ever exercising it.

Appending does NOT change `log.py`. Its `EventLog.append()` already takes an
exclusive lock, truncates a torn tail and appends inside one critical section;
replacing that with whole-file rewrite-and-rename would cost O(n) per append and
reintroduce lost updates (DIP-0046 §10).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger.log import EventLog  # noqa: E402
from file_utils import file_lock, private_state_directory  # noqa: E402

PUSH_ATTEMPTS = 3


@dataclass
class Result:
    """An outcome. `ok` is the only thing callers branch on."""
    ok: bool
    reason: str = ""
    context: dict = field(default_factory=dict)

    def __bool__(self) -> bool:      # `if not converge(space):` reads correctly
        return self.ok


# AN UNREACHABLE HOST MUST FAIL IN SECONDS, NOT MINUTES. Measured against a
# blackhole address by the chaos harness on 2026-09-08: a single converge sat
# for 75 s before giving up, because ssh's default ConnectTimeout is the OS
# TCP timeout. A sweep over ten spaces then costs twelve minutes of pure
# waiting, which is how `mac-seq-gap` lost its artifact for three days --
# the job was killed before it could write anything at all.
#
# Five seconds is far longer than any reachable host needs. The distinction
# this preserves is the one that matters: a fast refusal still reads as
# `blocked` (auth, missing repo), and only a genuine timeout reads as
# `offline`. Failing fast makes that classification arrive sooner; it does
# not blur it.
SSH_FAIL_FAST = "ssh -o ConnectTimeout=5 -o BatchMode=yes"


def _net_env() -> dict:
    env = {**os.environ}
    env.setdefault("GIT_SSH_COMMAND", SSH_FAIL_FAST)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")   # never block waiting for a password
    return env


def _repair_weekdays(space: Path) -> list[str]:
    """Fix wrong day names in staged .org/.md files; return the paths repaired.

    Uses validate_org_dates -- the pre-commit hook's own validator -- so the
    two can never disagree about what is wrong, and its `_fix_file`, which
    rewrites under the org lock. Files it cannot read are left to the hook.
    """
    try:
        import validate_org_dates as dates
    except ImportError:
        return []
    rc, out, _ = _git(space, "diff", "--cached", "--name-only", "--diff-filter=ACM")
    repaired = []
    for rel in (out or "").splitlines() if rc == 0 else []:
        if not rel.endswith((".org", ".md")) or dates.is_archived(rel):
            continue
        path = space / rel
        if not path.is_file():
            continue
        result = dates._fix_file(path)
        if result and result[1]:
            _git(space, "add", "--", rel)
            repaired.append(rel)
    return repaired


def _git(repo: Path, *args: str, timeout: int = 120) -> tuple[int, str, str]:
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                           text=True, timeout=timeout, env=_net_env())
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"



_WRITER_LOG = re.compile(r"\.datacore/events/[^/]+\.jsonl")


def _merge(space: Path, ref: str, *, keep_both: bool = False) -> tuple[bool, str, list[str], list[dict]]:
    """Merge `ref`. (ok, detail, writer logs resolved as prefix extensions,
    content conflicts kept for a person).

    A per-writer log is append-only, so when both sides appended to it after a
    common base and one copy is an exact prefix of the other, git reports a
    conflict where there is a single history: the longer copy holds every event
    of both. Measured 2026-09-17 on nightshift, 5-plur: ledger/nightshift held
    nightshift.jsonl at 556 events, main at 561 with the same first 556, and
    "merge conflict on a ledger ref -- human needed" stopped the overnight run
    at its first step. A human could only have picked the longer file.

    Only that case is resolved, by resolve_ledger_conflicts (chain-validated,
    prefix-proven, working copy checked against both stages) -- and only when
    EVERY conflicted path is a writer log it accepts. Anything else, a fork
    included, aborts the whole merge exactly as before: nothing half-merged.

    `keep_both` (the default branch's merge only, SYN-9): every other conflicted
    file is resolved by `_keep_both` and the merge completes, so one file
    waiting for a person no longer stops the whole space. A writer log that is
    not a prefix extension is a fork and still aborts everything.
    """
    rc, mout, err = _git(space, "merge", "--no-edit", ref)
    if rc == 0:
        return True, "", [], []
    # BOTH streams. git reports conflicts on STDOUT ("CONFLICT (content):
    # Merge conflict in ...") and leaves stderr empty, so capturing only
    # stderr yields a failure with a blank reason — the identical defect
    # that hid a nine-day auth outage behind `claude -p failed: `.
    detail = "\n".join(x for x in (mout.strip(), err.strip()) if x)
    ok, detail, logs, kept = resolve_in_progress(space, detail, keep_both=keep_both)
    if ok:
        return True, detail, logs, kept
    _git(space, "merge", "--abort")
    return False, detail, [], []


def resolve_in_progress(space: Path, detail: str = "", *, keep_both: bool = False
                        ) -> tuple[bool, str, list[str], list[dict]]:
    """Finish a merge git stopped on, if every conflict has a lossless answer.

    (ok, detail, writer logs resolved, conflicts kept for a person). On ok the
    merge is committed; otherwise the caller runs `git merge --abort`, which
    undoes anything this wrote, and nothing is half-merged.
    Shared by converge and the fleet sweep (git_fleet_sync), so both answer a
    conflict the same way.
    """
    from resolve_ledger_conflicts import resolve_file, unmerged
    try:
        names = unmerged(space)
        if not names:
            return False, detail, [], []
        logs = [n for n in names if _WRITER_LOG.fullmatch(n)]
        rest = [n for n in names if n not in logs]
        if rest and (not keep_both or any(n.startswith(".datacore/events/") for n in rest)):
            return False, detail, [], []
        stages = _unmerged_stages(space) if rest else {}
        if rest and any(1 not in s and (2 not in s or 3 not in s) for s in stages.values()):
            # Added on one side only yet unmerged: a rename/directory case this
            # does not guess at.
            return False, detail, [], []
        for name in logs:
            resolve_file(space, name)
        if logs:
            rc, aout, aerr = _git(space, "add", "--", *logs)
            if rc != 0:
                return False, detail + "\n" + (aout + aerr).strip(), [], []
        done = [_keep_both(space, name, stages[name]) for name in rest]
        if unmerged(space):
            return False, detail, [], []
        # A .org file merged task by task with nothing left to decide needs no
        # person: it is named in the merge commit, and no task is filed for it.
        kept = [k for k in done if k.get("needs_person", True)]
        settled = [k for k in done if not k.get("needs_person", True)]
        msg = []
        if settled:
            msg += ["-m", f"sync: merge; {', '.join(k['path'] for k in settled)} merged task by "
                          f"task on :ID: (nothing left for a person)"]
        if kept:
            # The commit itself is the record of what waits: one trailer per
            # file, read back by waiting_conflicts on every later sync.
            for k in kept:
                k["task"] = _conflict_item_id(space, k)
            msg += ["-m", f"sync: merge; conflict in {', '.join(k['path'] for k in kept)} "
                          f"kept for a person (a task names what to settle)",
                    "-m", "\n".join(f"{CONFLICT_TRAILER}: {k['task']} {k['path']}" for k in kept)]
        msg = msg or ["--no-edit"]
        rc, cout, cerr = _git(space, "commit", "-q", *msg)
        if rc != 0:
            return False, detail + "\n" + (cout + cerr).strip(), [], []
        _, merge, _ = _git(space, "rev-parse", "HEAD")
        for k in kept:
            k["merge"] = merge.strip()
            _, blob, _ = _git(space, "rev-parse", f"HEAD:{k['path']}")
            k["blob"] = blob.strip()
        return True, detail, logs, kept
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return False, detail + f"\nnot a prefix extension: {exc}", [], []


# ── a content conflict waits for a person; the rest of the space flows ─────
#
# SYN-9, and the owner's decision of 2026-09-30: "let's avoid creating multiple
# copies and excessive backups. If there are conflicting files, the conflict
# should be resolved. It can be a task on its own, even."
#
# So a conflicted file is resolved IN PLACE and the merge completes: no copy
# files, no side branch, no parallel commit. A text file takes git's `union`
# resolution -- both sides' lines, no markers -- which is what the fleet's
# .gitattributes already does for journals (deployed 2026-09-02); each side's
# version also stays in history. A file union cannot hold (binary) keeps this
# host's copy; a file one side deleted keeps the surviving edit. Then ONE
# ledger task, addressed to the owner, names the space, the file and both
# commits, and every sync names the file again until someone edits it or
# closes the task.

CONFLICT_KEY = "sync_conflict"
CONFLICT_TRAILER = "Sync-Conflict"
#: Days a conflict task has before it is due; after that the overdue reports own it.
CONFLICT_DUE_DAYS = 7


def _note_line(n: dict) -> str:
    """One line of the conflict task for one thing the merge decided."""
    what = f"task {n['id']} ({n.get('task') or ''})" if n.get("id") else "the file"
    field_ = n.get("field", "")
    if n.get("theirs") and n.get("ours") and n["ours"] != n["theirs"]:
        return (f"{what}: {field_}: this host's value kept ({n['ours'][:160]!r}); "
                f"the other host had {n['theirs'][:160]!r}")
    return f"{what}: {field_}" + (f" ({n['theirs'][:160]})" if n.get("theirs") else "")


def _unmerged_stages(space: Path) -> dict[str, dict[int, str]]:
    """{path: {stage: blob}} for every unmerged path."""
    rc, out, _ = _git(space, "ls-files", "-u", "-z")
    stages: dict[str, dict[int, str]] = {}
    for row in out.split("\0"):
        if not row.strip():
            continue
        meta, _, path = row.partition("\t")
        _mode, blob, stage = meta.split()
        stages.setdefault(path, {})[int(stage)] = blob
    return stages


def _blob(space: Path, sha: str) -> bytes:
    return subprocess.run(["git", "-C", str(space), "cat-file", "blob", sha],
                          capture_output=True, timeout=60, check=True).stdout


def _keep_both(space: Path, path: str, stages: dict[int, str]) -> dict:
    """Resolve one conflicted path without losing either side; stage it."""
    import tempfile
    how, notes, needs_person = "union", [], True
    if 2 not in stages or 3 not in stages:
        merged, how = _blob(space, stages.get(2) or stages[3]), "kept the surviving edit"
    else:
        ours, theirs = _blob(space, stages[2]), _blob(space, stages[3])
        base = _blob(space, stages[1]) if 1 in stages else b""
        org = None
        if path.endswith(".org") and not any(b"\0" in x for x in (ours, theirs, base)):
            # Owner, 2026-09-30: "merge by task ID, that's why we have
            # org-workspace". One task per :ID:, never a doubled one; a person
            # is asked only for what the merge had to decide (org_sync_merge).
            try:
                from org_sync_merge import merge3
                org = merge3(base.decode(), ours.decode(), theirs.decode())
            except (UnicodeDecodeError, ImportError):
                org = None
        if org is not None:
            merged, how = org.text.encode(), org.how
            notes, needs_person = org.notes, org.needs_person
        elif b"\0" in ours or b"\0" in theirs or b"\0" in base:
            merged, how = ours, "binary: kept this host's copy"
        else:
            with tempfile.TemporaryDirectory() as tmp:
                files = []
                for name, data in (("ours", ours), ("base", base), ("theirs", theirs)):
                    f = Path(tmp) / name
                    f.write_bytes(data)
                    files.append(str(f))
                merged = subprocess.run(["git", "merge-file", "-p", "--union", *files],
                                        capture_output=True, timeout=60).stdout
    (space / path).write_bytes(merged)
    rc, out, err = _git(space, "add", "--", path)
    if rc != 0:
        raise RuntimeError(f"could not stage {path}: {(out + err).strip()}")
    # The commit on each side that last wrote this file: the two versions.
    _, head, _ = _git(space, "log", "-1", "--format=%H", "HEAD", "--", path)
    _, other, _ = _git(space, "log", "-1", "--format=%H", "MERGE_HEAD", "--", path)
    return {"path": path, "how": how, "ours": head.strip(), "theirs": other.strip(),
            "notes": notes, "needs_person": needs_person}


def _conflict_item_id(space: Path, k: dict) -> str:
    import hashlib
    key = f"{Path(space).name}|{k['path']}|{k['ours']}|{k['theirs']}"
    return "sync-conflict-" + hashlib.sha256(key.encode()).hexdigest()[:12]


def file_conflict_tasks(space: Path, kept: list[dict]) -> list[str]:
    """One ledger task per kept conflict, filed once, committed (not pushed).

    Deduplicated by an id derived from space, file and both commits, checked
    against the fold before appending -- the same rule jobs/autofix.py uses, so
    a repeat never spends the writer's daily creation allowance. Returns what
    each conflict is known by: the task id, or why no task could be filed (the
    sync report still names the file either way).
    """
    if not kept:
        return []
    from datetime import datetime, timedelta, timezone
    from actor_identity import this_actor
    from ledger.fold import fold
    from ledger.log import read_events
    from ledger.policy import guarded_append
    import roster
    out, filed = [], []
    try:
        actor = this_actor()
        log = EventLog(space, actor)
        known = fold(read_events(space)).items if (space / ".datacore" / "events").is_dir() else {}
    except Exception as exc:  # noqa: BLE001 -- the report still names the file
        return [f"no task filed: {type(exc).__name__}: {exc}"[:200] for _ in kept]
    owner = roster.owner()
    for k in kept:
        iid = _conflict_item_id(space, k)
        k["task"] = iid
        if iid in known:
            out.append(iid)
            continue
        where = f"{Path(space).name}/{k['path']}"
        decided = "".join(f"\n- {_note_line(n)}" for n in (k.get("notes") or [])[:20])
        body = (f"Syncing {Path(space).name} met a content conflict in {k['path']}.\n"
                f"- this host's commit: {k['ours']}\n- the other side's commit: {k['theirs']}\n"
                f"- merged in: {k.get('merge', '?')} ({k['how']})\n"
                + (f"What the merge decided or kept twice:{decided}\n" if decided else "")
                + f"Both versions are in git history (git show <commit>:{k['path']}). "
                f"Edit the file into the version that should stand and commit, or close this "
                f"task if the merge is right; the rest of the space kept syncing, and every "
                f"sync names the file (without a new alert) until then.")
        # A deadline hands a conflict nobody settles to the ordinary overdue
        # machinery (owner, 2026-09-30: alert once, then quiet; no new loop).
        due = (datetime.now(timezone.utc) + timedelta(days=CONFLICT_DUE_DAYS)).date().isoformat()
        payload = {"id": iid, "title": f"Resolve the sync conflict in {where}", "body": body,
                   "state": "TODO", "deadline": due,
                   CONFLICT_KEY: {kk: k.get(kk) for kk in
                                  ("path", "ours", "theirs", "merge", "blob", "how")}}
        if owner:
            payload["assignee"] = owner
        try:
            guarded_append(log, "item.create", payload)
        except Exception as exc:  # noqa: BLE001 -- the report still names the file
            out.append(f"no task filed: {type(exc).__name__}: {exc}"[:200])
            continue
        filed.append(log.path_for("item.create", payload).relative_to(space).as_posix())
        out.append(iid)
    if filed:
        rels = sorted(set(filed))
        _git(space, "add", "--", *rels)
        _git(space, "commit", "-q", "-m", "ledger: task for a sync conflict", "--only", "--", *rels)
    return out


def waiting_conflicts(space: Path) -> list[tuple[str, str]]:
    """[(path, task id)] for conflicts still waiting for a person.

    The record is the merge commit that kept the conflict (its Sync-Conflict
    trailers), so this holds even when no task could be filed. A file waits
    while it is still exactly what that merge wrote and its task, if the
    ledger has it, is not closed. Most recent merge wins for a path.
    """
    return [(p, i) for p, i, _ in _waiting(space)]


def split_waiting(space: Path, fresh=()) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(alert, handled): the waiting conflicts, split by whether to alert.

    Owner, 2026-09-30: "alert once, then quiet". A conflict is ALERTED by the
    sync that meets it (its path is in `fresh`: this sync merged around it and
    filed the task). After that, while its task is open in the ledger, it is
    HANDLED: every sync still names it, but it is not a failure and not a new
    alert; if nobody settles it, the task's deadline hands it to the ordinary
    overdue reports. A conflict with no task in this ledger (none could be
    filed, or it has not arrived) stays an alert: nothing else tells a person.
    """
    alert, handled = [], []
    for path, iid, filed in _waiting(space):
        (handled if filed and path not in set(fresh) else alert).append((path, iid))
    return alert, handled


def _waiting(space: Path) -> list[tuple[str, str, bool]]:
    """[(path, task id, the task is open in this ledger)], see waiting_conflicts."""
    rc, out, _ = _git(space, "log", "--merges", "-F", f"--grep={CONFLICT_TRAILER}: ",
                      "--format=%H%x00%B%x01", "HEAD")
    if rc != 0 or not out.strip():
        return []
    seen, candidates = set(), []
    for record in out.split("\x01"):
        sha, _, body = record.strip().partition("\0")
        for line in body.splitlines():
            if not line.startswith(f"{CONFLICT_TRAILER}: "):
                continue
            iid, _, path = line[len(CONFLICT_TRAILER) + 2:].strip().partition(" ")
            if not path or path in seen:
                continue
            seen.add(path)
            rc1, now, _ = _git(space, "rev-parse", f"HEAD:{path}")
            rc2, then, _ = _git(space, "rev-parse", f"{sha}:{path}")
            if rc1 == 0 and rc2 == 0 and now.strip() == then.strip():
                candidates.append((path, iid))
    if not candidates:
        return []
    try:
        from ledger.fold import fold
        from ledger.log import read_events
        items = fold(read_events(space)).items if (Path(space) / ".datacore" / "events").is_dir() else {}
    except Exception:  # noqa: BLE001 -- an unreadable ledger cannot close anything
        items = {}
    closed = ("completed", "verified", "dismissed")
    return sorted((p, i, i in items) for p, i in candidates
                  if not (i in items and items[i].status in closed))


_REPO_LOCK_TIMEOUT = 120


def _lock_name(space: Path) -> str:
    """One name per REPOSITORY, not per path: the resolved git-common-dir.

    The publication record lives in the git-common-dir, which every linked
    worktree shares, so a lock keyed by the path's basename let a publication
    reserved from `worktree-7` interleave with the autosave in `0-personal`
    (DatacoreSpec/Publication.lean, `basename_key_not_exclusive`). For the main
    checkout this is still its basename, so existing lock names/inodes and
    cooperating older processes are unchanged. A path git cannot resolve keeps
    the old name.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE')}
    try:
        r = subprocess.run(['git', '-C', str(space), 'rev-parse', '--path-format=absolute',
                            '--git-common-dir'], capture_output=True, text=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError):
        return space.name
    if r.returncode or not r.stdout.strip():
        return space.name
    common = Path(r.stdout.strip()).resolve()
    return common.parent.name if common.name == '.git' else common.name


_HELD = threading.local()


@contextmanager
def _repo_lock(space: Path):
    """Exclusive, per-repo, SAME-MACHINE ONLY. See the module docstring.

    Re-entrant for the thread that already holds it: with one key per
    repository, a nested acquisition from another checkout of the same
    repository (formerly a different key) would otherwise wait out its own
    deadline. Other threads and processes still block (flock is per open file).
    """
    lock_dir = private_state_directory('locks')
    lock = lock_dir / f"{_lock_name(Path(space))}.lock"
    held = getattr(_HELD, 'names', None)
    if held is None:
        held = _HELD.names = set()
    if lock in held:
        yield
        return
    # Retain the existing lock name/inode for cooperating callers. Opening a
    # lock never truncates it or follows an alias; contention has a deadline.
    with file_lock(lock, lock_path=lock, timeout=_REPO_LOCK_TIMEOUT):
        held.add(lock)
        try:
            yield
        finally:
            held.discard(lock)


SHIPPED_REGISTRY = Path(__file__).resolve().parents[2] / ".datacore" / "registry" / "repositories.yaml"


def _data_root() -> Path:
    """Data/configuration location is independent of the installed code tree."""
    return Path(os.environ.get('DATACORE_ROOT', str(Path.home() / 'Data')))


def _registry(root: Path) -> dict:
    """The repository registry: the root's own copy, else the one that ships
    with this code tree.

    A host whose code lives outside its data root (hermes: the runner clone
    holds the code, `~/Data` there is a plain directory of space clones) has no
    `.datacore/registry` under `--root`. Until 2026-09-06 `sync` globbed the
    spaces and classified each against the code tree's registry; the
    registry-driven sync that replaced it read `root/.datacore/registry` only
    and raised FileNotFoundError twice a day on hermes (datacore-fleet-sync,
    from 18:10 UTC 2026-09-06) — a traceback where an outcome belonged."""
    import yaml

    class UniqueKeysLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            self.flatten_mapping(node)
            seen = set()
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in seen:
                    raise ValueError('invalid or duplicate repository configuration key')
                seen.add(key)
            return super().construct_mapping(node, deep=deep)

    p = root / ".datacore" / "registry" / "repositories.yaml"
    if not p.exists() and not p.is_symlink() and SHIPPED_REGISTRY.exists():
        p = SHIPPED_REGISTRY
    try:
        with p.open('rb') as source:
            raw = source.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError('repository configuration is too large')
        document = yaml.load(raw.decode('utf-8'), Loader=UniqueKeysLoader)
        if not isinstance(document, dict) or not isinstance(document.get('repositories'), dict):
            raise ValueError('repository configuration requires a repositories mapping')
        entries = document['repositories']
        for key, entry in entries.items():
            if (not isinstance(key, str) or not key or '\\' in key or '\0' in key
                    or key != '<root>' and any(part in ('', '.', '..') for part in key.split('/'))
                    or not isinstance(entry, dict)
                    or entry.get('category') not in ('knowledge', 'agent-personal', 'code')):
                raise ValueError('invalid repository path or category')
        return entries
    except (OSError, UnicodeError, yaml.YAMLError, TypeError, ValueError):
        raise ValueError('repository registry is unavailable or invalid') from None


def _marker_space(space: Path, root: Path) -> bool:
    """Is `space` a top-level space under `root` that declares itself by marker?"""
    try:
        if Path(space).resolve().parent != Path(root).resolve():
            return False
        from spaces import read_marker
        return read_marker(Path(space)) is not None
    except (OSError, ValueError, RuntimeError, ImportError):
        return False


def classify(space: Path, root: Path | None = None) -> Result:
    """The repo's category, or a refusal.

    An unregistered repository is REFUSED, never defaulted. Defaulting would
    hand a production repo the knowledge rules — direct pushes to a default
    branch — which is the single mistake the two categories exist to prevent
    (DIP-0046 §1).
    """
    root = Path(root) if root is not None else _data_root()
    try:
        reg = _registry(root)
        if not isinstance(reg, dict):
            raise ValueError('invalid registry')
    except (OSError, ValueError, TypeError, AttributeError):
        return Result(False, 'repository registry is unavailable or invalid', {})
    key = "<root>" if space.resolve() == root.resolve() else space.name
    entry = reg.get(key)

    # FALL BACK TO THE REMOTE'S NAME, because the directory name is a LOCAL
    # fact. Tris on hermes clones the same repos under different names —
    # `2-plur` there is `5-plur` here, `1-datacore` is `2-datacore` — so a
    # name-keyed registry refuses every one of its spaces and the transport
    # cannot be used on that machine at all. The remote's basename is the same
    # everywhere. (Basename only: this registry is tracked in a PUBLIC repo and
    # must carry no host or address.)
    if not entry:
        rc, out, _ = _git(space, "remote", "get-url", "origin")
        if rc == 0 and out.strip():
            import re as _re
            name = _re.sub(r"\.git$", "", out.strip().rstrip("/").split("/")[-1])
            entry = next((v for v in reg.values() if isinstance(v, dict) and v.get("repo") == name), None)

    # A SPACE IS REGISTERED BY ITS MARKER (SPC-9). A new top-level space
    # declares itself in `<space>/.datacore/config.yaml`, which discovery,
    # context and the ledger already read; demanding a second entry in the
    # tracked registry meant a new space silently never synced (audit C12).
    # A space is knowledge by definition. Only a direct child of the data root:
    # a nested (client) space keeps needing an explicit registry entry.
    if not entry and _marker_space(space, root):
        entry = {"category": "knowledge", "via": "space marker"}

    if not entry:
        return Result(False, "repository not in registry/repositories.yaml",
                      {"repo": key, "fix": "classify it as knowledge, code or agent-personal"})
    if not isinstance(entry, dict) or entry.get('category') not in ('knowledge', 'agent-personal', 'code'):
        return Result(False, 'repository category is invalid; no synchronization permitted', {})
    return Result(True, entry['category'], {"entry": entry})


def default_branch(space: Path) -> str:
    rc, out, _ = _git(space, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    out = out.strip()
    return out.split("/", 1)[1] if rc == 0 and out.startswith("origin/") else "main"


def _in_progress(space: Path) -> str:
    """What is half-finished in this repo: 'merge' | 'rebase' | 'cherry-pick' |
    'revert' | 'conflict markers' | ''.

    Both signals matter. MERGE_HEAD says git is mid-merge; leftover markers
    with no MERGE_HEAD say a person aborted the merge and left the file — a
    tree the space pre-commit hook refuses where it is installed, and nothing
    refused where it is not.
    """
    rc, gitdir, _ = _git(space, "rev-parse", "--git-dir")
    if rc == 0 and gitdir.strip():
        g = Path(gitdir.strip())
        if not g.is_absolute():
            g = space / g
        for marker, name in (("MERGE_HEAD", "merge"), ("rebase-merge", "rebase"),
                             ("rebase-apply", "rebase"), ("CHERRY_PICK_HEAD", "cherry-pick"),
                             ("REVERT_HEAD", "revert")):
            if (g / marker).exists():
                return name
    for args in (("diff", "--check"), ("diff", "--cached", "--check")):
        _, out, _ = _git(space, *args)
        if "leftover conflict marker" in out:
            return "conflict markers"
    return ""


_WRITER_LOG = re.compile(r"(?:^|/)\.datacore/events/([A-Za-z0-9_-]+)\.jsonl$")


def _own_principal() -> tuple[str | None, str]:
    """This host's principal and the actor it writes as (DIP-0044); (None, "")
    when identity cannot be resolved — the guard then stands down rather than
    refusing every autosave on a host with a half-configured identity."""
    try:
        from actor_identity import principal_of, this_actor
        actor = this_actor()
        return principal_of(actor)[0], actor
    except Exception:  # noqa: BLE001 — identity is advisory here, never a crash
        return None, ""


def _principal_of(writer: str) -> str | None:
    try:
        from actor_identity import principal_of
        return principal_of(writer)[0]
    except Exception:  # noqa: BLE001
        return None


def own_writer_log(path: str) -> bool:
    """Is `path` a per-writer event log that belongs to THIS host's principal?

    False whenever it cannot be decided -- no identity, an undeclared writer,
    a path that is not a writer log -- so a caller that treats "not own" as
    "someone else's work" fails closed. A foreign principal's log is never own.
    """
    m = _WRITER_LOG.search(str(path).strip())
    if not m:
        return False
    own, _ = _own_principal()
    return bool(own) and _principal_of(m.group(1)) == own


def foreign_writer_logs(space: Path) -> list[tuple[str, str, str]]:
    """Staged writer logs that belong to a DIFFERENT declared principal than
    this host's: (path, writer, principal).

    Only a declared-against-declared mismatch counts. A writer nobody has
    declared (a hostname-derived log from before DIP-0044) is still autosaved
    as before, and a host whose own principal is unknown refuses nothing:
    the guard exists for the one case the registry can actually decide."""
    own, _ = _own_principal()
    if not own:
        return []
    _, staged, _ = _git(space, "diff", "--cached", "--name-only")
    out = []
    for line in (staged or "").splitlines():
        m = _WRITER_LOG.search(line.strip())
        if not m:
            continue
        writer = m.group(1)
        principal = _principal_of(writer)
        if principal and principal != own:
            out.append((line.strip(), writer, principal))
    return out


def converge(space: Path, *, root: Path | None = None) -> Result:
    """Apply the registered category at every entry point, including direct calls."""
    cat = classify(space, root)
    if not cat:
        return cat
    if cat.reason not in ('knowledge', 'agent-personal', 'code'):
        return Result(False, 'repository category does not permit synchronization', {})
    with _repo_lock(space):
        if cat.reason == 'code':
            outcome = _code_update(space)
            return Result(outcome == 'clean', f'code repository: {outcome}', {'outcome': outcome})
        return _converge_locked(space)


def _fetch_reason(err: str) -> str:
    """Name the failure the operator has to act on.

    Ordered most-specific first: a denied key and an unknown host both mention
    the host, so matching on the host alone would swallow the auth case.
    """
    e = err.lower()
    if "permission denied" in e or "authentication failed" in e:
        # "check your key OR your route": a VPN or exit node can put a different
        # host on the far end of the same address, which answers and rejects the
        # key — identical symptom, completely different fix. Naming only the key
        # sends the operator to regenerate credentials that were never wrong.
        return "auth denied (key rejected — check the key, or a VPN/exit node)"
    if "host key verification failed" in e:
        return "host key not trusted"
    if "repository not found" in e or "does not appear to be a git repo" in e:
        return "remote repo missing"
    return "fetch failed (offline?)"


#: A file this large is never shared (GitHub refuses 100 MB; the fleet's own
#: limit, as in git_fleet_sync, is 50 MB). Same threshold everywhere (SYN-3).
MAX_SHARED_BYTES = 50 * 1024 * 1024


def unshareable(space: Path, paths: list[str] | None = None) -> dict[str, str]:
    """Staged paths that must never reach the shared copy, with why (SYN-3).

    Conflict markers (`git diff --cached --check` sees an UNTRACKED file once
    `add -A` staged it, which the working-tree check before the autosave
    cannot) and files of MAX_SHARED_BYTES or more. `paths` limits the check.
    """
    bad: dict[str, str] = {}
    _, out, _ = _git(space, "-c", "core.quotepath=off", "diff", "--cached", "--check",
                     "--", *(paths or []))
    for line in out.splitlines():
        if "leftover conflict marker" in line:
            bad.setdefault(line.split(":", 1)[0], "conflict markers")
    _, names, _ = _git(space, "-c", "core.quotepath=off", "diff", "--cached", "--name-only",
                       "--diff-filter=AM", "--", *(paths or []))
    for rel in names.splitlines():
        try:
            size = (space / rel).stat().st_size
        except OSError:
            continue
        if size >= MAX_SHARED_BYTES:
            bad.setdefault(rel, f"too large to share ({size / 1048576:.0f} MB ≥ 50 MB)")
    return bad


def refused_by_hook(repo: Path, paths: list[str]) -> tuple[list[str], str]:
    """Which of `paths` the pre-commit hook refuses, and what it said (SYN-8).

    A hook refuses a COMMIT, not a file, so ask it about subsets: stage a
    subset in a scratch index (the real index is not touched), run the hook
    against it, and bisect. One refused inbox capture then holds back that
    file alone instead of the whole space (2-datacore, 2026-09-26). If no
    subset is refused on its own -- a hook that objects to a combination, or
    to something other than the files -- every path is reported refused,
    which is the old whole-stop behaviour.
    """
    import tempfile
    rc, hook, _ = _git(repo, "rev-parse", "--git-path", "hooks/pre-commit")
    hook_path = Path(hook.strip()) if rc == 0 and hook.strip() else None
    if hook_path is not None and not hook_path.is_absolute():
        hook_path = repo / hook_path
    if hook_path is None or not os.access(hook_path, os.X_OK):
        return list(paths), ""
    said: list[str] = []

    def refuses(subset: list[str]) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**_net_env(), "GIT_INDEX_FILE": str(Path(tmp) / "index")}
            try:
                subprocess.run(["git", "read-tree", "HEAD"], cwd=repo, env=env,
                               capture_output=True, timeout=60, check=True)
                subprocess.run(["git", "add", "-A", "--", *subset], cwd=repo, env=env,
                               capture_output=True, timeout=120, check=True)
                r = subprocess.run([str(hook_path)], cwd=repo, env=env, capture_output=True,
                                   text=True, timeout=300)
            except (OSError, subprocess.SubprocessError):
                return True
            if r.returncode != 0:
                said.append(((r.stdout or "") + (r.stderr or "")).strip())
            return r.returncode != 0

    def bisect(subset: list[str]) -> list[str]:
        if not refuses(subset):
            return []
        if len(subset) == 1:
            return subset
        mid = len(subset) // 2
        return bisect(subset[:mid]) + bisect(subset[mid:])

    refused = bisect(list(paths))
    return (refused or list(paths)), "\n".join(dict.fromkeys(x for x in said if x))


def _incoming_rewrites(space: Path, ref: str) -> list[str]:
    """Would merging `ref` change or remove history this machine already holds?

    A three-way merge takes the incoming copy of a log WHOLE when this side
    never touched it since the merge base. So an origin commit that edited
    event 0 and re-chained the rest (audit A#2), truncated a log or deleted it,
    arrives as a clean fast-forward and silently replaces the held history
    (LED-2). Every log `ref` changed since the merge base must extend its
    base copy byte for byte -- the append-only rule the write gate applies to
    a push (hooks/ledger_write_gate.check_change), applied here to what we
    receive. [] means the incoming side only appended.
    """
    rc, base, _ = _git(space, "merge-base", "HEAD", ref)
    if rc != 0 or not base.strip():
        return []                           # nothing held in common yet
    base = base.strip()
    hooks = str(Path(__file__).resolve().parent / "hooks")
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    from ledger_write_gate import PATHSPEC, check_change
    rc, out, err = _git(space, "diff", "--no-renames", "--name-only", base, ref, "--", PATHSPEC)
    if rc != 0:
        return [f"incoming ledger changes could not be listed: {err.strip()[:120]}"]

    def blob(rev: str, rel: str) -> bytes | None:
        try:
            r = subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=space,
                               capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout if r.returncode == 0 else None

    bad = []
    for rel in (x.strip() for x in out.splitlines()):
        if not rel.endswith(".jsonl"):
            continue
        old = blob(base, rel)
        if not old:
            continue                        # a new log: nothing held to lose
        bad += [e for e in check_change(rel, old, blob(ref, rel)) if "append-only" in e]
    return bad


def _private_context_staged(space: Path) -> list[str]:
    """Staged paths that are private context (context_merge.private_context_reason)."""
    from context_merge import private_context_reason
    rc, out, _ = _git(space, "diff", "--cached", "--name-status", "--no-renames")
    found = []
    for line in (out or "").splitlines() if rc == 0 else []:
        status, _, path = line.partition("\t")
        if path and status != "D" and private_context_reason(space / path, tracked=status != "A"):
            found.append(path)
    return found


def _rewrite_refusal(db: str, ref: str, rewrites: list[str], autosaved: bool) -> Result:
    return Result(False,
                  f"refused: {ref} rewrites ledger history this machine holds — "
                  f"{'; '.join(rewrites)[:240]}. Nothing was merged; history is never "
                  f"rewritten (a bad record is voided in-ledger). A human must find who "
                  f"published it",
                  {"branch": db, "ref": ref, "autosaved": autosaved, "ledger_rewrite": rewrites})


def _converge_locked(space: Path, *, publish: bool = True) -> Result:
    """converge() with the repo lock ALREADY HELD.

    The split is not stylistic. `append()` holds the lock and, on a
    non-fast-forward push, must converge before retrying — calling the public
    `converge()` there re-enters `flock` on a second file descriptor for the
    same file and deadlocks the process against itself. It hung for two minutes
    on the first end-to-end smoke test and had to be killed, which is a better
    place to find it than a nightly run.
    """
    rc, _, err = _git(space, "fetch", "--prune", "origin")
    if rc != 0:
        # Offline is not an error state — it is a condition. Report it and
        # let the caller decide; a sweep that treats offline as failure
        # stops working on a train.
        #
        # But DENIED IS NOT OFFLINE, and collapsing them is the exact defect
        # this module exists to remove. Offline says "wait, you are on a
        # train"; denied says "your key stopped working, four spaces are not
        # syncing and will not start on their own". On 2026-08-11 Gitea began
        # rejecting the Mac's ed25519 key mid-afternoon and all four Gitea
        # spaces reported `offline` — indistinguishable, in a sweep summary,
        # from a closed laptop lid.
        return Result(False, _fetch_reason(err), {"stderr": err.strip()[:200]})
    db = default_branch(space)

    # Never autosave a half-finished merge. A converge that reaches a repo
    # whose previous merge stopped on a conflict — markers in the tree,
    # MERGE_HEAD in .git — would `add -A` the markers, commit them as an
    # autosave and push them to the shared remote. `sync push` did exactly
    # that (datacore#28). An in-progress merge, rebase or cherry-pick belongs
    # to whoever started it; the converge steps back and names what it found.
    busy = _in_progress(space)
    if busy:
        return Result(False, f"{busy} in progress — finish or abort it by hand, then converge",
                      {"branch": db, "in_progress": busy})

    # Autosave BEFORE merging. A dirty working file makes git refuse the
    # merge, and refusing forever means never converging — 2-datacore was
    # 45 behind when this was written. Commit, never stash: ENG-2026-0729-009
    # cost 10 orphaned stashes over six weeks because a stash is invisible
    # while a commit is on a branch, findable and pushable.
    rc, out, _ = _git(space, "status", "--porcelain")
    autosaved = bool(out.strip())
    held_back: dict[str, str] = {}
    hook_detail = ""
    if autosaved:
        _git(space, "add", "-A")
        # NEVER autosave a submodule pointer. `add -A` stages a changed
        # gitlink, so an unattended converge would silently move `.datacore/dips`
        # to whatever commit happens to be checked out locally — publishing a
        # DIP revision nobody chose to publish, as a side effect of syncing
        # something else. Bumping a pointer is a deliberate act; unstage them and
        # leave the change in the working tree where it stays visible.
        # Read GITLINKS FROM THE INDEX, not `git submodule foreach`.
        #
        # foreach ABORTS ON THE FIRST ERROR. Hermes has a gitlink whose path has
        # no url in .gitmodules, so foreach emitted one entry, died, and this
        # guard unstaged only that one — then committed three space pointers
        # (1-datacore, 2-plur, 3-firm) exactly as if the guard were not there.
        # A protection that depends on unrelated config being well-formed is not
        # a protection.
        #
        # Mode 160000 in the index IS a gitlink, by definition, whether or not
        # .gitmodules knows about it. Orphan gitlinks — committed without
        # submodule config — are precisely the ones nothing else would catch.
        rc, staged, _ = _git(space, "diff", "--cached", "--raw")
        for line in (staged or "").splitlines():
            # ":100644 160000 <sha> <sha> M\tpath"
            if line.startswith(":") and " 160000 " in line[:40]:
                path = line.split("\t", 1)[-1].strip()
                if path:
                    _git(space, "restore", "--staged", "--", path)

        # NEVER AUTOSAVE PRIVATE CONTEXT (SPC-3). `add -A` stages whatever the
        # space's .gitignore fails to list, and a hand-made or fresh space may
        # list nothing -- so CLAUDE.local.md, or a composed CLAUDE.md holding
        # it, was committed and pushed. Judge the file, not the .gitignore.
        for path in _private_context_staged(space):
            _git(space, "restore", "--staged", "--", path)

        # Unstaging the submodules may have emptied the index. `git commit` then
        # exits non-zero for "nothing to commit", which the check below would
        # report as a REFUSED autosave and abort the whole converge — so a repo
        # whose only change is a submodule pointer could never sync again.
        # Observed on nightshift: 2 commits ahead, 7 behind, dirty only in
        # `.datacore/dips`, unable to converge at all.
        # NEVER AUTOSAVE ANOTHER PRINCIPAL'S WRITER LOG. Per-writer logs are
        # the ledger's authorship (DIP-0044): `<space>/.datacore/events/tris.jsonl`
        # is Tris's word and nobody else's. On 2026-09-06 and again on
        # 2026-09-07 a cadence on hermes appended to `winston.jsonl`, and this
        # autosave committed the file under Tris's name — an approval-capable
        # log carrying events its principal never made, signed into main by
        # a host that is not its principal's. The verifier caught it after the
        # fact; the transport must not carry it in the first place. Unstage
        # it, commit the rest, and stop: the file stays in the working tree,
        # visible, for whoever owns the process that wrote it.
        foreign = foreign_writer_logs(space)
        for path, _writer, _principal in foreign:
            _git(space, "restore", "--staged", "--", path)

        # A WRONG WEEKDAY IS REPAIRED, NOT A REASON TO STOP THE FLEET. The
        # weekday in `[2026-09-23 Tue]` is derived from the date, so a wrong one
        # is a formatting error the date tool corrects deterministically. On
        # 2026-09-23 an agent wrote "Tue" for a Wednesday in 6-meridian's inbox;
        # the pre-commit hook refused this autosave (correctly), and that
        # refusal failed the whole phase-1 cycle -- and the agent kept writing
        # new wrong stamps while it was being fixed by hand. The hook stays the
        # guard for everything it cannot repair; this runs the same validator's
        # fixer on the same staged files first and says what it changed.
        repaired = _repair_weekdays(space)
        if repaired:
            print(f"  autosave {space.name}: repaired weekday names in {', '.join(repaired)}")

        # NEVER SHARE BAD STATE (SYN-3): a file with conflict markers or one
        # too large for the shared copy is unstaged, left in the working tree
        # and named -- and the rest of the space still syncs (SYN-8).
        for rel, why in unshareable(space).items():
            _git(space, "restore", "--staged", "--", rel)
            held_back[rel] = why

        commit_msg = ["-m", "ledger: autosave before converge"]
        if os.environ.get("DATACORE_AUTOSAVE_TRAILER"):
            # A caller's attribution (cos_sync: Winston as co-author), so it no
            # longer needs an autosave of its own ahead of these checks.
            commit_msg += ["-m", os.environ["DATACORE_AUTOSAVE_TRAILER"]]
        rc_staged, _, _ = _git(space, "diff", "--cached", "--quiet")
        if rc_staged == 0:                      # 0 = no staged changes remain
            autosaved = False
            crc, cout, cerr = 0, "", ""
        else:
            crc, cout, cerr = _git(space, "commit", *commit_msg)
        if crc != 0:
            # A REFUSED AUTOSAVE HOLDS BACK THE REFUSED FILES, NOT THE SPACE.
            # Not --no-verify: the hook is a guard doing its job, and its own
            # words are the reason. But one invalid org tag on line 6042 used to
            # stop the whole converge (LS-11): nothing else in 2-datacore moved
            # from 2026-09-26 04:25Z, hourly. Ask the hook which files it
            # refuses, unstage those (they stay in the working tree, untouched),
            # commit the rest, and carry on (SYN-8).
            detail = (cout + cerr).strip()
            _, staged_out, _ = _git(space, "-c", "core.quotepath=off", "diff", "--cached",
                                    "--name-only")
            staged_paths = [x for x in staged_out.splitlines() if x.strip()]
            refused, said = refused_by_hook(space, staged_paths)
            if said:
                detail = said
            for rel in refused:
                _git(space, "restore", "--staged", "--", rel)
                held_back[rel] = "refused by pre-commit hook"
            rc_staged, _, _ = _git(space, "diff", "--cached", "--quiet")
            if rc_staged == 0:
                autosaved, crc = False, 0
            else:
                crc, cout, cerr = _git(space, "commit", *commit_msg)
            if crc != 0:
                detail = (cout + cerr).strip() or detail
                return Result(False, "autosave refused by pre-commit hook",
                              {"detail": detail[:400], "held_back": sorted(held_back)})
            hook_detail = detail
        if foreign:
            own_principal, own_actor = _own_principal()
            named = "; ".join(f"{path} belongs to {principal} (writer {writer})"
                              for path, writer, principal in foreign)
            return Result(False,
                          f"foreign writer log left uncommitted: {named} — this host writes "
                          f"as {own_actor or '?'} for {own_principal or 'an undeclared principal'}; "
                          f"find the process writing under that name and stop it (DIP-0044)",
                          {"branch": db, "foreign": [p for p, _, _ in foreign]})

    rewrites = _incoming_rewrites(space, f"origin/{db}")
    if rewrites:
        return _rewrite_refusal(db, f"origin/{db}", rewrites, autosaved)
    ok, err, resolved, kept = _merge(space, f"origin/{db}", keep_both=True)
    if kept:
        # SYN-9: the merge completed around the conflicted file(s); a person
        # gets one task each, and the rest of the space publishes below.
        file_conflict_tasks(space, kept)
    if not ok:
        # Never reset, never rescue-branch, never discard. A conflict here
        # is genuine disagreement about content and belongs to a human; the
        # autosave above guarantees their work is already committed.
        return Result(False, "merge conflict — human needed",
                      {"branch": db, "autosaved": autosaved,
                       "detail": err[:400]})
    # PER-ACTOR LEDGER REFS. A satellite writer publishes its claims on its
    # own ref (refs/heads/ledger/<actor>: plur-claw's dispatcher since
    # 2026-08-10) because only that writer pushes there and a push can never
    # race. But nothing ever folded those refs back into the default branch:
    # Data's last claims sat on ledger/data from 2026-08-11 and reached main
    # only by hand. Every converge now merges each origin/ledger/* ref into the
    # branch before publishing. Per-writer logs are disjoint files, so this is
    # a union -- except where one writer's log reaches main by two paths (the
    # claim path publishes to ledger/<actor> while main carries the same log
    # further). That is one history, not a disagreement: see `_merge`.
    rc, refs_out, _ = _git(space, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/ledger/")
    merged_refs = []
    for ref in (refs_out.split() if rc == 0 else []):
        rc, ahead, _ = _git(space, "rev-list", "--count", f"HEAD..{ref}")
        if rc != 0 or ahead.strip() == "0":
            continue
        rewrites = _incoming_rewrites(space, ref)
        if rewrites:
            return _rewrite_refusal(db, ref, rewrites, autosaved)
        ok, err, prefixes, _ = _merge(space, ref)
        if not ok:
            return Result(False, "merge conflict on a ledger ref — human needed",
                          {"branch": db, "ref": ref, "autosaved": autosaved, "detail": err[:400]})
        merged_refs.append(ref)
        resolved += prefixes
    # PUBLISH. Converge previously stopped here, which made it a one-way
    # operation: it pulled and never pushed. Every caller means both — `sync`,
    # `./sync pull`, and cos_sync on winston's 15-minute cron all report
    # "synced clean" from this Result. Measured before the fix: 5-plur sat 2
    # commits ahead of a reachable GitHub remote, silently, and nightshift held
    # 4 including a learning-classifier fix and a 140-line audit script.
    #
    # `publish=False` only for _push_with_retry, which calls this to resolve a
    # non-fast-forward and would otherwise recurse into pushing.
    if not publish:
        return Result(True, "converged", {"branch": db, "autosaved": autosaved, "ledger_refs": merged_refs,
                                          "ledger_prefixes": resolved})
    pr = _push_with_retry(space, db)
    if not pr.ok:
        return Result(False, f"converged but not published: {pr.reason}",
                      {"branch": db, "autosaved": autosaved, **pr.context})
    # Alert once, then quiet (owner, 2026-09-30): a conflict this sync met is
    # an alert; one whose task is already open is named but handled.
    alert, handled = split_waiting(space, fresh=[k["path"] for k in kept])
    quiet = ("conflict waiting for a person, already reported: " + "; ".join(
        f"{path} (task {iid} is open)" for path, iid in handled)) if handled else ""
    if held_back or alert:
        # Everything else synced; these did not, and say why (SYN-3, SYN-8),
        # or they wait for a person to settle a conflict (SYN-9).
        parts = []
        if held_back:
            named = "; ".join(f"{rel} ({why})" for rel, why in sorted(held_back.items()))
            refused = any(why.startswith("refused by") for why in held_back.values())
            parts.append(f"{'autosave refused by pre-commit hook — ' if refused else ''}"
                         f"held back, still only on this machine: {named}")
        if alert:
            parts.append("conflict waiting for a person: " + "; ".join(
                f"{path} (merged around it, nothing lost; task {iid})" for path, iid in alert))
        if quiet:
            parts.append(quiet)
        # Not "synced" beside a conflict: that word reads as success (SYN-6).
        return Result(False, "; ".join(parts) + ("; the rest of the space went through"
                                                 if alert or handled else "; everything else synced"),
                      {"branch": db, "autosaved": autosaved, "held_back": sorted(held_back),
                       "conflicts": [p for p, _ in alert], "conflicts_handled": handled,
                       "detail": hook_detail[:400], "pushed": pr.context.get("attempts", 1)})
    if handled:
        # Handled, not success: named on every sync, never a success word
        # (SYN-6), never a failure or a second alert.
        return Result(True, quiet + "; the rest of the space went through",
                      {"branch": db, "autosaved": autosaved, "conflicts_handled": handled,
                       "pushed": pr.context.get("attempts", 1), "ledger_refs": merged_refs,
                       "ledger_prefixes": resolved})
    return Result(True, "converged", {"branch": db, "autosaved": autosaved,
                                      "pushed": pr.context.get("attempts", 1), "ledger_refs": merged_refs,
                                      "ledger_prefixes": resolved})


def _publication_forks(space: Path, commit: str, db: str) -> list[str]:
    """git_relay.publication_forks against origin/<db>; [] means safe to push."""
    from git_relay import publication_forks
    return publication_forks(space, commit, f'origin/{db}')


def _fork_refusal(space: Path, db: str, forks: list[str]) -> str:
    """The refusal, naming what is wrong and the way out. Nothing was pushed."""
    return (f"push REFUSED — ledger fork against origin/{db}: {'; '.join(forks)[:240]}. "
            f"The commit stays local and origin is unchanged. Recover: inspect with "
            f"`python3 .datacore/lib/git_relay.py --forks`; restore the log to origin's "
            f"history with `python3 .datacore/lib/ledger_restore_prefix.py --space "
            f"{Path(space).name} --actor <writer> --find` (a genuinely divergent chain "
            f"needs a human), then converge again")


def _push_with_retry(space: Path, db: str) -> Result:
    """Push, converging and retrying on non-fast-forward.

    This is the cross-machine race. `flock` cannot help: the competing writer is
    on another host. Bounded because an unbounded retry against a genuinely
    diverged remote is a spin, not a recovery.
    """
    from git_publication import push_arguments
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        rc, captured, _ = _git(space, 'rev-parse', '--verify', 'HEAD^{commit}')
        if rc:
            return Result(False, 'publication commit cannot be established')
        try:
            args = push_arguments(captured.strip(), f'refs/heads/{db}')
        except ValueError:
            return Result(False, 'publication commit/ref is invalid')
        # NEVER PUSH A FORK (owner decision L9). A CLEAN merge of origin/<db>
        # or of an origin/ledger/* ref takes one side's log whole when the
        # other side never touched it, so a host that rewound its log and
        # re-appended arrives as a valid chain that replaces events origin
        # already holds (GitFleet.lean `merge_rewrite_is_not_union`). Check
        # what this push PUBLISHES against what origin holds, with the same
        # gate the relay, the fleet sweep and the rollout use. Re-checked on
        # every attempt: a retry pushes a new merge.
        forks = _publication_forks(space, captured.strip(), db)
        if forks:
            return Result(False, _fork_refusal(space, db, forks),
                          {"attempt": attempt, "ledger_fork": forks})
        rc, _, err = _git(space, *args)
        if rc == 0:
            return Result(True, "pushed", {"attempts": attempt})
        low = err.lower()
        if "non-fast-forward" in low or "fetch first" in low or "rejected" in low:
            c = _converge_locked(space, publish=False)
            if not c:
                return Result(False, "push rejected and converge failed",
                              {"attempt": attempt, "converge": c.reason})
            continue
        # CLASSIFY THE PUSH TOO. This module exists to draw one distinction --
        # offline says wait, you are on a train; denied says your key stopped
        # working and four spaces will not sync on their own -- and it was drawn
        # on the FETCH only. Every other push failure came back as one untyped
        # sentence, so a laptop that slept between the fetch and the push wrote
        # FAIL into the cycle's status, which is the false alarm the fetch-side
        # classification was written to end. The classifier reads the same
        # stderr and already answers correctly for both cases.
        return Result(False, f"push {_fetch_reason(err)}",
                      {"attempt": attempt, "stderr": err.strip()[:200]})
    return Result(False, f"push still rejected after {PUSH_ATTEMPTS} attempts",
                  {"hint": "remote is moving faster than we can converge"})


def append(space: Path, actor: str, type: str, payload: dict,
           *, push: bool = True, root: Path | None = None) -> Result:
    """Publish one fact.

    Order matters and is the point: the event is appended, committed, and only
    then pushed — and a FAILED PUSH IS REPORTED, never swallowed. The caller
    learns that the fact exists only on this disk, which is precisely what
    `git push … || true` hid while printing "synced clean".
    """
    cat = classify(space, root)
    if not cat:
        return cat
    if cat.reason not in ('knowledge', 'agent-personal'):
        return Result(False, 'repository category does not permit direct fact publication', {})
    with _repo_lock(space):
        try:
            log = EventLog(space, actor)
            event = log.append(type, payload)
        except Exception as exc:  # noqa: BLE001 — a bad event type is the caller's bug
            return Result(False, "append rejected", {"error": exc.__class__.__name__})

        # The file it landed in: a telemetry type goes to the telemetry log (LED-8).
        rel = log.path_for(type, payload).relative_to(space).as_posix()
        rc, _, err = _git(space, "add", "--", rel)
        if rc != 0:
            return Result(False, "git add failed", {"stderr": err.strip()[:200]})
        rc, _, err = _git(space, "commit", "-m",
                          f"ledger: {type} by {actor}",
                          "--only", "--", rel)
        if rc != 0 and "nothing to commit" not in (err or "").lower():
            return Result(False, "commit failed", {"stderr": err.strip()[:200]})

        if not push:
            return Result(True, "appended (push deferred)",
                          {"event": event.hlc, "pushed": False})
        db = default_branch(space)
        pushed = _push_with_retry(space, db)

    ctx = {"event": event.hlc, "pushed": pushed.ok}
    if not pushed:
        # The fact is real and durable locally; it is simply not published yet.
        # seq-gap will keep reporting it until it is, which is the safety net.
        return Result(False, f"appended but NOT published: {pushed.reason}",
                      {**ctx, **pushed.context})
    return Result(True, "appended and published", ctx)


def gaps(space: Path) -> Result:
    """What exists here and nowhere else. Delegates to the detector so there is
    one definition of the question."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "detectors"))
    from seq_gap import scan_space  # noqa: E402
    rows = scan_space(space)
    ungapped = [r for r in rows if r.get("gap")]
    # PENDING IS NOT PUBLISHED. The detector computes both: `gap` is what has
    # been unpublished long enough to alert on (a 90-minute grace, so the :05
    # and :10 sweeps do not flap), and `pending` is what is on this disk and
    # nowhere else RIGHT NOW. This function answered the second question with
    # the first one's number, so `append()`'s own safety net -- "seq-gap will
    # keep reporting it until it is [published]" -- said "all published" with
    # three events sitting locally. Found by a fleet stress test, 2026-09-18.
    pending = [r for r in rows if r.get("pending") and not r.get("gap")]
    if ungapped:
        reason = f"{len(ungapped)} log(s) unpublished"
    elif pending:
        reason = (f"{sum(r['pending'] for r in pending)} event(s) not yet published "
                  f"in {len(pending)} log(s), inside the grace window")
    else:
        reason = "all published"
    return Result(not ungapped and not pending, reason, {"rows": rows})


def sync_repo(repo: Path, quiet: bool = False, *, root: Path | None = None) -> str:
    """Converge one repo, reported in the operator's vocabulary.

    This was `space_sync.py`: first 142 lines reimplementing the algorithm
    `cos_sync.sh` already had for the box, then an 80-line shim over converge.
    A shim is still a file, still a name to remember, and still a place for the
    next fix to land on one side only — which is the defect DIP-0046 exists to
    remove, not a smaller instance of it worth keeping.

    'clean' | 'waiting' | 'offline' | 'blocked' | 'conflict' | 'skipped'.
    'waiting' is a conflict already reported whose task is still open: it
    needs nothing new from anyone and is not an alert. The distinction
    that earns its keep is offline vs blocked: offline clears itself when the
    laptop reopens, blocked never does.
    """
    res = converge(Path(repo), root=root) if root is not None else converge(Path(repo))
    if res.ok and res.context.get("conflicts_handled"):
        # A conflict already reported, its task open: not a failure, not clean.
        outcome = "waiting"
    elif res.ok:
        outcome = "clean"
    elif "not in registry" in res.reason:
        # Refused, not failed: an unregistered repo has no category, so there is
        # no rule to apply. Defaulting silently is what DIP-0046 §1 forbids.
        outcome = "skipped"
    elif any(s in res.reason for s in ("auth denied", "host key", "repo missing",
                                       "autosave refused")):
        outcome = "blocked"
    elif "offline" in res.reason or "fetch failed" in res.reason:
        outcome = "offline"
    else:
        outcome = "conflict"
    if not quiet:
        detail = res.context.get("detail", "")
        print(f"{Path(repo).name}: {outcome}"
              + (f" — {res.reason}" if not res.ok or outcome == "waiting" else "")
              + (f"\n  {detail.splitlines()[0]}" if detail else ""))
    return outcome


def converge_line(op: str, space: Path, res: Result) -> str:
    """One log line: space, ok / waiting / FAIL, reason. 'waiting' is a
    conflict already reported with its task open -- not ok in words (SYN-6),
    not a failure (owner, 2026-09-30: alert once, then quiet)."""
    label = "FAIL" if not res.ok else ("waiting" if res.context.get("conflicts_handled") else "ok")
    return f"{op} {Path(space).name}: {label} — {res.reason}"


def _code_update(repo: Path) -> str:
    """Bring a CODE repo forward without ever committing for it.

    The retired `./sync` pulled these with `--autostash` and swallowed the
    result (datacore#31). A code repo is a person's working tree: fetch, then
    fast-forward the default branch when it is checked out and clean enough
    to move. Anything else is reported in the operator's vocabulary and left
    exactly as found.

    'clean' | 'dirty' | 'parked' | 'diverged' | 'offline' | 'blocked'
    """
    rc, out, _ = _git(repo, "status", "--porcelain")
    dirty = bool(out.strip())
    rc, _, err = _git(repo, "fetch", "-q", "origin")
    if rc != 0:
        reason = _fetch_reason(err)
        return "offline" if "offline" in reason else "blocked"
    db = default_branch(repo)
    _, cur, _ = _git(repo, "branch", "--show-current")
    if cur.strip() != db:
        return "parked"          # on a work branch — theirs, untouched
    rc, _, err = _git(repo, "merge", "--ff-only", "-q", f"origin/{db}")
    if rc != 0:
        # Either the branch has its own commits (diverged: needs a PR or a
        # merge by a person) or local edits sit on files the remote moved.
        return "dirty" if dirty else "diverged"
    return "dirty" if dirty else "clean"


def sync_outcomes(root: Path, only: str | None = None,
                  include_code: bool = True) -> list[tuple[str, str, str]]:
    """Every registered repo under `root`, converged or fast-forwarded.

    The registry, not a glob, says what is synced: `[0-9]-*` found only the
    spaces, and the modules, DIPs and project repos that `./sync` used to
    pull were left to a script this replaces. Knowledge and agent-personal
    repos converge (autosave, merge, push); code repos are fast-forwarded and
    never committed. Returns (name, category, outcome) per repo.
    """
    out: list[tuple[str, str, str]] = []
    registry = dict(_registry(root))
    # New spaces declared only by their marker (SPC-9): see classify().
    for path in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                       and p.name not in registry and (p / ".git").exists()
                       and _marker_space(p, root)):
        registry[path.name] = {"category": "knowledge", "via": "space marker"}
    for key, entry in registry.items():
        path = root if key == "<root>" else root / key
        name = "<root>" if key == "<root>" else key
        if only and Path(key).name != only and key != only:
            continue
        if not (path / ".git").exists():
            continue
        cat = str(entry.get("category") or "")
        if cat == "code":
            if include_code:
                out.append((name, cat, _code_update(path)))
            continue
        out.append((name, cat, sync_repo(path, quiet=True, root=root)))
    return out


HUMAN_NEEDED = ("conflict", "blocked", "diverged")


def sync_all(root: Path, only: str | None = None, quiet: bool = False,
             include_code: bool = True) -> int:
    outcomes = sync_outcomes(root, only=only, include_code=include_code)
    bad = [(n, o) for n, _, o in outcomes if o in HUMAN_NEEDED]
    if not outcomes and not quiet:
        print(f"sync: no registered repository is checked out under {root} "
              f"({len(_registry(root))} registered) — the hourly phase-1 cycle "
              f"converges what is here by path")
    if not quiet:
        for name, cat, o in outcomes:
            print(f"{name}: {o}" + (f"  [{cat}]" if cat == "code" else ""))
        print(f"\nsync: {len(outcomes)} repo(s), {len(bad)} needing a human")
    return 1 if bad else 0


def status_lines(root: Path) -> list[str]:
    """One line per registered repo: branch, dirty count, ahead/behind. Touches nothing."""
    lines = []
    for key, entry in _registry(root).items():
        path = root if key == "<root>" else root / key
        if not (path / ".git").exists():
            continue
        _, cur, _ = _git(path, "branch", "--show-current")
        _, st, _ = _git(path, "status", "--porcelain")
        db = default_branch(path)
        rc, ab, _ = _git(path, "rev-list", "--left-right", "--count", f"origin/{db}...HEAD")
        behind, ahead = (ab.split() + ["?", "?"])[:2] if rc == 0 else ("?", "?")
        dirty = len([l for l in st.splitlines() if l.strip()])
        lines.append(f"{key if key != '<root>' else '<root>'}: {cur.strip() or '?'} "
                     f"dirty={dirty} ahead={ahead} behind={behind} [{entry.get('category', '')}]")
    return lines


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ledger transport")
    ap.add_argument("op", choices=["converge", "gaps", "classify", "sync", "status"])
    ap.add_argument("--space", type=Path, help="required for all ops except sync")
    ap.add_argument("--repo", help="sync: limit to one space by directory name")
    ap.add_argument("--root", type=Path, default=_data_root())
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--line", action="store_true",
                    help="converge: one line (space, ok/FAIL, reason) instead of JSON, for logs")
    ap.add_argument("--no-code", action="store_true",
                    help="sync: knowledge repos only, leave code repos alone")
    a = ap.parse_args()

    if a.op == "sync":
        raise SystemExit(sync_all(a.root, only=a.repo, quiet=a.quiet,
                                  include_code=not a.no_code))
    if a.op == "status":
        print("\n".join(status_lines(a.root)))
        raise SystemExit(0)
    if a.space is None:
        ap.error(f"--space is required for {a.op}")
    fn = {"converge": converge, "gaps": gaps, "classify": classify}[a.op]
    res = fn(a.space) if a.op == 'gaps' else fn(a.space, root=a.root)
    if a.line:
        print(converge_line(a.op, a.space, res))
        raise SystemExit(0 if res.ok else 1)
    print(json.dumps({"ok": res.ok, "reason": res.reason, "context": res.context},
                     indent=2, default=str))
    raise SystemExit(0 if res.ok else 1)
