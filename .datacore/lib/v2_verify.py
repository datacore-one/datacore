#!/usr/bin/env python3
"""The v2 checklist: one command, every gate, honest exit code.

Everything here was checked by hand at least once, ad hoc, in a long session —
which is exactly how a gate stops being checked. The point of this file is that
"is v2 still good?" costs one command instead of an afternoon's archaeology.

THREE OUTCOMES, NOT TWO. A check reports ok / FAIL / n-a, and `n-a` is a real
answer: it means the check could not run, which is different from passing and
different from failing. Collapsing "could not tell" into "fine" is how a machine
sat six weeks behind while every dashboard stayed green; collapsing it into
"broken" manufactures alarms until people stop reading them. Both were live
defects in this system, so the distinction is enforced here.

Checks are grouped by the DIP that owns them, because the useful question when
something goes red is "which part of v2 regressed", not "which script failed".

    v2_verify.py               human-readable checklist
    v2_verify.py --json        machine-readable, for job contracts
    v2_verify.py --quick       skip the slow per-space folds

Exit: 0 all green (n-a allowed), 1 any FAIL, 2 the harness itself broke.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

LIB = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
PY = sys.executable


@dataclass
class Check:
    dip: str
    name: str
    ok: bool | None          # True | False | None (could not run)
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, dip: str, name: str, ok: bool | None, detail: str = "") -> None:
        self.checks.append(Check(dip, name, ok, detail))

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]

    @property
    def unknown(self) -> list[Check]:
        return [c for c in self.checks if c.ok is None]


def run(args: list[str], timeout: int = 180) -> tuple[int, str]:
    """Run a helper. A missing helper is `n-a`, not a failure — this file must
    stay usable on an installation that has not deployed every component."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "not installed"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as exc:
        return 126, str(exc)


def spaces() -> list[Path]:
    return [s for s in sorted(ROOT.glob("[0-9]-*"))
            if (s / ".datacore" / "events").is_dir()]


# ── DIP-0034: event ledger substrate ────────────────────────────────────────
def check_ledger(rep: Report, quick: bool) -> None:
    sp = spaces()
    if not sp:
        rep.add("0034", "hash chains", None, "no space carries an event log")
        return
    bad = []
    for s in sp:
        rc, _ = run([PY, str(LIB / "ledger_cli.py"), "verify", "--space", str(s)], 60)
        if rc != 0:
            bad.append(s.name)
    rep.add("0034", "hash chains", not bad,
            f"{len(sp) - len(bad)}/{len(sp)} verify" + (f"; broken: {', '.join(bad)}" if bad else ""))

    # Per-actor nonces: seq must be dense and unique WITHIN each writer's file.
    # This is the invariant that makes a merge a union — two actors both at
    # seq 82 is correct and expected, like two Ethereum accounts at nonce 82.
    import collections
    dup = gap = 0
    for f in ROOT.glob("[0-9]-*/.datacore/events/*.jsonl"):
        seqs = []
        for line in f.read_text(errors="replace").splitlines():
            if line.strip():
                try:
                    seqs.append(json.loads(line)["seq"])
                except Exception:  # noqa: BLE001 - a torn line is not a nonce fault
                    pass
        if not seqs:
            continue
        dup += sum(1 for _, c in collections.Counter(seqs).items() if c > 1)
        gap += sum(1 for i in range(max(seqs) + 1) if i not in set(seqs))
    rep.add("0034", "per-actor nonces", dup == 0 and gap == 0,
            f"{dup} duplicate, {gap} gapped")

    # FORK CHECK. The nonce check above looks WITHIN one copy; a fork is a
    # disagreement BETWEEN copies and has neither a gap nor a duplicate on
    # either side. It broke in production on 2026-08-13 and was found only
    # because git happened to produce a text conflict.
    sys.path.insert(0, str(LIB))
    try:
        from ledger.fork import detect_all
        reps = detect_all(ROOT)
    except Exception as exc:  # noqa: BLE001
        rep.add("0034", "no forked logs", None, f"detector unavailable: {exc}")
    else:
        forked = [r for r in reps if not r.clean]
        unknown = [r for r in reps if r.reason]
        checked = sum(r.checked for r in reps)
        rep.add("0034", "no forked logs",
                None if (unknown and not forked) else not forked,
                f"{checked} event(s) agree with origin"
                + (f"; FORKED: {', '.join(r.space for r in forked)}" if forked else "")
                + (f"; {len(unknown)} unchecked" if unknown else ""))


