#!/usr/bin/env python3
"""Output inventory for unattended publication.

This is not the operator approval gate proposed in Draft DIP-0046 E3. An
inventory records permitted paths, not approval or proof of publication.

`git_commit_push` ran `git add -A`. An overnight task that edits one report
therefore commits whatever else happens to be in the tree — a half-finished
edit left open on the Mac, another agent's scratch file, a credential someone
dropped in to test with. The task's own diff is correct; everything travelling
with it is unreviewed, and it is committed under the task's message, which is
how it stops looking like anything worth checking.

The rule is narrow on purpose: **an unattended run commits what it produced,
and nothing else.** Anything else in the tree is not discarded, not committed,
and not silently ignored — it becomes a PENDING DECISION, a file naming exactly
what was found and which task found it, for a human to resolve.

Why a file rather than a prompt: nightshift runs ~20 tasks with nobody awake.
A gate that blocks on an answer nobody is there to give converts one unreviewed
commit into a stalled queue, which is worse. So the run continues, having
committed only its own output, and the backlog is counted and alerted
(`detectors/pending_decisions.py`) so the operator learns about it in the
morning rather than discovering it in a diff three weeks later.

Deliberately NOT here: any attempt to judge whether the extra changes are
*good*. This gate answers "did this run produce it?", which is a question with
an answer. "Is this change safe?" is not, and a gate that pretends otherwise
would be trusted for a guarantee it cannot make (DIP-0046 E1: the defence is
check strength, not the appearance of one).
"""
from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from git_inventory import dirty_paths

PENDING = Path.home() / ".datacore" / "state" / "commit-decisions"


@dataclass
class Decision:
    """What the caller is permitted to commit, and what it must not."""
    allowed: list[str] = field(default_factory=list)
    withheld: list[str] = field(default_factory=list)
    record: Path | None = None

    @property
    def clean(self) -> bool:
        return not self.withheld


def decide(repo: Path, produced: list[str] | None, *,
           task_id: str = "unknown", actor: str = "unknown",
           at: str = "") -> Decision:
    """Split the dirty tree into what this run made and what it merely found.

    `produced=None` cannot authorize any dirty path. Record the withheld work
    for reconciliation. Publication callers must name their actual outputs;
    missing producer plumbing is not authority to publish other writers' data.
    """
    dirty = dirty_paths(repo)
    if not dirty:
        return Decision()

    if produced is None:
        dec = Decision(allowed=[], withheld=list(dirty))
        dec.record = _record(repo, dec, task_id=task_id, actor=actor, at=at)
        return dec

    wanted = set(produced or [])
    allowed = [p for p in dirty if p in wanted]
    withheld = [p for p in dirty if p not in wanted]
    dec = Decision(allowed=allowed, withheld=withheld)
    if withheld:
        dec.record = _record(repo, dec, task_id=task_id, actor=actor, at=at)
    return dec


def _record(repo: Path, dec: Decision, *, task_id: str, actor: str, at: str) -> Path:
    """Persist the decision. An audit artifact, not a log line: it has to be
    countable by a detector and resolvable by a human, and a line in a rotating
    log is neither."""
    PENDING.mkdir(parents=True, mode=0o700, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in task_id)[:60]
    stamp = at or _now()
    # Caller timestamps are metadata, never path components. Unique records
    # preserve retries and simultaneous decisions for the same task.
    safe_stamp = "".join(c if c.isalnum() or c in "-_" else "-" for c in stamp)[:32]
    name = f"{safe_stamp}-{safe}-{uuid.uuid4().hex}.json"
    temporary = f".{name}.pending"
    content = json.dumps({
        "repo": str(repo), "task_id": task_id, "actor": actor, "at": stamp,
        "schema_version": 2, "kind": "output-inventory",
        "allowed": dec.allowed, "withheld": dec.withheld,
        "publication_verified": False,
        "note": "Unattended run found changes it did not produce. Nothing was "
                "discarded. This inventory records no publication or operator "
                "approval; review retained changes separately.",
    }, indent=2).encode('utf-8')
    directory = os.open(PENDING, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(directory)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError('Decision storage must be an owner-controlled directory')
        # Older installations created this private audit directory with umask
        # defaults. Tightening the directory also protects existing records.
        os.fchmod(directory, 0o700)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        # Linking publishes a complete file atomically and refuses any existing
        # destination. On failure retain the temporary record for recovery.
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        os.fsync(directory)
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(directory)
    return PENDING / name


def _now() -> str:
    # Timestamps come from the caller where possible so a run's artifacts share
    # one stamp; this is the fallback.
    from datetime import datetime
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def pending() -> list[dict]:
    """Unresolved decisions. Resolution is deleting the file, which is the
    cheapest possible affordance and needs no tooling to exist first."""
    if not PENDING.is_dir():
        return []
    out = []
    for f in sorted(PENDING.glob("*.json")):
        try:
            out.append({**json.loads(f.read_text()), "_file": str(f)})
        except (OSError, ValueError):
            out.append({"_file": str(f), "at": "", "task_id": "<unreadable>",
                        "withheld": []})
    return out


def enabled() -> bool:
    """On unless explicitly disabled. A safety gate defaulting to off is a
    safety gate that is off."""
    return os.environ.get("DATACORE_COMMIT_GATE", "1") != "0"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="commit-decision gate")
    ap.add_argument("op", choices=["check", "pending"])
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--produced", nargs="*", default=None)
    a = ap.parse_args()

    if a.op == "pending":
        rows = pending()
        for r in rows:
            print(f"  {r.get('at','?'):<16} {r.get('task_id','?'):<28} "
                  f"{len(r.get('withheld') or [])} withheld")
        print(f"\ncommit-decisions: {len(rows)} pending")
        raise SystemExit(1 if rows else 0)

    d = decide(a.repo, a.produced, task_id="cli")
    print(json.dumps({"allowed": d.allowed, "withheld": d.withheld,
                      "record": str(d.record) if d.record else None}, indent=2))
    raise SystemExit(0 if d.clean else 1)
