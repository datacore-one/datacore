"""T6 (ledger-upgrade Phase 1, audit E-F1, A#10, D9): an event that is not
attested by a declared principal has no effect on state.

Owner decision 1 (no signing yet): "attested" is authorship without keys --
the event's `actor` is a declared writer (some principal's `writes_as` in the
principal registry) AND the event sits in that writer's own log file (the
file stem, with a `-run-<date>` or `.telemetry` suffix removed, equals
`actor`). The signature conjunct returns with FDS-ID.

Two halves, one claim:

* Formal: `LedgerSpec/Author.lean` proves the eval-owned statements in
  `T6Targets.lean` beside this file, with no `sorry` and no axiom beyond the
  three the project allows. A machine without Lean fails ("could not run").
* Replay against the real code: `ledger.fold.fold` over `read_events` of a
  disposable space behaves as the model says -- an impostor line (a declared
  name in another writer's log) and an undeclared writer's events change
  nothing; a declared writer in its own log, including its run-branch log,
  takes effect.

Seeded failures: the model's theorems left as `sorry` (2026-10-04 stub);
`attested` weakened to "actor is declared" (the impostor theorem no longer
holds); `fold()` applying every event by its self-declared actor (the code
before T6: the 2026-09-25 forgery folded like a genuine event).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ledger.events import Event, body_dict, compute_hash, to_line
from ledger.fold import fold
from ledger.log import EventLog, read_events

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2] / "specs" / "datacore-lean"
TARGETS = HERE / "T6Targets.lean"
ALLOWED = {"propext", "Classical.choice", "Quot.sound"}

REGISTRY = """\
principals:
  owner:
    kind: human
    writes_as: [mac]
  worker:
    kind: agent
    writes_as: [miles, nightshift]
"""


# ── formal half ──────────────────────────────────────────────────────────────

def _lake() -> str:
    found = shutil.which("lake") or str(Path.home() / ".elan" / "bin" / "lake")
    assert Path(found).exists(), "could not run: Lean's `lake` is not installed (elan)"
    return found


def test_the_model_proves_t6_without_gaps():
    lake = _lake()
    env = {**os.environ, "PATH": f"{Path(lake).parent}:{os.environ.get('PATH', '')}"}
    built = subprocess.run([lake, "build", "LedgerSpec.Author"], cwd=PROJECT, env=env,
                           capture_output=True, text=True, timeout=1800)
    assert built.returncode == 0, f"SETUP: the model does not build:\n{built.stdout[-2000:]}{built.stderr[-1000:]}"
    run = subprocess.run([lake, "env", "lean", str(TARGETS)], cwd=PROJECT, env=env,
                         capture_output=True, text=True, timeout=1800)
    out = run.stdout + run.stderr
    errors = [l for l in out.splitlines() if re.search(r":\d+:\d+: error", l)]
    assert run.returncode == 0 and not errors, \
        "T6's statements do not typecheck against the model:\n  " + "\n  ".join(errors or [out[-1500:]])
    bad = []
    for m in re.finditer(r"'(t6_\w+)' depends on axioms: \[([^\]]*)\]", out):
        used = {a.strip() for a in m.group(2).split(",") if a.strip()}
        if used - ALLOWED:
            bad.append(f"{m.group(1)} uses {sorted(used - ALLOWED)}")
    assert not bad, "T6 is not proved (a gap or a disallowed axiom): " + "; ".join(bad)
    printed = set(re.findall(r"'(t6_\w+)' (?:depends on axioms|does not depend on any axioms)", out))
    assert printed >= {"t6_fold_ignores_unattested", "t6_anywhere", "t6_filter", "t6_impostor",
                       "t6_undeclared", "t6_non_vacuous"}, f"axiom report incomplete: {sorted(printed)}"


# ── replay against the real fold ─────────────────────────────────────────────

@pytest.fixture
def space(tmp_path, monkeypatch):
    import actor_identity
    reg = tmp_path / "principals.yaml"
    reg.write_text(REGISTRY)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", reg)
    s = tmp_path / "9-fixture"
    (s / ".datacore" / "events").mkdir(parents=True)
    return s


def _line(space: Path, log: str, actor: str, type_: str, payload: dict) -> None:
    """Append one canonical, correctly chained event to `<log>.jsonl` as `actor`
    -- exactly what EventLog would write, except the writer is chosen freely."""
    path = space / ".datacore" / "events" / f"{log}.jsonl"
    lines = path.read_text().splitlines() if path.exists() else []
    last = json.loads(lines[-1]) if lines else None
    seq = last["seq"] + 1 if last else 0
    prev = last["hash"] if last else "GENESIS"
    ms = 1_790_000_000_000 + 1000 * sum(1 for _ in (space / ".datacore" / "events").glob("*.jsonl")) + seq
    body = body_dict(seq, f"{ms}.0000.{actor}", actor, type_, payload, prev)
    with path.open("a") as f:
        f.write(to_line(Event(**body, hash=compute_hash(body), sig="")) + "\n")


def _create(space, log, actor, iid):
    _line(space, log, actor, "item.create", {"id": iid, "title": iid, "state": "NEXT"})


def test_a_declared_writer_in_its_own_log_takes_effect(space):
    _create(space, "mac", "mac", "t-own")
    _create(space, "miles-run-2026-10-04", "miles", "t-run")
    items = fold(read_events(space)).items
    assert "t-own" in items and "t-run" in items, (
        "non-vacuity: a declared writer's events in its own log (and its run-branch log) must take "
        f"effect; folded items: {sorted(items)}")


def test_an_impostor_line_has_no_effect(space):
    _create(space, "mac", "mac", "t-real")
    _create(space, "miles", "mac", "t-impostor")       # the owner's name, in the agent's log
    items = fold(read_events(space)).items
    assert "t-impostor" not in items, (
        "expected: an event whose actor is not the writer of the log it sits in has no effect.\n"
        f"seen: item t-impostor exists after fold (created by 'mac' inside miles.jsonl)")


def test_an_undeclared_writer_has_no_effect(space):
    _create(space, "mac", "mac", "t1")
    _line(space, "mac", "mac", "item.complete", {"id": "t1"})
    _create(space, "stranger", "stranger", "t-stranger")
    _line(space, "stranger", "stranger", "item.verify", {"id": "t1", "reason": "looks fine"})
    _line(space, "stranger", "stranger", "item.dismiss", {"id": "t1", "reason": "obsolete"})
    state = fold(read_events(space))
    assert "t-stranger" not in state.items, "an undeclared writer created an item"
    assert state.items["t1"].status != "verified" and state.items["t1"].status != "dismissed", (
        "expected: an undeclared writer's verify/dismiss has no effect on a declared writer's item.\n"
        f"seen: t1 is {state.items['t1'].status!r}")


def test_ignoring_an_unattested_event_is_the_same_as_it_never_existing(space):
    _create(space, "mac", "mac", "a")
    _create(space, "miles", "miles", "b")
    clean = fold(read_events(space)).state_root()
    _create(space, "nightshift", "mac", "c")           # impostor
    _create(space, "ghost", "ghost", "d")              # undeclared
    assert fold(read_events(space)).state_root() == clean, (
        "expected: the state after adding only unattested events equals the state without them "
        "(fold_ignores_unattested)")