# ── DIP-0035: job contracts ─────────────────────────────────────────────────
def check_jobs(rep: Report) -> None:
    jv = LIB / "job_verify.py"
    if not jv.exists():
        rep.add("0035", "job contracts", None, "job_verify.py absent")
        return
    import socket
    machine = os.environ.get("DATACORE_ACTOR") or socket.gethostname().split(".")[0].lower()
    rc, out = run([PY, str(jv), "--machine", machine, "--no-emit"], 200)
    failed = [l.split("'")[1] for l in out.splitlines() if "FAILED" in l and "'" in l]
    ok_line = next((l for l in out.splitlines() if l.startswith("OK")), "")
    rep.add("0035", f"job contracts ({machine})", rc == 0,
            ok_line or (f"failing: {', '.join(failed)}" if failed else out.strip()[:70]))


# ── DIP-0036: config plane ──────────────────────────────────────────────────
def check_config(rep: Report) -> None:
    cp = LIB / "config_plane.py"
    rep.add("0036", "config plane", None if not cp.exists() else True,
            "config_plane.py absent" if not cp.exists() else "present")


# ── DIP-0037/0038: grounded briefings + action loop ─────────────────────────
def check_briefing(rep: Report) -> None:
    need = {"0037": [LIB / "briefing" / "fact_table.py", LIB / "briefing" / "render.py"],
            "0038": [LIB / "briefing" / "actions.py", LIB / "ledger" / "policy.py"]}
    for dip, paths in need.items():
        missing = [p.name for p in paths if not p.exists()]
        rep.add(dip, "modules", not missing,
                "all present" if not missing else f"missing: {', '.join(missing)}")


# ── DIP-0040: agent consolidation ───────────────────────────────────────────
def check_registry(rep: Report) -> None:
    gc = LIB / "registry_gc.py"
    reg = ROOT / ".datacore" / "registry" / "agents.yaml"
    if not (gc.exists() and reg.exists()):
        rep.add("0040", "registry gc", None, "registry_gc.py or agents.yaml absent")
        return
    rc, out = run([PY, str(gc), "--registry", str(reg), "--check"], 90)
    rep.add("0040", "registry gc", rc == 0, out.strip().splitlines()[-1][:70] if out.strip() else "")


# ── DIP-0041: executor adapters + shadow accounting ─────────────────────────
def check_executors(rep: Report) -> None:
    d = LIB / "executors"
    if not d.is_dir():
        rep.add("0041", "executor adapters", None, "executors/ absent")
        return
    adapters = sorted(p.stem for p in d.glob("*.py")
                      if p.stem not in ("__init__", "base"))
    rep.add("0041", "executor adapters", bool(adapters), ", ".join(adapters) or "none")
    v = (ROOT / ".datacore" / "VERSION")
    rep.add("0041", "VERSION", v.exists() and v.read_text().strip().startswith("2."),
            v.read_text().strip() if v.exists() else "absent")


# ── DIP-0043: org projection (shadow / F2 gate) ─────────────────────────────
def check_projection(rep: Report) -> None:
    sc = LIB / "shadow_check.py"
    if not sc.exists():
        rep.add("0043", "org drift", None, "shadow_check.py absent")
        return
    rc, out = run([PY, str(sc)], 250)
    last = next((l for l in reversed(out.splitlines()) if "clean" in l), "")
    # Drift is REPORTED, never failed: Phase 0 is a shadow migration and the
    # gate is a countdown, not a health check. Failing here would make the
    # whole checklist red for the entire migration.
    rep.add("0043", "org drift (informational)", None, last.strip()[:70])

    ck = LIB / "ledger_checkpoint.py"
    if ck.exists():
        rc, out = run([PY, str(ck), "verify"], 250)
        last = next((l for l in reversed(out.splitlines()) if "checkpoint-verify" in l), "")
        rep.add("0043", "checkpoint restores", rc == 0, last.strip()[:70])


