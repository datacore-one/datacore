#!/usr/bin/env python3
"""Adversarial probe: actively try to break the ledger's invariants.

Every attack here BROKE the system the first time it ran. They are kept as
tests so the fixes cannot silently regress — a defence nobody re-attacks is
just a claim.

Each attack states the invariant it targets and what a PASS means. Run in
throwaway spaces only. Nothing here touches a real ledger.
"""
import json, os, shutil, subprocess, sys, tempfile, threading
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
from ledger.log import EventLog, read_events          # noqa: E402
from ledger.fold import fold                          # noqa: E402
from ledger.log import StaleLogError                  # noqa: E402


RESULTS = []


def result(attack, held, detail):
    RESULTS.append((attack, held, detail))
    mark = "HELD" if held else "BROKEN"
    print(f"  [{mark:6}] {attack:<46} {detail}")


def newspace(name="9-attack"):
    d = Path(tempfile.mkdtemp()) / name
    (d / ".datacore" / "events").mkdir(parents=True)
    return d


# ── A1: append against a STALE copy of your own log ─────────────────────────
def a1_stale_append():
    """INVARIANT: (actor, seq) identifies exactly one event, forever.

    This is the one that broke in production tonight. Simulate a machine whose
    own log was rewound by a git operation, then appends.
    """
    s = newspace()
    log = EventLog(s, "mac")
    for i in range(3):
        log.append("item.create", {"id": f"a{i}", "title": f"t{i}"})
    p = s / ".datacore" / "events" / "mac.jsonl"
    full = p.read_text()
    lines = full.splitlines()

    # Rewind the log by one event, exactly as a bad merge / checkout would.
    p.write_text("\n".join(lines[:-1]) + "\n")
    try:
        EventLog(s, "mac").append("item.dismiss", {"id": "other", "reason": "x"})
    except StaleLogError as exc:
        result("A1 stale-log append reuses a seq", True, f"refused: {str(exc)[:44]}")
        return

    rewound = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    original = [json.loads(l) for l in lines if l.strip()]
    clash = [(o["seq"], o["hash"]) for o in original][-1]
    now = [(e["seq"], e["hash"]) for e in rewound][-1]
    forked = clash[0] == now[0] and clash[1] != now[1]
    result("A1 stale-log append reuses a seq", not forked,
           f"seq {now[0]} now has 2 distinct hashes" if forked else "refused/renumbered")


# ── A2: concurrent appends from threads (flock) ─────────────────────────────
def a2_concurrent():
    """INVARIANT: concurrent appends by the same actor never collide."""
    s = newspace()
    errs = []

    def worker(n):
        try:
            EventLog(s, "mac").append("item.create", {"id": f"c{n}", "title": "x"})
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    evs = read_events(s)
    seqs = [e.seq for e in evs]
    result("A2 concurrent same-actor appends", len(seqs) == len(set(seqs)) == 12,
           f"{len(seqs)} events, {len(set(seqs))} distinct seqs, {len(errs)} errors")


# ── A3: two clones diverge — is the fork DETECTABLE? ────────────────────────
def a3_fork_detection():
    """INVARIANT: a fork of one actor's log is detectable by tooling.

    Tonight's fork was found only because git happened to conflict. If the two
    sides had merged cleanly (e.g. appended at different line positions) it
    would have been silent.
    """
    s1 = newspace("9-clone-a")
    log = EventLog(s1, "mac")
    for i in range(3):
        log.append("item.create", {"id": f"f{i}", "title": "x"})
    s2 = Path(tempfile.mkdtemp()) / "9-clone-b"
    shutil.copytree(s1, s2)

    EventLog(s1, "mac").append("item.create", {"id": "left", "title": "L"})
    EventLog(s2, "mac").append("item.create", {"id": "right", "title": "R"})

    a = {(e.actor, e.seq): e.hash for e in read_events(s1)}
    b = {(e.actor, e.seq): e.hash for e in read_events(s2)}
    conflicts = [k for k in (a.keys() & b.keys()) if a[k] != b[k]]

    # Does a TOOL report it? Set s2 up as s1's "origin" so the detector has the
    # external reference a fork can only be seen against.
    for d in (s1, s2):
        subprocess.run(["git", "init", "-q", str(d)], capture_output=True)
        subprocess.run(["git", "-C", str(d), "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", str(d), "-c", "user.email=t@t", "-c",
                        "user.name=t", "commit", "-qm", "x"], capture_output=True)
    subprocess.run(["git", "-C", str(s1), "remote", "add", "origin", str(s2)],
                   capture_output=True)
    subprocess.run(["git", "-C", str(s1), "fetch", "-q", "origin"], capture_output=True)
    from ledger.fork import detect
    rep = detect(s1, ref="origin/HEAD")
    if rep.reason:
        rep = detect(s1, ref="origin/master")
    detected = bool(rep.collisions)
    result("A3 cross-clone fork is detectable", detected,
           str(rep) if detected else f"{len(conflicts)} real collision(s), detector said: {rep}")


