#!/usr/bin/env python3
"""Round two: attack the layers ABOVE the ledger — transport, projection,
ingest, and the guards themselves. Throwaway repos only.
"""
import json, os, subprocess, sys, tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
from ledger.log import EventLog, read_events, StaleLogError   # noqa: E402
from ledger.fold import fold                                   # noqa: E402

RESULTS = []


def result(a, held, d):
    RESULTS.append((a, held, d))
    print(f"  [{'HELD' if held else 'BROKEN':6}] {a:<48} {d}")


_GIT_ENV = {k: v for k, v in os.environ.items()
            if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}


def git(d, *a, **kw):
    kw.setdefault("env", _GIT_ENV)
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True,
                          text=True, **kw)


def repo(name="9-t"):
    d = Path(tempfile.mkdtemp()) / name
    (d / ".datacore" / "events").mkdir(parents=True)
    (d / "org").mkdir(parents=True, exist_ok=True)
    git(d.parent, "init", "-q", str(d))
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    return d


# ── B1: converge must never lose a local commit ────────────────────────────
def b1_converge_preserves():
    """INVARIANT: converge never discards local work, even on divergence."""
    up = repo("9-up")
    (up / "seed.txt").write_text("seed\n")
    git(up, "add", "-A"); git(up, "commit", "-qm", "seed")
    git(up, "config", "receive.denyCurrentBranch", "ignore")

    clone = Path(tempfile.mkdtemp()) / "9-clone"
    subprocess.run(["git", "clone", "-q", str(up), str(clone)], capture_output=True)
    git(clone, "config", "user.email", "t@t"); git(clone, "config", "user.name", "t")
    (clone / ".datacore" / "events").mkdir(parents=True, exist_ok=True)

    # both sides move
    (up / "theirs.txt").write_text("t\n"); git(up, "add", "-A"); git(up, "commit", "-qm", "theirs")
    (clone / "mine.txt").write_text("m\n"); git(clone, "add", "-A"); git(clone, "commit", "-qm", "mine")
    mine = git(clone, "rev-parse", "HEAD").stdout.strip()

    subprocess.run([sys.executable, str(LIB / "ledger_transport.py"), "converge",
                    "--space", str(clone)], capture_output=True, timeout=120)
    reachable = git(clone, "merge-base", "--is-ancestor", mine, "HEAD").returncode == 0
    result("B1 converge preserves local commits", reachable,
           "local commit still reachable" if reachable else "LOCAL COMMIT LOST")


# ── B2: converge on a dirty tree must not stash-and-lose ───────────────────
def b2_dirty_tree():
    """INVARIANT: uncommitted work survives a converge."""
    d = repo("9-dirty")
    (d / "a.txt").write_text("committed\n")
    git(d, "add", "-A"); git(d, "commit", "-qm", "a")
    (d / "a.txt").write_text("UNCOMMITTED EDIT\n")
    subprocess.run([sys.executable, str(LIB / "ledger_transport.py"), "converge",
                    "--space", str(d)], capture_output=True, timeout=120)
    survived = "UNCOMMITTED" in (d / "a.txt").read_text()
    stash = git(d, "stash", "list").stdout.strip()
    result("B2 dirty tree survives converge", survived and not stash,
           "edit intact, no stash left" if survived else "EDIT LOST")


# ── B3: ownership guard vs an actual foreign-log write ─────────────────────
def b3_ownership_guard():
    """INVARIANT: writing another actor's log is refused at push."""
    d = repo("9-own")
    EventLog(d, "mac").append("item.create", {"id": "x", "title": "x"})
    git(d, "add", "-A"); git(d, "commit", "-qm", "base")
    git(d, "branch", "-M", "main")
    base = git(d, "rev-parse", "HEAD").stdout.strip()
    # mac writes winston's log
    EventLog(d, "winston").append("item.create", {"id": "y", "title": "y"})
    git(d, "add", "-A"); git(d, "commit", "-qm", "forge")
    env = {**os.environ, "DATACORE_ACTOR": "mac"}
    p = subprocess.run([sys.executable, str(LIB / "hooks" / "log_ownership_guard.py"),
                        f"{base}..HEAD"], cwd=d, capture_output=True, text=True, env=env)
    result("B3 foreign-log write is refused", p.returncode != 0,
           f"guard rc={p.returncode}")


# ── B4: projection must be deterministic across runs ───────────────────────
def b4_projection_stable():
    """INVARIANT: same ledger -> byte-identical projection. Phase 1 replaces a
    real file with this; a projection that differs run to run would rewrite the
    user's org file on every render."""
    from ledger.projector import project
    d = repo("9-proj")
    a = EventLog(d, "mac")
    for i in range(6):
        a.append("item.create", {"id": f"p{i}", "title": f"T{i}", "state": "TODO",
                                 "tags": ["x"], "level": 1})
    st = fold(read_events(d))
    t1 = project(st, space=d.name).text
    t2 = project(fold(read_events(d)), space=d.name).text
    result("B4 projection is byte-stable", t1 == t2,
           "identical" if t1 == t2 else f"differ ({len(t1)} vs {len(t2)} bytes)")