# ── DIP-0044: actor identity ────────────────────────────────────────────────
def check_identity(rep: Report) -> None:
    reg = ROOT / ".datacore" / "registry" / "infrastructure.yaml"
    try:
        import yaml
        servers = (yaml.safe_load(reg.read_text()) or {}).get("servers") or {}
    except Exception as exc:  # noqa: BLE001
        rep.add("0044", "actor registry", None, f"unreadable: {exc}")
        return
    missing = [n for n, c in servers.items()
               if isinstance(c, dict) and not (c.get("access") or {}).get("actor")]
    rep.add("0044", "every machine has an actor", not missing,
            f"{len(servers) - len(missing)}/{len(servers)}" +
            (f"; missing: {', '.join(missing)}" if missing else ""))
    # ssh_user == service_user: they differed on exactly one box and that single
    # divergence produced three wrong diagnoses in one session.
    split = [n for n, c in servers.items() if isinstance(c, dict)
             and (a := c.get("access") or {}).get("ssh_user")
             and a.get("service_user") and a["ssh_user"] != a["service_user"]]
    rep.add("0044", "ssh_user == service_user", not split,
            "aligned" if not split else f"differ on: {', '.join(split)}")


# ── DIP-0046: git transport ─────────────────────────────────────────────────
def check_transport(rep: Report) -> None:
    t = LIB / "ledger_transport.py"
    if not t.exists():
        rep.add("0046", "transport", None, "ledger_transport.py absent")
        return
    rep.add("0046", "transport present", True, "converge (merge, never rebase)")

    # No rebase anywhere in the scheduled path. Comments naming the anti-pattern
    # are fine; a live call is not.
    hits = []
    for p in list(LIB.rglob("*.py")) + list(LIB.rglob("*.sh")):
        if "node_modules" in str(p) or p.name == "v2_verify.py":
            continue
        try:
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                s = line.strip()
                if s.startswith(("#", "*", "//")):
                    continue
                if "pull --rebase" in s or "'--rebase'" in s or '"--rebase"' in s:
                    hits.append(f"{p.name}:{i}")
        except OSError:
            continue
    rep.add("0046", "no rebase in sync paths", not hits,
            "clean" if not hits else "; ".join(hits[:3]))

    sg = LIB / "detectors" / "seq_gap.py"
    if sg.exists():
        state = Path.home() / ".datacore" / "state" / "seq-gap.log"
        if state.exists():
            last = state.read_text(errors="replace").strip().splitlines()[-1]
            rep.add("0046", "events published", "0 with unpublished" in last, last[:70])
        else:
            rep.add("0046", "events published", None, "seq-gap.log not written yet")


# ── Fleet: are the OTHER machines' jobs succeeding? ─────────────────────────
def check_fleet(rep: Report) -> None:
    """The blind spot this checklist shipped with.

    Every other check runs against THIS machine. On 2026-08-13 the checklist
    reported 17 ok / 0 FAIL while Winston's every-15-minutes sync had been
    failing on 4 of 9 spaces for hours — the migration left its service user
    without known_hosts, so four spaces died with "host key not trusted" and
    nothing here noticed. A fleet-wide system with a single-machine checklist
    is a checklist that certifies the one box you were already looking at.
    """
    import yaml
    try:
        reg = yaml.safe_load((ROOT / ".datacore/registry/infrastructure.yaml").read_text())
        servers = {k: v for k, v in (reg.get("servers") or {}).items()
                   if isinstance(v, dict)}
    except Exception as exc:  # noqa: BLE001
        rep.add("fleet", "remote job health", None, f"registry unreadable: {exc}")
        return

    unreachable, failing, ok_hosts = [], [], []
    for name, cfg in servers.items():
        access = cfg.get("access") or {}
        alias = cfg.get("ssh_alias") or name
        if alias == "-" or name == "mac":
            continue
        actor = access.get("actor") or name
        # RUNNER FIRST. On hermes and plur-claw `~/Data` is the AGENT'S OWN
        # SPACE repo (tris-space / data-space), not the Datacore core — the
        # core lives in the runner. Using data_root reported both as failing
        # when they were simply being asked the wrong directory, the same
        # mistake fleet_status.py already carries a comment about.
        runner = access.get("runner") or ""
        roots = [r for r in (runner, access.get("data_root") or "~/Data") if r]
        # The manifest keys jobs by REGISTRY NAME (winston, nightshift,
        # plur-claw...), not by ledger actor — `miles` has no jobs, `nightshift`
        # does. And job_verify resolves the manifest through DATACORE_ROOT, so
        # that must point at whichever tree actually holds .datacore/lib.
        probe = " || ".join(
            f"(test -f {r}/.datacore/lib/job_verify.py && cd {r} && "
            f"DATACORE_ROOT={r} python3 .datacore/lib/job_verify.py "
            f"--machine {name} --no-emit >/dev/null 2>&1)" for r in roots)
        cmd = probe + "; echo $?"
        rc, out = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                       alias, cmd], 120)
        code = (out or "").strip().splitlines()[-1] if out.strip() else ""
        if rc != 0 or not code.isdigit():
            unreachable.append(name)
        elif code == "0":
            ok_hosts.append(name)
        else:
            failing.append(name)

    rep.add("fleet", "remote job contracts",
            None if (unreachable and not failing) else not failing,
            f"{len(ok_hosts)} ok"
            + (f", FAILING: {', '.join(failing)}" if failing else "")
            + (f", {len(unreachable)} unreachable" if unreachable else ""))


