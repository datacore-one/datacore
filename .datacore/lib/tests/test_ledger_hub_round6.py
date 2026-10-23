"""Round 6 of the ledger hub: the round-5 independent audit's reproductions
(2026-10-23 round-5 verdict, C1-C6 and H1, H2, H4) turned into regression
tests, under the owner's round-6 decisions:

  * who may reverse is read ONLY from the ledger's own owner records (C5);
  * the correction window runs on the hub's clock: each event's admission
    time at the hub decides, never a writer's timestamp (H1);
  * the window stays one named placeholder constant (one hour);
  * spaces not on the hub keep the old reversal rule.

Each test was written from the auditor's probe (test_r5_probes.py) with the
assertion turned around, and seen failing on the round-5 head (f90a125).
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _confirmed, _replica, _setup, _void, env  # noqa: F401
from ledger import hub as H
from ledger.events import from_line, to_line
from ledger.log import read_events


def _rejected_hashes(h):
    return {x for (_, _, x) in h._rejected()}


# ── C4: a forward correction is admitted by ONE rule, whatever path it took ──

@pytest.mark.parametrize("variant", ["foreign", "unsealed", "missing"])
def test_r6_an_invalid_compensation_imported_by_git_is_never_confirmed(env, monkeypatch, variant):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    if variant != "unsealed":
        h.seal()
    wb = env.writer("wb", H.LocalTransport(h))
    writer = wb if variant == "foreign" else owner
    p = _void(done)
    if variant == "missing":
        p["hash"] = "0" * 64
    c = T._signed_into(env, writer, "ledger.compensate", p)
    with pytest.raises(H.HubRefused) as exc:
        h.append(writer._submission(c))
    assert exc.value.code == "bad_compensate"
    T.git_pull(writer.space_dir, env.hub_dir)                 # it arrives anyway, by git
    published = T._published(env, h, [own, wa, owner, wb])
    report = writer.receipt_check(published, required=[c.hash])
    assert report["status"] != "confirmed" and not report["ok"], report
    assert c.hash in _rejected_hashes(h)                       # the grantor recorded its refusal
    assert H.incidents(published, registry_path=env.registry) == []


def test_r6_a_valid_compensation_imported_by_git_is_confirmed_with_its_incident(env, monkeypatch):
    """The control: the same rule admits a lawful correction that came by git."""
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    h.seal()
    c = T._signed_into(env, owner, "ledger.compensate", _void(done))
    T.git_pull(owner.space_dir, env.hub_dir)
    published = T._published(env, h, [own, wa, owner])
    assert owner.receipt_check(published, required=[c.hash])["status"] == "confirmed"
    assert wa.receipt_check(published, required=[done.hash])["status"] == "confirmed"   # the original stands
    rows = H.incidents(published, registry_path=env.registry)
    assert [(r["original"], r["correction"]) for r in rows] == [(done.hash, c.hash)]


# ── C5: who may reverse comes from the ledger's owner records only ───────────

def test_r6_a_registry_change_never_changes_a_sealed_reversal(env, monkeypatch):
    import actor_identity
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    v, _ = owner.append("ledger.void", _void(done))
    published = T._published(env, h, [own, wa, owner])
    before = owner.receipt_check(published, required=[v.hash])
    assert before["status"] == "confirmed", before
    assert h._state()[1].items["t"].status == "claimed"
    registry = Path(actor_identity.PRINCIPALS)
    registry.write_text(registry.read_text().replace("owner: {kind: human}", "owner: {kind: agent}"))
    assert h._state()[1].items["t"].status == "claimed"
    after = owner.receipt_check(published, required=[v.hash])
    assert after["status"] == "confirmed", after
    registry.write_text("principals: {}\n")                    # even an empty registry changes nothing
    assert H.authoritative_state(published, registry_path=env.registry).items["t"].status == "claimed"


def test_r6_a_human_principal_who_is_not_the_space_owner_cannot_reverse(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)      # `own` is a human principal
    v = T._signed_into(env, own, "ledger.void", _void(done))
    with pytest.raises(H.HubRefused) as exc:
        h.append(own._submission(v))
    assert exc.value.code == "not_authorized", exc.value
    T.git_pull(own.space_dir, env.hub_dir)
    published = T._published(env, h, [own, wa, owner])
    assert h._state()[1].items["t"].status == "completed"
    assert wa.receipt_check(published, required=[done.hash])["status"] == "confirmed"


# ── H1: the window runs on the hub's clock, by admission times ───────────────

def test_r6_a_backdated_reverser_timestamp_cannot_widen_the_window(env, monkeypatch):
    from ledger.voids import REVERSAL_MAX_AGE_MS
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    done_ms = int(done.hlc.split(".")[0])
    env.clock.advance(REVERSAL_MAX_AGE_MS / 1000 + 60)          # the hub's clock: past the window
    v = T._signed_into(env, owner, "ledger.void", _void(done), hlc=f"{done_ms + 1:013d}.0000.{T.OWNER}")
    with pytest.raises(H.HubRefused) as exc:
        h.append(owner._submission(v))
    assert exc.value.code == "reversal_closed", exc.value
    assert h._state()[1].items["t"].status == "completed"
    T.git_pull(owner.space_dir, env.hub_dir)                   # and by git: judged at first sight
    published = T._published(env, h, [own, wa, owner])
    assert h._state()[1].items["t"].status == "completed"
    assert wa.receipt_check(published, required=[done.hash])["status"] == "confirmed"


def test_r6_a_reversal_inside_the_window_by_the_hubs_clock_takes_effect(env, monkeypatch):
    from ledger.voids import REVERSAL_MAX_AGE_MS
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    env.clock.advance(REVERSAL_MAX_AGE_MS / 1000 - 60)
    v, answer = owner.append("ledger.void", _void(done))
    assert answer["status"] == "accepted", answer
    assert h._state()[1].items["t"].status == "claimed"


def test_r6_the_window_constant_is_one_named_hour():
    import ledger.voids as voids
    assert voids.REVERSAL_MAX_AGE_MS == 60 * 60 * 1000
    assert not hasattr(voids, "SEAL_INTERVAL_S") and not hasattr(voids, "CORRECTION_WINDOW_FACTOR")


# ── H2: generic verification applies the same correction rule ────────────────

def test_r6_generic_verification_never_honours_a_closed_reversal(env, monkeypatch):
    from ledger.verify import verify_chain
    from ledger.voids import for_events_dir
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    h.seal()
    T._signed_into(env, owner, "ledger.void", _void(done))
    T.git_pull(owner.space_dir, env.hub_dir)
    assert h._state()[1].items["t"].status == "completed"
    assert not for_events_dir(env.hub_dir / ".datacore/events", registry_path=env.registry).applies("wa", done)
    p = env.hub_dir / ".datacore/events/wa.jsonl"
    events = [from_line(line) for line in p.read_text().splitlines()]
    p.write_text("".join(to_line(replace(e, sig="invalid") if e.hash == done.hash else e) + "\n" for e in events))
    assert verify_chain(p, registry_path=env.registry, strict=True) != []


# ── C3: every reader and the seal agree with the receipt ─────────────────────

def test_r6_settled_reader_seal_and_receipt_agree_after_a_closed_reversal(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    first = T._published(env, h, [own, wa])
    assert wa.receipt_check(first, required=[done.hash])["status"] == "confirmed"
    v = T._signed_into(env, owner, "ledger.void", _void(done))
    T.git_pull(owner.space_dir, env.hub_dir)
    second = T._published(env, h, [own, wa, owner])
    assert v.hash in _rejected_hashes(h)
    assert wa.receipt_check(second, required=[done.hash])["status"] == "confirmed"
    assert owner.receipt_check(second, required=[v.hash])["status"] != "confirmed"
    assert H.seal_status(second, registry_path=env.registry)[0] is True
    tip = H.authoritative_state(second, registry_path=env.registry)
    settled = H.authoritative_state(second, registry_path=env.registry, settled=True)
    assert tip.items["t"].status == "completed"
    assert settled.items["t"].status == "completed"


def test_r6_a_rejected_reversal_never_removes_its_target_in_any_fold(env, monkeypatch):
    """Rejected proposals are excluded BEFORE reversals are resolved."""
    from ledger.fold import fold
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    h.seal()
    T._signed_into(env, owner, "ledger.void", _void(done))
    T.git_pull(owner.space_dir, env.hub_dir)
    published = T._published(env, h, [own, wa, owner])
    events, kw = H.authoritative_events(published, registry_path=env.registry)
    work = [e for e in events if e.type != "ledger.seal"]
    assert fold(work, **kw).items["t"].status == "completed"     # what the seal folds
    assert fold(events, **kw).items["t"].status == "completed"   # what the tip reader folds


# ── C2: a promoted grantor starts from its predecessor's seal, not the disk ──

def test_r6_a_promoted_hub_never_confirms_a_late_predecessor_result(env):
    h, own, wa, g = _setup(env)
    env.clock.advance(901)
    done = T._signed_into(env, wa, "item.complete",
                          {"id": "t", "attempt": g["attempt"], "task_hash": g["task_hash"]})
    T.git_pull(wa.space_dir, env.hub_dir)
    h.fence("test promotion")
    H.promote(env.hub_dir, "hubb", by=T.OWNER, reason="test promotion",
              keys_dir=env.keys_dir, registry_path=env.registry,
              attest_stopped=True, clock=env.clock)
    env.clock.advance(1000)
    promoted = env.hub("hubb", eligibility=None)
    report = wa.receipt_check(T._published(env, promoted, [own, wa]), required=[done.hash])
    assert promoted._state()[1].items["t"].status != "completed"
    assert report["status"] != "confirmed" and not report["ok"], report


def test_r6_a_standby_never_judges_what_it_accepts(env):
    """A standby takes appends but judges no lease: its acceptance is no
    evidence that a result was in time once it is promoted."""
    h, own, wa, g = _setup(env)
    standby = env.hub("hubb", eligibility=None)
    env.clock.advance(901)
    done = T._signed_into(env, wa, "item.complete",
                          {"id": "t", "attempt": g["attempt"], "task_hash": g["task_hash"]})
    assert standby.append(wa._submission(done))["status"] == "accepted"
    assert standby._judged_view().get("wa", (-1, set()))[0] < done.seq
    assert done.seq not in standby._judged_view().get("wa", (-1, set()))[1]


# ── C1: a corrupt judged record is a lost one, and a seal is evidence ────────

def test_r6_a_corrupt_judged_record_never_changes_a_confirmed_result(env):
    h, own, wa, g = _setup(env)
    done, _ = wa.append("item.complete", {"id": "t", "attempt": g["attempt"], "task_hash": g["task_hash"]})
    published = T._published(env, h, [own, wa])
    assert wa.receipt_check(published, required=[done.hash])["status"] == "confirmed"
    env.clock.advance(901)
    h.close()
    h._judged_path.write_text("{broken")
    again = env.hub(eligibility=None)
    published2 = T._published(env, again, [own, wa])
    after = wa.receipt_check(published2, required=[done.hash])
    assert after["status"] == "confirmed", after


def test_r6_a_judged_record_corrupted_while_running_is_rebuilt_from_seals(env):
    h, own, wa, g = _setup(env)
    done, _ = wa.append("item.complete", {"id": "t", "attempt": g["attempt"], "task_hash": g["task_hash"]})
    published = T._published(env, h, [own, wa])
    assert wa.receipt_check(published, required=[done.hash])["status"] == "confirmed"
    env.clock.advance(901)
    h._judged_path.write_text("[1, 2")
    after = wa.receipt_check(T._published(env, h, [own, wa]), required=[done.hash])
    assert after["status"] == "confirmed", after


# ── H4: a timely result accepted over the network keeps its judgment ────────

def test_r6_a_timely_network_result_behind_an_import_gap_stays_confirmed(env):
    h, own, wa, g = _setup(env)
    first = T._signed_into(env, wa, "item.create", {"id": "unrelated"})
    T.git_pull(wa.space_dir, env.hub_dir)
    done = T._signed_into(env, wa, "item.complete",
                          {"id": "t", "attempt": g["attempt"], "task_hash": g["task_hash"]},
                          hlc=f"{int(first.hlc.split('.')[0]) + 1:013d}.0000.wa")
    assert h.append(wa._submission(done))["status"] == "accepted"
    assert env.clock() < g["expires_ms"]
    env.clock.advance(901)
    published = T._published(env, h, [own, wa])
    after = wa.receipt_check(published, required=[done.hash])
    assert after["status"] == "confirmed", after
    assert done.hash not in h._state()[1].items["t"].lease_unresolved


def test_r6_a_late_import_behind_the_gap_is_still_late(env):
    """The control for H4: recording a timely judgment never vouches for the
    late result below it."""
    h, own, wa, g = _setup(env)
    env.clock.advance(901)
    late = T._signed_into(env, wa, "item.complete",
                          {"id": "t", "attempt": g["attempt"], "task_hash": g["task_hash"]})
    T.git_pull(wa.space_dir, env.hub_dir)
    other = T._signed_into(env, wa, "item.create", {"id": "unrelated"},
                           hlc=f"{int(late.hlc.split('.')[0]) + 1:013d}.0000.wa")
    assert h.append(wa._submission(other))["status"] == "accepted"
    report = wa.receipt_check(T._published(env, h, [own, wa]), required=[late.hash])
    assert report["status"] != "confirmed", report


# ── C6: the job-health reader takes events through admission ────────────────

def test_r6_the_job_health_reader_admits_before_it_decides(env, monkeypatch):
    import actor_identity
    import job_attestations
    import ledger.keys as keys
    T._with_principals(env, monkeypatch)
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    h = env.hub()
    wa = env.writer("wa", H.LocalTransport(h))
    e = T._signed_into(env, wa, "metric.attest",
                       {"metric": "job.verify", "job": "critical-job", "ok": True, "failures": []})
    wa.log.path.write_text(to_line(replace(e, sig="")) + "\n")
    T.git_pull(wa.space_dir, env.hub_dir)
    events, _ = H.authoritative_events(env.hub_dir, registry_path=env.registry)
    assert e.hash not in {x.hash for x in events}
    got = job_attestations.latest_jobs(env.hub_dir, env.clock() / 1000,
                                      registry=Path(actor_identity.PRINCIPALS))
    assert "critical-job" not in got.get("wa", {}), got


def test_r6_a_signed_job_attestation_in_a_hub_space_still_counts(env, monkeypatch):
    import actor_identity
    import job_attestations
    import ledger.keys as keys
    T._with_principals(env, monkeypatch)
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    h = env.hub()
    wa = env.writer("wa", H.LocalTransport(h))
    wa.append("metric.attest", {"metric": "job.verify", "job": "critical-job", "ok": True, "failures": []})
    T.git_pull(wa.space_dir, env.hub_dir)
    got = job_attestations.latest_jobs(env.hub_dir, (T._real_ms() + 60_000) / 1000,
                                      registry=Path(actor_identity.PRINCIPALS))
    assert got["wa"]["critical-job"].ok


# ── the guards: one correction rule, and no decision reader around admission ─

_LIB = Path(H.__file__).resolve().parents[1]
_ADMISSION_CALLS = {"authoritative_state", "authoritative_events", "_authoritative", "admit", "seal_status"}
#: Readers of raw events inside the decision gates' import closure that are the
#: admission path itself, each with why.
_RAW_READ_ALLOWED = {
    # read_events is defined here; its own helpers read what they write.
    "ledger/log.py",
    # The admission machinery: owner records verify their own signatures and
    # chain; promotion runs admit_chain; the rest feeds `admit`.
    "ledger/hub.py",
    "ledger/hub_admission.py",
}
#: The gates whose answers decide work, and everything they import.
_DECISION_GATES = ("claim_gate", "jobs.autofix", "ledger_claim", "ledger.policy", "ledger.keys",
                   "job_attestations", "reliability_scoreboard")


def _modules(lib: Path) -> dict:
    return {".".join(p.relative_to(lib).with_suffix("").parts): p
            for p in lib.rglob("*.py") if "tests" not in p.parts}


def _imports(lib: Path, p: Path, mods: dict) -> set:
    tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    pkg = p.relative_to(lib).with_suffix("").parts[:-1]
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                up = pkg[:len(pkg) - (n.level - 1)] if n.level > 1 else pkg
                base = ".".join([*up, *([base] if base else [])])
            out |= {base, *(f"{base}.{a.name}" for a in n.names)}
        elif isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
    return {m for m in out if m in mods}


def _decision_closure(lib: Path, roots=_DECISION_GATES) -> dict:
    mods = _modules(lib)
    seen, stack = set(), [r for r in roots if r in mods]
    while stack:
        m = stack.pop()
        if m not in seen:
            seen.add(m)
            stack += _imports(lib, mods[m], mods)
    return {m: mods[m] for m in sorted(seen)}


def _raw_reads(path: Path, lib: Path) -> list[str]:
    """Every `read_events(...)` whose result is used for anything but an
    argument of the admission path -- directly, or through a name that is
    only ever passed to it. A reader that scans raw events decides around
    admission whether or not it folds (Codex round 5, C6)."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}

    def is_call(node, names):
        f = getattr(node, "func", None)
        return isinstance(node, ast.Call) and (getattr(f, "id", None) in names or getattr(f, "attr", None) in names)

    def scope_of(node):
        while node in parents and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            node = parents[node]
        return node

    out = []
    for call in [n for n in ast.walk(tree) if is_call(n, {"read_events"})]:
        up = parents.get(call)
        if is_call(up, _ADMISSION_CALLS):
            continue
        if isinstance(up, ast.Assign) and len(up.targets) == 1 and isinstance(up.targets[0], ast.Name):
            name = up.targets[0].id
            uses = [n for n in ast.walk(scope_of(up)) if isinstance(n, ast.Name) and n.id == name
                    and isinstance(n.ctx, ast.Load)]
            if uses and all(is_call(parents.get(u), _ADMISSION_CALLS) for u in uses):
                continue
        out.append(f"{path.relative_to(lib)}:{call.lineno}")
    return out