# ── B5: a torn final line must not lose earlier events ─────────────────────
def b5_torn_line():
    """INVARIANT: a crash mid-append costs at most the in-flight event."""
    d = repo("9-torn")
    log = EventLog(d, "mac")
    for i in range(4):
        log.append("item.create", {"id": f"t{i}", "title": "x"})
    p = d / ".datacore" / "events" / "mac.jsonl"
    p.write_text(p.read_text() + '{"seq":4,"actor":"mac","typ')   # torn
    before = 4
    evs = read_events(d)
    ok = len(evs) == before
    EventLog(d, "mac").append("item.create", {"id": "after", "title": "x"})
    after = read_events(d)
    result("B5 torn line costs only itself", ok and len(after) == before + 1,
           f"{len(evs)} readable, {len(after)} after recovery")


# ── B6: HWM guard must not block a LEGITIMATE fresh clone ──────────────────
def b6_hwm_false_positive():
    """INVARIANT: the new stale-log guard must not break normal operation.

    A guard that fires on healthy systems is worse than none — this is the
    regression that would make people delete it.
    """
    d = repo("9-fresh")
    log = EventLog(d, "mac")
    for i in range(3):
        log.append("item.create", {"id": f"n{i}", "title": "x"})
    # A fresh clone elsewhere: same events, no local state dir.
    import shutil
    other = Path(tempfile.mkdtemp()) / "9-fresh2"
    shutil.copytree(d, other)
    shutil.rmtree(other / ".datacore" / "state", ignore_errors=True)
    try:
        EventLog(other, "mac").append("item.create", {"id": "ok", "title": "x"})
        result("B6 guard allows a legitimate fresh clone", True, "append succeeded")
    except StaleLogError as exc:
        result("B6 guard allows a legitimate fresh clone", False, f"FALSE POSITIVE: {exc}")


# ── B7: guard must allow catching up after a converge ──────────────────────
def b7_hwm_after_converge():
    """INVARIANT: after receiving newer events, appending must work again."""
    d = repo("9-catchup")
    log = EventLog(d, "mac")
    for i in range(3):
        log.append("item.create", {"id": f"c{i}", "title": "x"})
    p = d / ".datacore" / "events" / "mac.jsonl"
    lines = p.read_text().splitlines()
    p.write_text("\n".join(lines[:-1]) + "\n")          # rewound
    try:
        EventLog(d, "mac").append("item.create", {"id": "no", "title": "x"})
        blocked = False
    except StaleLogError:
        blocked = True
    p.write_text("\n".join(lines) + "\n")               # converge restores it
    try:
        EventLog(d, "mac").append("item.create", {"id": "yes", "title": "x"})
        recovered = True
    except StaleLogError:
        recovered = False
    result("B7 guard clears after converge", blocked and recovered,
           f"blocked={blocked}, recovered_after_converge={recovered}")


if __name__ == "__main__":
    print("\n  ADVERSARIAL PROBE II — transport, projection, guards\n")
    for fn in (b1_converge_preserves, b2_dirty_tree, b3_ownership_guard,
               b4_projection_stable, b5_torn_line, b6_hwm_false_positive,
               b7_hwm_after_converge):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            result(fn.__name__, False, f"crashed: {type(exc).__name__}: {exc}")
    broken = [r for r in RESULTS if not r[1]]
    print(f"\n  {len(RESULTS)} attack(s): {len(RESULTS)-len(broken)} held, {len(broken)} BROKEN")
    for a, _, d in broken:
        print(f"    - {a}: {d}")
    raise SystemExit(1 if broken else 0)


# ── pytest entry points ─────────────────────────────────────────────────────
def _run(fn):
    RESULTS.clear()
    fn()
    assert RESULTS, f"{fn.__name__} recorded no result"
    attack, held, detail = RESULTS[-1]
    assert held, f"{attack}: {detail}"


def test_b1_converge_preserves_local():   _run(b1_converge_preserves)
def test_b2_dirty_tree_survives():        _run(b2_dirty_tree)
def test_b3_foreign_log_refused():        _run(b3_ownership_guard)
def test_b4_projection_byte_stable():     _run(b4_projection_stable)
def test_b5_torn_line_recovery():         _run(b5_torn_line)
def test_b6_guard_no_false_positive():    _run(b6_hwm_false_positive)
def test_b7_guard_clears_after_converge(): _run(b7_hwm_after_converge)
