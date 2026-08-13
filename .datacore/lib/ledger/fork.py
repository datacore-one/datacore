"""Fork detection: has one actor's log split into two histories?

THE INVARIANT: `(actor, seq)` identifies exactly one event, forever. Everything
downstream depends on it — a merge is a union only because two machines can
never disagree about what `mac` seq 139 is, and `state_root` is comparable only
because both sides folded the same events.

It broke in production on 2026-08-13. In 5-plur, seq 139 was an `item.update`
on one machine and an `item.dismiss` on another. It was found only because git
happened to produce a TEXT conflict — the two sides differed on the same line.
Had they diverged at different line offsets, git would have merged them
cleanly, both chains would still have verified, and the fork would have been
silent and permanent.

That is the gap this closes. Note what does NOT catch it:

  chain verify   each side is internally consistent; both pass
  seq_gap        looks for gaps/duplicates WITHIN one copy; a fork has neither
  ownership      both events are legitimately authored by the same actor
  fold           happily folds a forked set into a plausible state

Detection needs an EXTERNAL reference, because a fork is a disagreement between
copies and no single copy can see it alone. The reference here is git: compare
the working tree against `origin/main`, which is the shared view every machine
converges on.

`log.py`'s high-water mark PREVENTS this machine from creating a fork. This
DETECTS one that arrived from elsewhere. Both are needed: prevention cannot
help with a fork another machine already pushed.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ForkReport:
    space: str
    collisions: list[tuple[str, int]] = field(default_factory=list)
    checked: int = 0
    reason: str = ""

    @property
    def clean(self) -> bool:
        return not self.collisions

    def __str__(self) -> str:
        if self.reason:
            return f"{self.space:14} n/a — {self.reason}"
        if self.clean:
            return f"{self.space:14} ok — {self.checked} event(s) agree with origin"
        first = self.collisions[0]
        return (f"{self.space:14} FORK — {len(self.collisions)} colliding "
                f"(actor, seq), e.g. {first[0]} seq {first[1]}")


def _git(space: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(space), *args],
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _index(text: str) -> dict[tuple[str, int], str]:
    """(actor, seq) -> hash for every parseable event in a log blob."""
    out: dict[tuple[str, int], str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "<=>":     # conflict markers
            continue
        try:
            e = json.loads(line)
            out[(e["actor"], int(e["seq"]))] = e["hash"]
        except Exception:  # noqa: BLE001 — a torn line is not a fork
            continue
    return out


def detect(space: Path, ref: str = "origin/main") -> ForkReport:
    """Compare this space's event logs against `ref`.

    A collision is `(actor, seq)` present on both sides with DIFFERENT hashes.
    Events present on only one side are normal — that is just one side being
    ahead — and are deliberately not reported, so ordinary divergence stays
    quiet and only genuine forks speak up.
    """
    rep = ForkReport(space=space.name)
    events_dir = space / ".datacore" / "events"
    if not events_dir.is_dir():
        rep.reason = "no event logs"
        return rep

    rc, _ = _git(space, "rev-parse", "--verify", ref)
    if rc != 0:
        # No remote ref to compare against — an offline clone or a fresh repo.
        # Genuinely unknown, not clean: say so rather than passing.
        rep.reason = f"{ref} not available locally (fetch first)"
        return rep

    for path in sorted(events_dir.glob("*.jsonl")):
        rel = path.relative_to(space)
        rc, blob = _git(space, "show", f"{ref}:{rel}")
        if rc != 0:
            continue                    # file exists only locally: not a fork
        theirs = _index(blob)
        try:
            ours = _index(path.read_text(errors="replace"))
        except OSError:
            continue
        shared = ours.keys() & theirs.keys()
        rep.checked += len(shared)
        rep.collisions.extend(sorted(k for k in shared if ours[k] != theirs[k]))
    return rep


def detect_all(root: Path, ref: str = "origin/main") -> list[ForkReport]:
    return [detect(s, ref) for s in sorted(root.glob("[0-9]-*"))
            if (s / ".datacore" / "events").is_dir()]
