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

sys.path.insert(0, str(LIB))
from spaces import discover_spaces  # noqa: E402
PY = sys.executable


@dataclass
class Check:
    dip: str
    name: str
    ok: bool | None          # True | False | None (could not run)
    detail: str = ""
    # `skipped` is the fourth word, and it is narrower than n-a. n-a means
    # "tried, could not tell" — a helper missing, a probe timing out — and it
    # stays in the could-not-run count because it is a gap someone should
    # close. `skipped` means the check is NOT APPLICABLE on this host by
    # design (the fleet probe needs the operator's ssh aliases, which a server
    # does not and must not hold), so counting it as could-not-run on every
    # unattended run reported a permanent gap that no one on that host could
    # close. The exit code never saw the difference; the summary line did.
    skipped: bool = False


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, dip: str, name: str, ok: bool | None, detail: str = "",
            *, skipped: bool = False) -> None:
        self.checks.append(Check(dip, name, ok, detail, skipped))

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]

    @property
    def unknown(self) -> list[Check]:
        return [c for c in self.checks if c.ok is None and not c.skipped]

    @property
    def skipped(self) -> list[Check]:
        return [c for c in self.checks if c.skipped]


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
    return [s.path for s in discover_spaces(ROOT)
            if (s.path / ".datacore" / "events").is_dir()]


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

    # ORPHANS ARE MACHINE-DEPENDENT, so they must not fail a fleet check.
    # An orphan is a registry entry whose agent file is missing HERE. Some
    # module agents are deliberately untracked and exist only on the Mac
    # (health, for one), so Winston reports 13 orphans and the Mac reports 0 —
    # both correct. Failing on that would make this check permanently red on
    # every server for a reason that is not a v2 regression, and a permanently
    # red check is one people stop reading.
    #
    # The other three categories ARE fleet-invariant registry hygiene: stray
    # .bak files, unregistered agents, duplicate keys. Those still fail.
    import re as _re
    counts = {k: int(v) for k, v in
              _re.findall(r"(bak files|unregistered|duplicate keys) \((\d+)\)", out)}
    orphans = next((int(v) for v in
                    _re.findall(r"orphaned \((\d+)\)", out)), 0)
    hygiene_bad = sum(counts.values())
    detail = ", ".join(f"{k}={v}" for k, v in counts.items()) or out.strip()[:48]
    rep.add("0040", "registry hygiene", hygiene_bad == 0,
            detail + (f"; {orphans} orphan(s) — module agents absent on this "
                      f"machine, informational" if orphans else ""))


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



def check_writer_authorship(rep: Report) -> None:
    """Was each writer's log only ever appended by its own principal?

    A per-writer chain is tamper-evident, not authenticated: any account that
    can push can append to any log. The principals registry binds each writer
    log to the git identities that may commit to it (Miles commits the
    executor's `nightshift.jsonl`; the owner commits `mac.jsonl`). Non-merge
    commits only — a merge that carries another writer's lines is a courier,
    not an author — and counted from 2026-09-05, the day this check began
    measuring. Two earlier autosave commits had carried another writer's log
    (0-personal/nightshift by winston, 6-meridian/mac by miles); they are on
    the record in the 2026-09-05 architecture audit and are not re-flagged.
    """
    sys.path.insert(0, str(LIB))
    try:
        from actor_identity import allowed_emails, principals
    except Exception as exc:  # noqa: BLE001
        rep.add("0044", "writer logs authored by their principal", None, f"actor_identity unusable: {exc}")
        return
    if not principals():
        rep.add("0044", "writer logs authored by their principal", None, "principals.yaml absent or empty")
        return
    foreign, unbound, seen = [], set(), 0
    for f in sorted(ROOT.glob("[0-9]-*/.datacore/events/*.jsonl")):
        space, writer = f.parts[-4], f.stem
        allowed = allowed_emails(writer)
        if not allowed:
            unbound.add(writer)
            continue
        rc, out = run(["git", "-C", str(ROOT / space), "log", "--no-merges", "--since=2026-09-05",
                       "--format=%ae", "--", str(f.relative_to(ROOT / space))], 60)
        if rc != 0:
            continue
        seen += 1
        bad = sorted({e.strip().lower() for e in out.splitlines() if e.strip()} - allowed)
        if bad:
            foreign.append(f"{space}/{writer} by {', '.join(bad)[:60]}")
    ok = not foreign
    detail = f"{seen} log(s) checked" + (f"; unbound writer(s): {', '.join(sorted(unbound))}" if unbound else "")
    rep.add("0044", "writer logs authored by their principal", ok,
            detail if ok else f"{len(foreign)} foreign: " + "; ".join(foreign[:3]))


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