def test_r6_no_decision_reader_scans_raw_events():
    offenders = [hit for mod, p in _decision_closure(_LIB).items()
                 if str(p.relative_to(_LIB)) not in _RAW_READ_ALLOWED for hit in _raw_reads(p, _LIB)]
    assert offenders == [], offenders


def test_r6_the_raw_read_guard_sees_a_scanning_reader(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    shapes = {
        "scan.py": "from ledger.log import read_events\n"
                   "def ok(space):\n    return [e for e in read_events(space) if e.type == 'metric.attest']\n",
        "loop.py": "from ledger.log import read_events\n"
                   "def ok(space):\n    events = read_events(space)\n    for e in events:\n        pass\n",
        "admitted.py": "from ledger.log import read_events\nfrom ledger.hub import authoritative_events\n"
                       "def ok(space):\n    events = read_events(space)\n"
                       "    return authoritative_events(space, events)\n",
    }
    for name, text in shapes.items():
        (lib / name).write_text(text)
    hits = {name: _raw_reads(lib / name, lib) for name in shapes}
    assert hits["scan.py"] and hits["loop.py"], hits
    assert hits["admitted.py"] == [], hits
    assert "job_attestations" in _decision_closure(_LIB)        # C6's reader is a decision reader


#: Calls that decide which corrections take effect. Only the shared finality
#: function (ledger.finality) makes that decision; voids.py keeps the old
#: rule for spaces without a hub, behind it.
_CORRECTION_DECIDERS = {"from_events", "closed_reversals", "_rejected_by_grantors", "resolve_voids"}
_CORRECTION_ALLOWED = {"ledger/finality.py", "ledger/voids.py"}


def test_r6_only_the_shared_finality_function_decides_corrections():
    offenders = []
    for p in sorted(_LIB.rglob("*.py")):
        rel = str(p.relative_to(_LIB))
        if "tests" in p.parts or rel in _CORRECTION_ALLOWED:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        for n in ast.walk(tree):
            f = getattr(n, "func", None) if isinstance(n, ast.Call) else None
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            if name in _CORRECTION_DECIDERS:
                offenders.append(f"{rel}:{n.lineno} {name}")
    assert offenders == [], offenders


def test_r6_the_fold_seal_and_receipt_take_corrections_from_finality():
    from ledger import finality  # noqa: F401 -- the one function exists
    fold_src = (_LIB / "ledger/fold.py").read_text()
    assert "finality.resolve(" in fold_src or "from .finality import resolve" in fold_src
