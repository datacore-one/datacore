"""OPS-13: A scheduled AI job that could not run -- a usage limit or an expired login --
says so and is marked failed. It never ends as if it had worked.

Kind: deterministic. The REAL job scripts run against a throwaway install, with the
model runtimes (`claude`, `hermes`, `codex`, `openclaw`) replaced by the fleet week
simulator's stand-in (sim/stand_in.py), set to answer like an expired login or like a
usage limit. Nothing reaches the network, Telegram, a git remote or the ledger: the
job's side-effect helpers (the Telegram sender, the alert sender, the sync, the ledger
event, the question poster) are recording stubs. The model call path itself --
the job script, the shared wrapper `cos_llm.sh`, the router `cos_route.py` -- is the
real code, deployed the way chief-of-staff's deploy ships it onto the always-on host
(module `server/lib` over core `lib`).

What the owner observes, and what is graded, per job and per refusal:
- "marked failed": the job exits non-zero, OR job_verify's own artifact check for that
  job (the manifest's `artifacts`, run by jobs/checks.run_check exactly as job_verify
  runs it) fails on what the run left behind. Either one is enough; both passing is
  the break.
- "says so": the job's log names the reason (a usage limit or a login problem), so the
  red is actionable. Graded separately so a fix that only flips the exit code is not
  mistaken for the whole promise.

The run is shaped like the box's crontab (cos-server-setup.sh): `<script> >>
~/.datacore/cos/<job>.log 2>&1`. The manifest's own `cmd` omits that redirect (fleet
sim report, item 6); this eval does not grade that separate break.

Seeded failure (fleet week simulator, 2026-09-30, faults F4 and F7): on the
chief-of-staff box `cos_research.sh` logged "FATAL: openrouter failed ... the plan
fallback returned nothing" and exited 0; the inbox, research and tomorrow jobs exited 0
under an expired login, and their `no_crash` log checks matched none of the refusal
messages. Only the 08:00 morning check noticed.

Coverage rule (static): every manifest job whose script calls the box's shared model
wrapper (`cos_llm_call`) is run here; every other job that starts a model runtime
directly must name the eval that holds it to the same promise. A new model job that is
in neither list turns this eval red rather than going unwatched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
DC = LIB.parent
COS_LIB = DC / "modules" / "chief-of-staff" / "server" / "lib"
STAND_IN = LIB / "sim" / "stand_in.py"
MANIFEST = LIB / "jobs" / "manifest.yaml"
RUNTIMES = ("claude", "hermes", "codex", "openclaw")
REFUSALS = ("expired_login", "usage_limit")

#: What chief-of-staff's deploy ships from server/lib onto the always-on host's lib
#: (same patterns as fleet_week_sim._deploy_cos).
DEPLOYED = ("cos_*", "winston_*", "miles_delivery.*", "lens_analyze.py", "ws_chat_probe.py")

#: Box jobs run hermetically here: manifest job -> (deployed script, crontab log name).
BOX_JOBS = {
    "box-inbox": ("cos_inbox.sh", "inbox.log"),
    "box-research": ("cos_research.sh", "research.log"),
    "box-tomorrow": ("cos_tomorrow.sh", "tomorrow.log"),
}

#: Model jobs that do not go through cos_llm_call, and the eval that holds each to
#: this promise. Reviewed by hand; the static test checks each file exists.
COVERED_ELSEWHERE = {
    "nightshift-github-triage-report": "this file: test_the_github_triage_report_fails_when_the_model_cannot_run",
    "cadence-firm-cos-briefing": "modules/ventures/tests/test_promise_AGT4_login_limit_says_so_and_waits.py",
    "cadence-firm-cos-weekly-plan": "modules/ventures/tests/test_promise_AGT4_login_limit_says_so_and_waits.py",
    # a failed audit night (a refused model included) is told to The Firm
    "box-audit-check": "lib/tests/test_promise_AUD6_cost_cap_and_missed_night.py",
    "box-audit-nightly": "lib/tests/test_promise_AUD6_cost_cap_and_missed_night.py",
    "box-audit-calibration": "lib/tests/test_promise_AUD6_cost_cap_and_missed_night.py",
    "box-audit-publish": "lib/tests/test_promise_AUD6_cost_cap_and_missed_night.py",
    # a usage limit defers the night's tasks and the deferral is reported
    "nightshift-overnight": "modules/nightshift/tests/test_promise_NS7_caps_defer_not_fail.py",
    # owner-approved 2026-09-30: the claim loop exits 1 on a failed item and pauses
    # claiming on a login or credit failure; the sweep's refused run is a failed day
    "box-ledger-claim": "lib/tests/test_claim_access_block.py",
    "mac-session-learning": "lib/tests/test_session_learning_sweep_refusal.py",
}

#: Model jobs the static discovery below cannot see, because their runtime is chosen
#: in Python at run time (an agent-runner table, the nightshift executor). Listed by
#: hand so they are held to the same rule as the ones it finds.
KNOWN_INDIRECT = {
    "box-ledger-claim": "ledger_claim_run.sh -> ledger_claim.py (runs the claimed task's agent)",
    "nightshift-overnight": "nightshift run -> execute.py (claude -p per task)",
}

#: Refusal wording a log must carry for the red to be actionable ("says so").
SAYS_WHY = re.compile(r"usage limit|limit reached|log ?in|/login|expired|invalid api key|"
                      r"401|402|quota|could not run", re.I)


# ----------------------------------------------------------------------------- fixture

def _link_stand_ins(bin_dir: Path) -> None:
    """The simulator's stand-in under each runtime's name (it reads its name from
    argv[0]), run by this interpreter."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    body = STAND_IN.read_text().split("\n", 1)[1]
    for name in RUNTIMES:
        target = bin_dir / name
        target.write_text(f"#!{sys.executable}\n{body}")
        target.chmod(0o755)