# ── The APPLICATION layer: the part that was never watched ─────────────────
def check_declared_identity(rep: Report) -> None:
    """Is this machine's ledger identity DECLARED, or still being guessed?

    Nothing writes `~/.datacore/identity.env` — not the box-setup script, not
    the agent updater — and until this check existed nothing reported its
    absence either. So the two machines whose identity was known to be wrong
    stayed wrong until someone hand-placed a file, and the checklist called it
    green. A mechanism nobody installs and nothing verifies is a mechanism that
    exists only in the machine it was demonstrated on.

    Reports `n-a` rather than FAIL when the file is missing but inference
    happens to land correctly: that is a real, working state, just a fragile
    one — it depends on a registry copy staying fresh. FAIL is reserved for
    identity actually resolving to the hostname, which means events are being
    filed under the wrong writer right now.
    """
    import socket
    sys.path.insert(0, str(LIB))
    try:
        from ledger_attest import _actor, _identity
    except Exception as exc:  # noqa: BLE001
        rep.add("0044", "identity declared", None, f"ledger_attest unusable: {exc}")
        return
    actor = _actor()
    host = socket.gethostname().split(".")[0].lower()
    declared = bool(_identity().get("DATACORE_ACTOR"))
    if actor == host and actor not in ("mac",):
        rep.add("0044", "identity declared", False,
                f"actor={actor} equals hostname — events filed under the wrong writer")
    elif declared:
        rep.add("0044", "identity declared", True, f"actor={actor} from identity.env")
    else:
        rep.add("0044", "identity declared", None,
                f"actor={actor} inferred — no identity.env; correct today, fragile")


def check_egress(rep: Report) -> None:
    """Has any module grown an external action nobody declared? (DIP-0047)

    Two separate questions, because they fail for different reasons and a single
    verdict would hide the useful one.

    IMPORTABILITY is the precondition. `from datacore.ledger import attests` has
    to work for the interpreter that runs the jobs, or every decorator in every
    module silently records nothing — which is exactly how X posting went
    unattested on plur-claw for months while looking fine everywhere else.

    THE RATCHET is checked only for modules that have declared egress. Failing
    every module at once on the day this turns on would guarantee the check gets
    disabled; failing a module that opted in and then grew a new action is the
    signal worth having.
    """
    try:
        subprocess.run([sys.executable, "-c", "import datacore.ledger"],
                       cwd="/", capture_output=True, timeout=60, check=True)
        rep.add("app", "core importable by modules", True,
                "from datacore.ledger import attests")
    except Exception as exc:  # noqa: BLE001
        rep.add("app", "core importable by modules", False,
                f"{type(exc).__name__} — module decorators would record nothing")
        return

    scan = LIB / "egress_scan.py"
    if not scan.is_file():
        rep.add("app", "egress declared", None, "egress_scan.py not present")
        return
    try:
        r = subprocess.run([sys.executable, str(scan), "--enforce"],
                           capture_output=True, text=True, timeout=300)
        head = next((ln for ln in r.stdout.splitlines()
                     if ln.startswith("EGRESS SCAN")), "").replace("EGRESS SCAN — ", "")
        rep.add("app", "egress declared", r.returncode == 0, head or "no summary")
    except Exception as exc:  # noqa: BLE001
        rep.add("app", "egress declared", None, f"{type(exc).__name__}: {exc}")

    # DECLARED IS NOT WIRED. The scan above reads source: it proves a decorator
    # is written above a def. Whether it is in force at runtime is a different
    # question, and it is the one that failed before — an import that resolves
    # on one machine and not another leaves the decorator absent with no error.
    # This imports each declared function and asks the object itself.
    rt = LIB / "egress_runtime_check.py"
    if not rt.is_file():
        rep.add("app", "egress wired at runtime", None, "egress_runtime_check.py not present")
        return
    try:
        r = subprocess.run([sys.executable, str(rt), "--functional"],
                           capture_output=True, text=True, timeout=300)
        line = next((ln.strip() for ln in r.stdout.splitlines()
                     if "runtime wiring:" in ln), "")
        detail = line.replace("runtime wiring: ", "") or "no summary"
        # UNVERIFIABLE IS NOT OK. A module that would not import under THIS
        # interpreter has not been checked, and saying "ok" for it is the exact
        # collapse this file's docstring forbids. Seen immediately: the standalone
        # run verified 28 under python3.11 and the checklist verified 22 under
        # 3.14, because six modules' dependencies are not installed there — a
        # real gap in whichever interpreter the jobs actually use.
        broken = "0 broken" not in detail
        unknown = "0 unverifiable" not in detail
        rep.add("app", "egress wired at runtime",
                False if broken else (None if unknown else True), detail)
    except Exception as exc:  # noqa: BLE001
        rep.add("app", "egress wired at runtime", None, f"{type(exc).__name__}: {exc}")