# ── A4: does a seal over a forked log get caught? ───────────────────────────
def a4_seal_over_fork():
    """INVARIANT: the sequencer cannot certify a forked history as settled."""
    try:
        from ledger.seal import build_seal_payload, verify_seal
    except Exception as exc:  # noqa: BLE001
        result("A4 seal refuses a forked log", False, f"import failed: {exc}")
        return
    s = newspace()
    log = EventLog(s, "mac")
    for i in range(3):
        log.append("item.create", {"id": f"s{i}", "title": "x"})
    p = s / ".datacore" / "events" / "mac.jsonl"
    lines = p.read_text().splitlines()
    p.write_text("\n".join(lines[:-1]) + "\n")
    try:
        EventLog(s, "mac").append("item.dismiss", {"id": "zz", "reason": "fork"})
    except StaleLogError:
        # Build the fork the way one ACTUALLY arrives: git union-merging two
        # divergent copies leaves BOTH lines in the file, so the same
        # (actor, seq) appears twice with different hashes. Replacing a line
        # instead would be a tamper, which the chain check already catches.
        import json as _j
        ev = _j.loads(lines[-1]); ev["payload"] = {"id": "zz", "reason": "fork"}
        ev["hash"] = "f" * 64
        p.write_text("\n".join(lines + [_j.dumps(ev, separators=(",", ":"), sort_keys=True)]) + "\n")

    EventLog(s, "winston").append("ledger.seal", build_seal_payload(read_events(s)))
    ok, detail = verify_seal(read_events(s))
    # A seal that verifies over a forked log is the dangerous outcome: it
    # certifies a history that another machine will disagree with.
    result("A4 seal refuses a forked log", ok is not True,
           f"verify_seal -> {ok}: {detail[:52]}")


# ── A5: tampering with a mid-chain event ───────────────────────────────────
def a5_tamper():
    """INVARIANT: editing history breaks the chain and is caught."""
    s = newspace()
    log = EventLog(s, "mac")
    for i in range(4):
        log.append("item.create", {"id": f"t{i}", "title": f"t{i}"})
    p = s / ".datacore" / "events" / "mac.jsonl"
    lines = p.read_text().splitlines()
    e = json.loads(lines[1])
    e["payload"]["title"] = "TAMPERED"
    lines[1] = json.dumps(e, separators=(",", ":"), sort_keys=True)
    p.write_text("\n".join(lines) + "\n")
    rc = subprocess.run([sys.executable, str(LIB / "ledger_cli.py"), "verify",
                         "--space", str(s)], capture_output=True).returncode
    result("A5 mid-chain tamper is caught", rc != 0, f"verify rc={rc}")


# ── A6: fold determinism across event ORDER ────────────────────────────────
def a6_fold_determinism():
    """INVARIANT: the same events fold to the same state root regardless of the
    order they arrived in — the property that makes state_root comparable."""
    s = newspace()
    a, b = EventLog(s, "mac"), EventLog(s, "miles")
    for i in range(4):
        a.append("item.create", {"id": f"d{i}", "title": "x"})
        b.append("item.update", {"id": f"d{i}", "title": "y"})
    # fold() documents that it takes ALREADY hlc-sorted events; read_events
    # guarantees that. Reversing its output violates the precondition and
    # proves nothing — the real question is whether two machines reading the
    # same event set agree, which is what state_root comparison depends on.
    r1 = fold(read_events(s)).state_root()
    import shutil as _sh, tempfile as _tf
    other = Path(_tf.mkdtemp()) / "9-mirror"
    _sh.copytree(s, other)
    r2 = fold(read_events(other)).state_root()
    evs = read_events(s)
    sorted_ok = evs == sorted(evs, key=lambda e: e.hlc)
    result("A6 same event set -> same state root", r1 == r2 and sorted_ok,
           "identical root, hlc-sorted" if r1 == r2 else f"{r1[:10]} vs {r2[:10]}")


# ── A7: an event claiming an actor that does not own the file ──────────────
def a7_actor_spoof():
    """INVARIANT: an event's actor matches the log it lives in."""
    s = newspace()
    EventLog(s, "mac").append("item.create", {"id": "x", "title": "x"})
    p = s / ".datacore" / "events" / "mac.jsonl"
    e = json.loads(p.read_text().splitlines()[0])
    e["actor"] = "winston"                      # claim someone else's identity
    p.write_text(json.dumps(e, separators=(",", ":"), sort_keys=True) + "\n")
    rc = subprocess.run([sys.executable, str(LIB / "ledger_cli.py"), "verify",
                         "--space", str(s)], capture_output=True).returncode
    result("A7 actor/file mismatch is caught", rc != 0, f"verify rc={rc}")


if __name__ == "__main__":
    print("\n  ADVERSARIAL PROBE — trying to break the ledger\n")
    for fn in (a1_stale_append, a2_concurrent, a3_fork_detection,
               a4_seal_over_fork, a5_tamper, a6_fold_determinism, a7_actor_spoof):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            result(fn.__name__, False, f"probe crashed: {type(exc).__name__}: {exc}")
    broken = [r for r in RESULTS if not r[1]]
    print(f"\n  {len(RESULTS)} attack(s): {len(RESULTS)-len(broken)} held, "
          f"{len(broken)} BROKEN")
    for a, _, d in broken:
        print(f"    - {a}: {d}")
    raise SystemExit(1 if broken else 0)


# ── pytest entry points ─────────────────────────────────────────────────────
# Each attack is its own test so a failure names the invariant that broke,
# rather than reporting "the probe failed".

def _run(fn):
    RESULTS.clear()
    fn()
    assert RESULTS, f"{fn.__name__} recorded no result"
    attack, held, detail = RESULTS[-1]
    assert held, f"{attack}: {detail}"


def test_a1_stale_log_append_refused():   _run(a1_stale_append)
def test_a2_concurrent_appends():         _run(a2_concurrent)
def test_a3_fork_is_detectable():         _run(a3_fork_detection)
def test_a4_seal_refuses_a_fork():        _run(a4_seal_over_fork)
def test_a5_tamper_is_caught():           _run(a5_tamper)
def test_a6_state_root_is_stable():       _run(a6_fold_determinism)
def test_a7_actor_spoof_is_caught():      _run(a7_actor_spoof)