# ── DIP-0042: sequencer / finality ──────────────────────────────────────────
def check_finality(rep: Report) -> None:
    """DIP-0042 is cited by DIP-0046 but has never been written or built.

    Listed explicitly rather than omitted: a checklist that silently skips the
    unbuilt part reports a system as complete because nobody asked about the
    missing piece. `n-a` here is the honest answer and keeps the gap visible on
    every run.
    """
    try:
        ev = (LIB / "ledger" / "events.py").read_text(errors="replace")
    except OSError:
        rep.add("0042", "finality / sequencer", None, "events.py unreadable")
        return
    if "ledger.seal" not in ev:
        rep.add("0042", "finality / sequencer", None,
                "NOT IMPLEMENTED — no finality event type; all reads use the tip")
        return
    rep.add("0042", "finality vocabulary", True, "ledger.seal")

    # Verify every sealed space. An UNSEALED space is n-a, not a failure:
    # sealing is the sequencer's job and most spaces will sit unsealed between
    # runs. A seal that does NOT verify is a real alarm — it means the
    # sequencer's claim disagrees with the events on this machine.
    sys.path.insert(0, str(LIB))
    try:
        from ledger.log import read_events
        from ledger.seal import verify_seal
    except Exception as exc:  # noqa: BLE001
        rep.add("0042", "seals verify", None, f"import failed: {exc}")
        return
    bad, sealed, unsealed = [], 0, 0
    for s_ in spaces():
        try:
            ok, _d = verify_seal(read_events(s_))
        except Exception:  # noqa: BLE001
            continue
        if ok is True:
            sealed += 1
        elif ok is False:
            bad.append(s_.name)
        else:
            unsealed += 1
    rep.add("0042", "seals verify", None if (sealed == 0 and not bad) else not bad,
            f"{sealed} sealed, {unsealed} unsealed"
            + (f"; MISMATCH: {', '.join(bad)}" if bad else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="v2 verification checklist")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quick", action="store_true", help="skip slow per-space folds")
    a = ap.parse_args()

    rep = Report()
    check_ledger(rep, a.quick)
    check_jobs(rep)
    check_config(rep)
    check_briefing(rep)
    check_registry(rep)
    check_executors(rep)
    if not a.quick:
        check_projection(rep)
    check_identity(rep)
    check_transport(rep)
    check_finality(rep)
    if not a.quick:
        check_fleet(rep)

    if a.json:
        print(json.dumps({"checks": [c.__dict__ for c in rep.checks],
                          "failed": len(rep.failed),
                          "unknown": len(rep.unknown)}, indent=2))
        return 1 if rep.failed else 0

    mark = {True: "\033[32mok  \033[0m", False: "\033[31mFAIL\033[0m", None: "\033[33mn-a \033[0m"}
    last_dip = None
    for c in rep.checks:
        if c.dip != last_dip:
            print(f"\n  \033[1mDIP-{c.dip}\033[0m")
            last_dip = c.dip
        print(f"    {mark[c.ok]} {c.name:<32} {c.detail}")

    print(f"\n  {len(rep.checks)} check(s): "
          f"{sum(1 for c in rep.checks if c.ok is True)} ok, "
          f"{len(rep.failed)} FAIL, {len(rep.unknown)} could-not-run")
    if rep.failed:
        print("  \033[31mv2 REGRESSED\033[0m — " + "; ".join(c.name for c in rep.failed))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