def check_app(rep: Report) -> None:
    """Everything above verifies the ledger; nothing verified what sits on it.

    Asked three direct questions on 2026-08-14 — is the app tested, will the
    brief run, do X posts reach the ledger — the answers were no, partly, and
    no. All three sat outside what this checklist looked at, and the unifying
    fact was that it checked the substrate thoroughly and the application layer
    not at all.
    """
    # 1. Can agents record external actions at all? This is the capability, not
    #    a specific post: if the module is absent or unimportable, publishing
    #    goes unrecorded and nothing else here would notice.
    sys.path.insert(0, str(LIB))
    try:
        from ledger_attest import _actor, attest  # noqa: F401
        actor = _actor()
        import socket
        host = socket.gethostname().split(".")[0].lower()
        # An actor equal to the hostname is the DIP-0044 trap: it means the
        # registry lookup failed and events would be filed under the wrong
        # writer. Two of five machines were doing exactly that until caught.
        ok = actor != host or actor in ("mac",)
        rep.add("app", "agents can attest actions", ok,
                f"actor={actor}" + ("" if ok else f" — equals hostname; registry lookup failed"))
    except Exception as exc:  # noqa: BLE001
        rep.add("app", "agents can attest actions", False, f"ledger_attest unusable: {exc}")

    # 2. Does the X path actually call it? A capability nothing invokes is not
    #    a capability. Checked by grep because importing the comms module pulls
    #    in credentials this check has no business touching.
    xapi = ROOT / ".datacore/modules/comms/lib/x_api.py"
    if not xapi.is_file():
        rep.add("app", "publishing is attested", None, "comms module not on this machine")
    else:
        # Matches the DECORATOR, not the old private helper. This check looked
        # for `_attest_post`, the hand-rolled function that DIP-0047 replaced —
        # so the moment publishing became correctly attested, the check
        # reporting on it began saying the opposite. A detector keyed to one
        # implementation of the thing it verifies fails exactly when that thing
        # is improved, which is the worst possible time to be told it is broken.
        src = xapi.read_text(errors="replace")
        wired = "@attests(" in src or "_attest_post" in src
        rep.add("app", "publishing is attested", wired,
                "x_api posts and replies attest" if wired
                else "X posts leave NO ledger record")


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

    # WHERE THIS CHECK IS MEANINGFUL. The ssh aliases (winston, miles, tris,
    # data) are defined in the operator's ~/.ssh/config on the Mac. Run from a
    # server, every host is simply unreachable — which reports n-a, honestly,
    # but means an unattended run there verifies only itself. Say so in the
    # detail rather than letting "0 ok, 4 unreachable" read as a fleet outage.
    #
    # And decide that BEFORE probing. Whether an alias exists is a local fact
    # (`ssh -G` resolves the config without connecting); a host with none is
    # not a host whose fleet is down, it is a host this check does not apply
    # to. Probing anyway cost four 10s connect timeouts per run and produced
    # a "could-not-run" that winston's every-12-hours run has carried since
    # the checklist shipped — a gap that host cannot close and should not
    # (servers must not hold fleet keys). Report it as skipped, once, in a
    # line that says where to run it instead.
    targets = []
    for name, cfg in servers.items():
        alias = cfg.get("ssh_alias") or name
        if alias == "-" or name == "mac":
            continue
        targets.append((name, cfg, alias))
    configured = [t for t in targets if _alias_configured(t[2])]
    if targets and not configured:
        rep.add("fleet", "remote job contracts", None,
                f"n-a here: none of the {len(targets)} fleet ssh aliases exists "
                "on this host — run from the operator machine", skipped=True)
        return
    no_alias = [name for name, _c, _a in targets if (name, _c, _a) not in configured]

    unreachable, failing, ok_hosts = [], [], []
    for name, cfg, alias in configured:
        access = cfg.get("access") or {}
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

    note = ""
    if unreachable and not ok_hosts:
        note = " — every configured alias failed to connect"
    rep.add("fleet", "remote job contracts",
            None if (unreachable and not failing) else not failing,
            f"{len(ok_hosts)} ok"
            + (f", FAILING: {', '.join(failing)}" if failing else "")
            + (f", {len(unreachable)} unreachable" if unreachable else "")
            + (f", {len(no_alias)} without an alias here ({', '.join(no_alias)})"
               if no_alias else "")
            + note)