def _stub(path: Path, body: str) -> None:
    path.unlink(missing_ok=True)
    path.write_text(body)
    path.chmod(0o755)


def _box(tmp: Path, mode: str) -> dict:
    """A throwaway always-on host: its home, its ~/Data with core lib + deployed CoS
    scripts, recording stubs for everything that would leave the machine."""
    home = tmp / "home"
    data = home / "Data"
    lib = data / ".datacore" / "lib"
    lib.mkdir(parents=True)
    # core lib, linked entry by entry (cheap, read-only in effect)
    for entry in LIB.iterdir():
        if entry.name in ("tests", "__pycache__"):
            continue
        (lib / entry.name).symlink_to(entry)
    # what deploy.sh ships from the module over core lib
    users: dict[str, int] = {}
    for pat in DEPLOYED:
        for f in COS_LIB.glob(pat):
            if f.is_file():
                for u in re.findall(r"/home/([a-z_][a-z0-9_-]*)/", f.read_text(errors="replace")):
                    users[u] = users.get(u, 0) + 1
    literal = f"/home/{max(users, key=users.get)}" if users else None
    for pat in DEPLOYED:
        for f in COS_LIB.glob(pat):
            if not f.is_file():
                continue
            dst = lib / f.name
            dst.unlink(missing_ok=True)
            text = f.read_text(errors="replace")
            if literal:
                # the scripts name the box's home literally (fleet sim report, item 7);
                # point that one path at this throwaway home, as the simulator does
                text = text.replace(literal + "/", str(home) + "/").replace(literal + " ", str(home) + " ") \
                           .replace(literal + "\n", str(home) + "\n")
                text = re.sub(re.escape(literal) + r"(?=[\s\"';]|$)", str(home), text, flags=re.M)
            dst.write_text(text)
            dst.chmod(0o755)
    agent_bin = lib / "agent-bin"
    if agent_bin.is_symlink() or agent_bin.exists():
        agent_bin.unlink() if agent_bin.is_symlink() else shutil.rmtree(agent_bin)
    shutil.copytree(COS_LIB / "agent-bin", agent_bin)
    # the stand-in runtimes, ahead of anything real: on PATH and in the wrapper's
    # agent-bin (cos_llm_call puts agent-bin first, and the scripts prepend
    # /usr/local/bin, where a real runtime may live)
    _link_stand_ins(home / "bin")
    _link_stand_ins(agent_bin)

    rec = home / "recorded"
    rec.mkdir()
    # side effects that must never leave the sandbox: recording stubs
    _stub(lib / "cos_alert.sh", f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {rec}/alerts.log\necho \"[alert] $*\"\n")
    _stub(lib / "cos_sync.sh", f"#!/bin/sh\necho sync >> {rec}/sync.log\n")
    _stub(lib / "cos_ledger_event.sh", f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {rec}/ledger.log\n")
    _stub(lib / "winston_send.py", f"#!/usr/bin/env python3\nimport sys\n"
                                   f"open('{rec}/telegram.log','a').write(sys.stdin.read()+'\\n---\\n')\n")
    _stub(lib / "cos_questions.py", "#!/usr/bin/env python3\nimport sys\nsys.stdin.read() if not sys.stdin.isatty() else None\n")
    _stub(lib / "inbox_dedup.py", "#!/usr/bin/env python3\n")
    # no credential is ever read by a test: the broker answers "cannot serve"
    _stub(lib / "creds.py", "#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n")
    # the inbox job's code-side guards: every space is personal, every check passes
    _stub(lib / "cos_next_actions_guard.py",
          "#!/usr/bin/env python3\nimport sys\nif sys.argv[1:2] == ['inbox-spaces']: print('0-personal')\n")
    research = data / ".datacore" / "modules" / "research" / "lib"
    research.mkdir(parents=True)
    (research / "research_orchestrator.py").write_text("print('Processed: 1 of 1 queued item')\n")

    personal = data / "0-personal"
    (personal / "org").mkdir(parents=True)
    (personal / "org" / "inbox.org").write_text("* a capture waiting for the nightly run\n")
    (personal / "org" / "next_actions.org").write_text("* Tasks\n")
    for d in (".datacore/cos", ".datacore/state", ".config"):
        (home / d).mkdir(parents=True, exist_ok=True)

    sim = home / "sim"
    sim.mkdir()
    (sim / "executor-box.json").write_text(json.dumps({"default": mode}))

    env = {
        "HOME": str(home), "USER": os.environ.get("USER", "t"), "LANG": "C.UTF-8",
        "PATH": f"{home / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "DATACORE_HOME": str(data), "DATACORE_ROOT": str(data),
        "DATACORE_STATE": str(home / ".datacore" / "state"),
        "COS_ROUTE_PYTHON": sys.executable,       # the router's documented interpreter knob
        "COS_AGENT_BIN": str(agent_bin),
        "SIM_STATE": str(sim), "SIM_MACHINE": "box",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        "DATACORE_POLICY_SPACE": str(home / "policy-scratch"),
    }
    return {"home": home, "data": data, "lib": lib, "rec": rec, "sim": sim, "env": env}


def _route(box: dict, provider: str) -> None:
    (box["home"] / ".datacore" / "cos" / "model_routing.yaml").write_text(
        f"default:\n  provider: {provider}\n  model: null\ntasks: {{}}\n")


def _stand_in_calls(box: dict) -> list[dict]:
    p = box["sim"] / "calls.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def _manifest_job(name: str):
    from jobs.manifest import load_manifest
    for job in load_manifest(MANIFEST):
        if job.name == name:
            return job
    raise AssertionError(f"{name} is not in the manifest")


def _check_failures(job, home: Path) -> list[str]:
    """job_verify's artifact contract for `job`, read on this throwaway host."""
    from jobs import checks
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        return [e for a in job.artifacts for e in checks.run_check(a)]
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old


# ------------------------------------------------------------------------- the wrapper

@pytest.mark.parametrize("provider", ["anthropic", "openrouter"])
@pytest.mark.parametrize("mode", REFUSALS)
def test_the_shared_wrapper_fails_when_the_model_cannot_run(tmp_path, mode, provider):
    """cos_llm_call, the one door every box job's model call goes through, returns
    non-zero when the runtime refuses -- whichever provider the router picked."""
    box = _box(tmp_path, mode)
    _route(box, provider)
    r = subprocess.run(["bash", "-c", f"source {box['lib'] / 'cos_llm.sh'} && cos_llm_call research 'say hi'"],
                       env={**box["env"], "SIM_JOB": "wrapper"}, cwd=box["data"],
                       capture_output=True, text=True, timeout=120)
    calls = _stand_in_calls(box)
    assert calls, f"no stand-in was called -- the test would not be hermetic: {r.stderr[-400:]}"
    assert r.returncode != 0, (f"cos_llm_call returned 0 although every runtime refused ({mode}, "
                               f"{provider}): {(r.stdout + r.stderr)[-400:]}")


# ------------------------------------------------------------------------ the box jobs

#: anthropic: what COS-SERVER.md documents for judgment tasks ("claude -p only");
#: openrouter: the router's own fallback default when no routing file is readable.
ROUTES = ("anthropic", "openrouter")


@pytest.mark.parametrize("provider", ROUTES)
@pytest.mark.parametrize("mode", REFUSALS)
@pytest.mark.parametrize("job_name", sorted(BOX_JOBS))
def test_a_box_ai_job_that_could_not_run_is_marked_failed(tmp_path, job_name, mode, provider):
    script, log_name = BOX_JOBS[job_name]
    box = _box(tmp_path, mode)
    _route(box, provider)
    log = box["home"] / ".datacore" / "cos" / log_name
    cron_line = f"{box['data'] / '.datacore' / 'lib' / script} >> {log} 2>&1"
    r = subprocess.run(["bash", "-c", cron_line], env={**box["env"], "SIM_JOB": job_name},
                       cwd=box["home"], capture_output=True, text=True, timeout=600)
    calls = _stand_in_calls(box)
    assert calls and all(c.get("mode") == mode for c in calls), \
        f"the job never reached a stand-in model -- setup, not the promise: {log.read_text()[-600:] if log.exists() else r.stderr}"
    failures = _check_failures(_manifest_job(job_name), box["home"])
    text = log.read_text() if log.exists() else ""
    assert r.returncode != 0 or failures, (
        f"{job_name} could not run its model ({mode}: every runtime refused, "
        f"{len(calls)} call(s)) yet exited 0 and job_verify's check passed -- "
        f"it ended as if it had worked. Last log lines:\n" + "\n".join(text.splitlines()[-8:]))
    assert SAYS_WHY.search(text), f"{job_name} is failed but its log does not say why:\n{text[-600:]}"


@pytest.mark.parametrize("job_name", sorted(BOX_JOBS))
def test_control_the_same_job_with_a_working_model_exits_0(tmp_path, job_name):
    """The red above is caused by the refusal, not by the sandbox: with a model that
    answers, the same job in the same sandbox exits 0."""
    script, log_name = BOX_JOBS[job_name]
    box = _box(tmp_path, "ok")
    _route(box, "anthropic")
    log = box["home"] / ".datacore" / "cos" / log_name
    r = subprocess.run(["bash", "-c", f"{box['lib'] / script} >> {log} 2>&1"],
                       env={**box["env"], "SIM_JOB": job_name}, cwd=box["home"],
                       capture_output=True, text=True, timeout=600)
    assert _stand_in_calls(box), "the job never reached the stand-in model"
    assert r.returncode == 0, f"{job_name} fails in this sandbox even with a working model:\n" + \
        (log.read_text()[-800:] if log.exists() else r.stderr)


# --------------------------------------------------------------- a host-agnostic model job

@pytest.mark.parametrize("mode", REFUSALS)
def test_the_github_triage_report_fails_when_the_model_cannot_run(tmp_path, mode):
    """The overnight host's triage report starts `claude -p` itself (no wrapper)."""
    home = tmp_path / "home"
    data = home / "Data"
    personal = data / "0-personal"
    personal.mkdir(parents=True)
    env = {"HOME": str(home), "PATH": f"{home / 'bin'}:/usr/bin:/bin", "LANG": "C.UTF-8",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "DATA_DIR": str(data), "TRIAGE_NO_PUSH": "1", "TRIAGE_TIMEOUT": "60",
           "CLAUDE_BIN": str(home / "bin" / "claude"),
           "SIM_STATE": str(tmp_path), "SIM_MACHINE": "nightshift", "SIM_JOB": "triage"}
    _link_stand_ins(home / "bin")
    if shutil.which("timeout", path=env["PATH"]) is None:
        # the host has coreutils' timeout; a Mac may not
        (home / "bin" / "timeout").write_text("#!/bin/sh\nshift\nexec \"$@\"\n")
        (home / "bin" / "timeout").chmod(0o755)
    (tmp_path / "executor-nightshift.json").write_text(json.dumps({"default": mode}))
    subprocess.run(["git", "init", "-q"], cwd=personal, env=env, check=True)
    r = subprocess.run(["bash", str(DC / "skills" / "github-triage" / "run_nightly.sh")],
                       env=env, cwd=home, capture_output=True, text=True, timeout=300)
    assert (tmp_path / "calls.jsonl").exists(), f"no stand-in was called: {r.stderr[-400:]}"
    job = _manifest_job("nightshift-github-triage-report")
    assert r.returncode != 0 or _check_failures(job, home), \
        f"the triage report exited 0 with no model behind it ({mode}): {r.stdout[-400:]}"


# ---------------------------------------------------------------------- coverage rule

_CALL = re.compile(r"\bcos_llm_call\b")
_RUNTIME = re.compile(r"(?:^|[|;&(]|\$\()\s*(?:\w+=\S*\s+)*(?:timeout\s+\S+\s+)?"
                      r"(?:\"?\$\{?CLAUDE_BIN\}?\"?|claude|hermes|codex|openclaw)\s+(?:-p\b|chat\b|exec\b|infer\b|agent\b)")
_PY_RUNTIME = re.compile(r"""[\[(,]\s*['"](?:claude|hermes|codex|openclaw)['"]\s*,""")
#: a script this one RUNS (at a command position), not one it merely mentions
_REF = re.compile(r"""(?:^|[;&|(]|\$\()\s*(?:!\s*)?"""
                  r"""(?:(?:env(?:\s+-u\s+\S+)*|\w+=\S*|source|\.|bash|sh|exec|timeout\s+\S+|"""
                  r"""\S*python3?(?:\s+-\w+)*|"\$\{?PY\}?")\s+)*"""
                  r""""?(?:[^\s"]*/)?([\w-]+\.(?:sh|py))\b""", re.M)


_QUOTED = re.compile(r"'[^']*'" + r'|"((?:[^"\\]|\\.)*)"')


def _code(text: str, py: bool) -> str:
    keep = []
    for line in text.splitlines():
        s = line.strip()
        # comments and messages are not commands
        if not s or s.startswith("#") or (not py and re.match(r"(echo|printf)\b", s)):
            continue
        if not py:
            # nor is prose inside a literal string ("... (claude -p) ..."); a string
            # with an expansion ("$LIB/x.py", "$CLAUDE_BIN") is kept
            line = _QUOTED.sub(lambda m: m.group(0) if m.group(1) and "$" in m.group(1) else '""', line)
        keep.append(line)
    return "\n".join(keep)


def _resolve(name: str, near: Path | None) -> Path | None:
    import fnmatch
    if any(fnmatch.fnmatch(name, p) for p in DEPLOYED) and (COS_LIB / name).is_file():
        return COS_LIB / name
    for d in (near, LIB):
        if d is not None and (d / name).is_file():
            return d / name
    return None


def _cmd_scripts(cmd: str) -> list[Path]:
    out = []
    for m in re.finditer(r"~/(?:Data|\.datacore/v2-runner)/(\S+?)(?=[\s;|&)\"']|$)", cmd):
        rel = m.group(1)
        p = _resolve(Path(rel).name, None) if rel.startswith(".datacore/lib/") else DC.parent / rel
        if p and p.is_file() and p.suffix in (".sh", ".py", "") and p.stat().st_size < 1_000_000:
            out.append(p)
    return out


def _model_use(path: Path, depth: int = 0) -> tuple[str, str] | None:
    """('wrapper'|'direct', evidence) when the script (or one it runs) starts a model."""
    py = path.suffix == ".py"
    code = _code(path.read_text(errors="replace"), py)
    if path.name == "cos_llm.sh":
        return None                              # the wrapper itself, tested above
    if not py and _CALL.search(code):
        return "wrapper", path.name
    if (_PY_RUNTIME if py else _RUNTIME).search(code):
        return "direct", path.name
    if depth == 0 and not py:
        for name in set(_REF.findall(code)):
            q = _resolve(name, path.parent)
            if q and q != path:
                hit = _model_use(q, 1)
                if hit:
                    return hit[0], f"{path.name} -> {hit[1]}"
    return None


def _model_jobs() -> dict[str, tuple[str, str]]:
    import yaml
    out = {}
    for j in yaml.safe_load(MANIFEST.read_text())["jobs"]:
        for f in _cmd_scripts(str(j.get("cmd") or "")):
            hit = _model_use(f)
            if hit:
                out[j["name"]] = hit
                break
    return out


def test_every_model_job_in_the_manifest_is_held_to_this_promise():
    found = _model_jobs()
    found.update({n: ("direct", ev) for n, ev in KNOWN_INDIRECT.items() if n not in found})
    assert {n for n, (kind, _) in found.items() if kind == "wrapper"} >= set(BOX_JOBS), \
        f"the discovery no longer sees the box jobs it should: {found}"
    unwatched = {n: ev for n, (kind, ev) in found.items()
                 if n not in BOX_JOBS and n not in COVERED_ELSEWHERE}
    assert not unwatched, (
        "these scheduled jobs start a model, and no eval shows they are marked failed when "
        "the model cannot run (add them to BOX_JOBS or name their eval in COVERED_ELSEWHERE): "
        + "; ".join(f"{n} ({ev})" for n, ev in sorted(unwatched.items())))
    missing = {n: p for n, p in COVERED_ELSEWHERE.items()
               if not p.startswith("this file") and not (DC / p).is_file()}
    assert not missing, f"COVERED_ELSEWHERE names evals that do not exist: {missing}"