def _alias_configured(alias: str) -> bool:
    """Does ssh on THIS host know `alias`? Decided without connecting.

    `ssh -G` prints the effective config for a name; for a name that no
    `Host` block matches it prints exactly what it prints for any unknown
    name, hostname aside. So an alias is configured iff its resolved config
    differs from that baseline in some option other than `host`/`hostname`. A bare
    resolver lookup is deliberately NOT the test: on winston, MagicDNS resolves
    `nightshift`/`hermes`/`plur-claw` while the box holds no key for any of
    them, and treating "resolves" as "configured" put the probe right back to
    four connect failures per run.
    """
    rc_base, base = run(["ssh", "-G", "datacore-no-such-alias-probe"], 15)
    rc, out = run(["ssh", "-G", alias], 15)
    if rc != 0 or rc_base != 0:
        return False
    def opts(text):
        return {l for l in text.splitlines() if not l.startswith(("host ", "hostname "))}
    return opts(out) != opts(base)


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
    check_app(rep)
    check_declared_identity(rep)
    check_writer_authorship(rep)
    check_egress(rep)
    if not a.quick:
        check_fleet(rep)

    if a.json:
        print(json.dumps({"checks": [c.__dict__ for c in rep.checks],
                          "failed": len(rep.failed),
                          "unknown": len(rep.unknown),
                          "skipped": len(rep.skipped)}, indent=2))
        return 1 if rep.failed else 0

    mark = {True: "\033[32mok  \033[0m", False: "\033[31mFAIL\033[0m", None: "\033[33mn-a \033[0m"}
    skip = "\033[2mskip\033[0m"
    last_dip = None
    for c in rep.checks:
        if c.dip != last_dip:
            print(f"\n  \033[1mDIP-{c.dip}\033[0m")
            last_dip = c.dip
        print(f"    {skip if c.skipped else mark[c.ok]} {c.name:<32} {c.detail}")

    print(f"\n  {len(rep.checks)} check(s): "
          f"{sum(1 for c in rep.checks if c.ok is True)} ok, "
          f"{len(rep.failed)} FAIL, {len(rep.unknown)} could-not-run"
          + (f", {len(rep.skipped)} not applicable here" if rep.skipped else ""))
    if rep.failed:
        print("  \033[31mv2 REGRESSED\033[0m — " + "; ".join(c.name for c in rep.failed))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
