"""The nightly cross-model audit's own behaviour beyond the AUD evals' contract:
one slice end to end, the cost cap before the model turn, publishing and
re-offering, calibration scoring on the real key, the auditor's read-only
policy on shell chains, cross-family PR review markers, and the templates.
No test calls a model: `call_model` is replaced wherever a turn would happen.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _audit_contract import ROOT, finding, findings_file, head_sha  # noqa: E402

import cross_model_audit as cma  # noqa: E402
import tool_policy  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    audits = tmp_path / "audits"
    monkeypatch.setattr(cma, "NIGHTLY", audits / "nightly")
    monkeypatch.setattr(cma, "SUMMARY", audits / "summary")
    monkeypatch.setattr(cma, "POOL", audits / "unconfirmed.yaml")
    monkeypatch.setattr(cma, "CANDIDATES", tmp_path / "evals" / "audit-candidates.yaml")
    monkeypatch.setattr(cma, "SPACE", tmp_path)
    monkeypatch.setattr(cma, "FRAGMENTS", tmp_path / "fragments")
    spend = tmp_path / "spend.jsonl"
    monkeypatch.setattr(cma, "_spend_file", lambda: spend)
    monkeypatch.setattr(cma, "brief", lambda: ("AUDIT BRIEF", "b" * 40))
    return tmp_path


def _answer(*findings):
    return yaml.safe_dump({"findings": list(findings)}, sort_keys=False)


def test_one_slice_end_to_end_keeps_only_checkable_findings(sandbox, monkeypatch):
    monkeypatch.setattr(cma, "rotation", lambda caps, night, agents=None: {a: "audits" for a in cma.AGENTS})
    asked = {}

    def fake_call(family, prompt, *, max_usd, timeout_s=1500):
        asked.update(family=family, prompt=prompt, max_usd=max_usd)
        return {"model": "fake-glm", "usd": 0.03, "text": "```yaml\n" + _answer(
            finding("AUD-3", ".datacore/lib/promise_evals.py:10"),
            finding("AUD-3", ".datacore/lib/no_such_file.py:3"),
            finding("TSK-2", ".datacore/lib/promise_evals.py:12")) + "```\n"}
    monkeypatch.setattr(cma, "call_model", fake_call)

    out = cma.run_nightly("tris", date(2026, 9, 28))

    assert out == cma.NIGHTLY / "2026-09-28" / "tris.yaml"
    assert cma.validate_findings(out, repo=ROOT) == []
    doc = yaml.safe_load(out.read_text())
    assert doc["model"] == "glm" and doc["capability"] == "audits" and doc["commit"] == head_sha()
    assert [f["promise"] for f in doc["findings"]] == ["AUD-3"]
    assert len(doc["rejected"]) == 2, "an invented location and an out-of-slice promise are rejected, not kept"
    assert asked["family"] == "glm" and 0 < asked["max_usd"] <= cma.NIGHTLY_CAP_USD["tris"]
    p = asked["prompt"]
    assert "AUDIT BRIEF" in p and "AUD-3" in p and "no tools" in p
    assert "=== .datacore/lib/tests/test_promise_AUD3_two_families_confirm.py @ " in p
    assert "    1  " in p, "the code is line-numbered so evidence can name a line"
    assert doc["cost_usd"] == pytest.approx(0.03) and cma.spent_today("tris") == pytest.approx(0.03)


def test_the_cap_is_checked_before_the_model_is_called(sandbox, monkeypatch):
    monkeypatch.setattr(cma, "rotation", lambda caps, night, agents=None: {a: "audits" for a in cma.AGENTS})
    cma.record_spend("winston", cma.NIGHTLY_CAP_USD["winston"] - 0.001, "earlier")
    monkeypatch.setattr(cma, "call_model", lambda *a, **k: pytest.fail("a model was called past the cap"))
    with pytest.raises(SystemExit, match="cap"):
        cma.run_nightly("winston", date(2026, 9, 28))
    assert not (cma.NIGHTLY / "2026-09-28" / "winston.yaml").exists(), "a refused night leaves no file to alert on"


def test_a_failed_turn_still_counts_its_spend(sandbox, monkeypatch):
    monkeypatch.setattr(cma, "rotation", lambda caps, night, agents=None: {a: "audits" for a in cma.AGENTS})

    def failing(*a, **k):
        raise cma._Spent("the model returned no answer", 0.2)
    monkeypatch.setattr(cma, "call_model", failing)
    with pytest.raises(SystemExit, match="failed"):
        cma.run_nightly("data", date(2026, 9, 28))
    assert cma.spent_today("data") == pytest.approx(0.2)


def _two_nights(tmp_path):
    sha = head_sha()
    n1, n2 = tmp_path / "n" / "2026-09-27", tmp_path / "n" / "2026-09-28"
    findings_file(n1 / "miles.yaml", agent="miles", capability="tasks", commit=sha,
                  findings=[finding("TSK-2", ".datacore/lib/promise_evals.py:20", "claude saw it")])
    findings_file(n2 / "winston.yaml", agent="winston", capability="tasks", commit=sha,
                  findings=[finding("TSK-2", ".datacore/lib/promise_evals.py:22", "deepseek saw it later")])
    return n1, n2


def test_an_unconfirmed_finding_is_reoffered_and_confirmed_on_a_later_night(sandbox):
    n1, n2 = _two_nights(sandbox)
    first = cma.aggregate(n1, pool=cma.load_pool())
    assert first["confirmed"] == [] and len(first["unconfirmed"]) == 1
    cma.publish(first, today=date(2026, 9, 28))
    pool = cma.load_pool()
    assert [p["families"] for p in pool] == [["claude"]]
    b = {"text": "CODE", "not_read": []}
    offered = [r for r in pool if "deepseek" not in r["families"]]
    assert "claude saw it" in cma.nightly_prompt("winston", "tasks", b, "BRIEF", offered)

    second = cma.aggregate(n2, pool=pool)
    assert [c["families"] for c in second["confirmed"]] == [["claude", "deepseek"]]
    pub = cma.publish(second, today=date(2026, 9, 29))
    rows = yaml.safe_load(cma.CANDIDATES.read_text())["rows"]
    assert len(rows) == 1 and rows[0]["status"] == "red-candidate" and rows[0]["owner_review"] == "pending"
    assert cma.load_pool() == []
    frag = json.loads((cma.FRAGMENTS / "2026-09-29" / "audits.json").read_text())
    assert len(frag["needs_you"]) == 1
    assert yaml.safe_load((cma.SUMMARY / "2026-09-28.yaml").read_text())["new_candidates"] == [
        pub["new"][0]["source"]["id"]]
    # the same agreement on a later night is not a second candidate
    assert cma.publish(cma.aggregate(n2, pool=[] + second["confirmed"]), today=date(2026, 9, 30))["new"] == []
    assert len(yaml.safe_load(cma.CANDIDATES.read_text())["rows"]) == 1


def test_calibration_scores_the_real_key_by_the_nearest_planted_defect():
    key = yaml.safe_load((cma.CALIBRATION / "keys" / "ledgerlite.yaml").read_text())
    got = cma.calibrate(key["planted"], {
        "claude": [{"evidence": "ledgerlite/fold.py:18"},       # H1
                   {"evidence": "ledgerlite/fold.py:14"},       # H12, not H1 again
                   {"evidence": "ledgerlite/health.py:30"},     # decoy X1: a false positive
                   {"evidence": "ledgerlite/config.py:10"}],    # H13
        "gpt": [{"evidence": "not-a-location"}]}, tolerance=key["tolerance"])
    assert got["claude"]["found"] == ["H1", "H12", "H13"] and got["claude"]["false_positives"] == 1
    assert got["claude"]["recall"] == pytest.approx(3 / 13, abs=1e-3)
    assert got["gpt"] == {"recall": 0.0, "false_positives": 1, "found": [], "missed": got["gpt"]["missed"]}


def test_the_key_is_pinned_to_the_fixture_it_describes():
    if subprocess.run(["git", "-C", str(cma.DEV), "rev-parse", "lab"], capture_output=True, timeout=30).returncode:
        pytest.skip("this checkout of the dev module has no lab branch")
    repo, sha, root, files = cma.fixture()
    assert "ledgerlite/fold.py" in files and not any("answer" in f for f in files)


@pytest.mark.parametrize("cmd", [
    "grep -rn 'def ' .datacore/lib | head -5",
    "git show HEAD:.datacore/lib/tool_policy.py | wc -l",
    "cat .datacore/lib/job_verify.py 2>/dev/null",
    "find .datacore/lib -name '*.py' | sort",
])
def test_the_auditor_may_read_with_the_shell(cmd):
    assert tool_policy.decide("auditor", "Bash", {"command": cmd}).allow, cmd


@pytest.mark.parametrize("tool,args", [
    ("Bash", {"command": "python3 -c \"open('x','w').write('y')\""}),
    ("Bash", {"command": "cat a > b"}),
    ("Bash", {"command": "ls; rm -rf .datacore"}),
    ("Bash", {"command": "git log $(touch x)"}),
    ("Bash", {"command": "cat a\nrm b"}),
    ("Bash", {"command": "find . -name '*.pyc' -delete"}),
    ("Bash", {"command": "grep x a | sh"}),
    ("Write", {"file_path": str(ROOT / "2-datacore/1-tracks/dev/audits/nightly/../../notes.yaml"
                                          ), "content": "x"}),
    ("Write", {"file_path": str(ROOT / "2-datacore/1-tracks/dev/audits/nightly/2026-09-27/summary.yaml"),
               "content": "x"}),
    ("mcp__datacore-app__gtd_add_task", {"title": "x"}),
    ("execute_code", {"code": "print(1)"}),
])
def test_the_auditor_changes_nothing(tool, args):
    assert not tool_policy.decide("auditor", tool, args).allow, (tool, args)


def test_the_write_effect_binds_only_the_auditor():
    edit = ("Edit", {"file_path": str(ROOT / ".datacore/lib/job_verify.py"), "old_string": "a", "new_string": "b"})
    assert "write" in tool_policy.decide("miles", *edit).effects
    assert tool_policy.decide("miles", *edit).allow


def test_a_pr_is_ready_only_after_another_family_commented(monkeypatch):
    comments = [{"body": "LGTM"}, {"body": "<!-- cross-model-review family=gpt -->\n**Review** ..."}]
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: (
        subprocess.CompletedProcess(cmd, 0, json.dumps({"comments": comments}), "")
        if cmd[:3] == ["gh", "pr", "view"] else real(cmd, *a, **k)))
    assert cma.pr_ready("https://github.com/o/r/pull/1", "claude") is True
    assert cma.pr_ready("https://github.com/o/r/pull/1", "gpt") is False, "its own family's comment is no review"


def test_night_alerts_names_an_agent_on_the_wrong_model(tmp_path):
    d = tmp_path / "2026-09-27"
    for agent in cma.AGENTS:
        findings_file(d / f"{agent}.yaml", agent=agent, capability=f"c-{agent}", commit=head_sha(),
                      findings=[finding()], model="claude" if agent == "data" else None)
    alerts = cma.night_alerts(d)
    assert len(alerts) == 1 and "data ran on claude" in alerts[0]


@pytest.mark.parametrize("agent", sorted(cma.AGENTS))
def test_each_agent_has_its_nightly_and_calibration_template(agent):
    tpl = ROOT / ".datacore" / "templates" / "cadences"
    for kind, where in (("nightly", f"nightly/{{date}}/{agent}.yaml"),
                        ("calibration", f"calibration/runs/{{date}}-{agent}.yaml")):
        text = (tpl / f"audit-{kind}-{agent}.md").read_text()
        meta = yaml.safe_load(text[4:].partition("\n---\n")[0])
        assert meta["evidence"]["path"].endswith(where) and meta["evidence"]["space"] == "datacore"
        assert f"^model: {cma.AGENTS[agent]}$" in meta["evidence"]["require"]
        script = ROOT / meta["script"]
        assert script.is_file() and os.access(script, os.X_OK)


def test_the_audit_roster_is_the_installs_own(monkeypatch):
    """INS-3: agents, families and caps come from principals.yaml; none ship."""
    import roster
    monkeypatch.setattr(roster, "section", lambda key, path=None: {
        "agents": {"ops": "claude", "cos": "gpt"}, "nightly_cap_usd": {"ops": 1, "cos": 0.5}}
        if key == "cross_model_audit" else {})
    assert cma._audit_roster() == ({"ops": "claude", "cos": "gpt"}, {"ops": 1.0, "cos": 0.5})
    monkeypatch.setattr(roster, "section", lambda key, path=None: {})
    assert cma._audit_roster() == ({}, {})


# ── model access comes from the install, never from the code ────────────────
def _models(monkeypatch, models):
    import roster
    monkeypatch.setattr(roster, "section", lambda key, path=None: {"models": models} if key == "cross_model_audit" else {})


def test_no_model_id_is_written_into_the_code():
    for fam, f in cma.FAMILIES.items():
        assert f["model"] == "", f"{fam}: a model id in the code is a guess; it belongs in the install's roster"


def test_the_model_and_its_access_come_from_the_installs_roster(monkeypatch):
    _models(monkeypatch, {"glm": {"model": "z-ai/glm-test", "transport": "openrouter"}})
    f = cma.family_config("glm")
    assert f["model"] == "z-ai/glm-test" and f["transport"] == "openrouter"
    assert f["in"] == cma.FAMILIES["glm"]["in"], "prices and limits stay the code's"


def test_a_family_with_no_configured_model_is_refused_not_guessed(monkeypatch):
    _models(monkeypatch, {})
    monkeypatch.setattr(cma, "_post_json", lambda *a, **k: pytest.fail("a model was called with no model id"))
    with pytest.raises(RuntimeError, match="no model"):
        cma.call_model("deepseek", "x", max_usd=0.1)


def test_a_local_model_is_reached_without_a_key(monkeypatch):
    _models(monkeypatch, {"glm": {"transport": "local", "model": "glm-local", "base_url": "http://127.0.0.1:11434/v1/"}})
    seen = {}

    def fake_post(url, body, headers, timeout, **k):
        seen.update(url=url, body=body, headers=headers, keyed=k.get("keyed", True))
        return {"model": "glm-local", "choices": [{"message": {"content": "findings: []"}}], "usage": {}}
    monkeypatch.setattr(cma, "_post_json", fake_post)
    monkeypatch.setattr(cma, "_secret", lambda name: pytest.fail("a local model needs no key"))
    out = cma.call_model("glm", "x", max_usd=0.1)
    assert seen["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert "Authorization" not in seen["headers"] and seen["body"]["model"] == "glm-local"
    assert seen["keyed"] is False, "no key is sent, so a plain-HTTP local server is allowed"
    assert out["usd"] == 0.0 and out["text"] == "findings: []"


def test_a_committed_night_commits_only_its_findings_file(sandbox, monkeypatch):
    monkeypatch.setattr(cma, "rotation", lambda caps, night, agents=None: {a: "audits" for a in cma.AGENTS})
    monkeypatch.setattr(cma, "call_model", lambda *a, **k: {"model": "m", "usd": 0.01, "text": "findings: []"})
    committed = []
    monkeypatch.setattr(cma, "_commit", lambda paths, message: committed.append((list(paths), message)) or "ok")
    out = cma.run_nightly("winston", date(2026, 9, 28), commit=True)
    assert committed == [([out], committed[0][1])] and "winston" in committed[0][1]


def test_a_rehearsal_can_read_a_smaller_slice(sandbox, monkeypatch):
    monkeypatch.setattr(cma, "rotation", lambda caps, night, agents=None: {a: "audits" for a in cma.AGENTS})
    real, asked = cma.bundle, []
    monkeypatch.setattr(cma, "bundle", lambda cap, max_chars, pins=None: asked.append(max_chars) or real(cap, max_chars, pins))
    cma.run_nightly("miles", date(2026, 9, 28), dry_run=True, max_chars=20_000)
    assert asked == [20_000]


def test_a_reasoning_model_gets_room_to_answer_after_it_thinks(monkeypatch):
    """GLM on 2026-09-28 spent all 8000 output tokens reasoning and returned no
    answer text: the answer budget must come on top of a reasoning budget."""
    _models(monkeypatch, {"glm": {"transport": "openrouter", "model": "z-ai/glm-test"}})
    monkeypatch.setattr(cma, "_secret", lambda name: "k")
    seen = {}

    def fake_post(url, body, headers, timeout):
        seen.update(body=body)
        return {"choices": [{"message": {"content": "findings: []"}}], "usage": {"cost": 0.01}}
    monkeypatch.setattr(cma, "_post_json", fake_post)
    cma.call_model("glm", "x", max_usd=0.4)
    f = cma.FAMILIES["glm"]
    # A token cap on reasoning was not honoured (GLM thought through all 24000);
    # a low effort left 12000 tokens of which the answer took 1300.
    assert seen["body"]["reasoning"] == {"effort": "low"}
    assert seen["body"]["max_tokens"] == f["max_output_tokens"] + f["max_reasoning_tokens"]
    assert cma.estimate_usd("glm", "") == pytest.approx(
        (f["max_output_tokens"] + f["max_reasoning_tokens"]) * f["out"] / 1_000_000)


def test_an_empty_answer_says_why_and_still_counts_its_spend(monkeypatch):
    _models(monkeypatch, {"gpt": {"transport": "openai", "model": "gpt-test"}})
    monkeypatch.setattr(cma, "_secret", lambda name: "k")
    monkeypatch.setattr(cma, "_post_json", lambda *a, **k: {
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 100}})
    with pytest.raises(cma._Spent, match="length") as exc:
        cma.call_model("gpt", "x", max_usd=0.4)
    assert exc.value.usd > 0


def test_an_answer_that_is_almost_yaml_still_yields_its_findings():
    """Claude's rehearsal answer (2026-09-28) put `: ` inside an unquoted claim,
    which strict YAML refuses; the night lost every finding for one colon."""
    text = """Here you go:
```yaml
findings:
  - promise: OPS-3
    claim: the task is closed with closed_reason "check passed: pull request ready", job box-x not rerun
    evidence: .datacore/lib/promise_evals.py:10
    severity: high
    seeded_failure: 'a job that fails: after the check'
  - promise: OPS-4
    claim: plain one
    evidence: .datacore/lib/promise_evals.py:12
    severity: low
    seeded_failure: none
```"""
    got = cma.parse_findings(text)
    assert [f["promise"] for f in got] == ["OPS-3", "OPS-4"]
    assert got[0]["claim"].endswith('"check passed: pull request ready", job box-x not rerun')
    assert got[0]["seeded_failure"] == "a job that fails: after the check"
    assert got[1]["evidence"] == ".datacore/lib/promise_evals.py:12"
    with pytest.raises(ValueError):
        cma.parse_findings("no findings here")


def test_a_slice_is_pinned_to_a_commit_others_can_fetch(tmp_path):
    """Rehearsal 2026-09-28: the box read a space whose HEAD had never been pushed
    (a diverged local history), and the findings file was unverifiable anywhere
    else. A repository is pinned at HEAD only when a remote branch holds it;
    otherwise at its upstream."""
    def git(repo, *a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()
    bare, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t")):
        git(work, "config", k, v)
    (work / "f.txt").write_text("1\n")
    git(work, "add", "f.txt")
    git(work, "commit", "-q", "-m", "published")
    git(work, "push", "-q", "origin", "HEAD:main")
    git(work, "branch", "-q", "--set-upstream-to=origin/main")
    published = git(work, "rev-parse", "HEAD")
    assert cma.published_head(work) == published
    (work / "f.txt").write_text("2\n")
    git(work, "commit", "-q", "-am", "local only")
    assert git(work, "rev-parse", "HEAD") != published
    assert cma.published_head(work) == published, "an unpushed HEAD was pinned"



# ── a subscription reached through OpenClaw (Data's ChatGPT login) ──────────
def _openclaw_answer(text="findings: []", ok=True):
    doc = {"ok": ok, "capability": "model.run", "provider": "openai", "model": "gpt-test",
           "outputs": [{"text": text, "mediaUrl": None}]}
    if not ok:
        doc = {"ok": False, "error": {"type": "provider_error", "message": "login expired"}}
    return json.dumps(doc, indent=2)


def test_the_openclaw_subscription_runs_one_toolless_turn_locally(monkeypatch):
    _models(monkeypatch, {"gpt": {"transport": "openclaw-cli", "model": "openai/gpt-test"}})
    monkeypatch.setattr(cma, "_secret", lambda name: pytest.fail("a subscription needs no API key"))
    seen = {}

    def fake_run(argv, **k):
        seen.update(argv=argv, input=k.get("input"))
        return subprocess.CompletedProcess(argv, 0, _openclaw_answer(), "")
    monkeypatch.setattr(cma.subprocess, "run", fake_run)
    out = cma.call_model("gpt", "PROMPT", max_usd=0.5)
    assert seen["argv"][:6] == ["openclaw", "infer", "model", "run", "--local", "--json"]
    assert seen["argv"][-2:] == ["--prompt", "PROMPT"] and "--model" in seen["argv"]
    assert out == {"text": "findings: []", "usd": 0.0, "model": "openai/gpt-test"}


def test_the_openclaw_subscription_on_another_host_gets_the_prompt_on_stdin(monkeypatch):
    _models(monkeypatch, {"gpt": {"transport": "openclaw-cli", "model": "openai/gpt-test", "host": "claw-audit"}})
    seen = {}

    def fake_run(argv, **k):
        seen.update(argv=argv, input=k.get("input"))
        return subprocess.CompletedProcess(argv, 0, "banner\n" + _openclaw_answer("findings: []"), "")
    monkeypatch.setattr(cma.subprocess, "run", fake_run)
    cma.call_model("gpt", "PROMPT", max_usd=0.5)
    assert seen["argv"][0] == "ssh" and "BatchMode=yes" in seen["argv"]
    assert seen["argv"][-2:] == ["claw-audit", "audit-infer openai/gpt-test"]
    assert seen["input"] == "PROMPT", "the prompt travels on stdin, never in a command line"


def test_the_openclaw_route_says_why_it_failed(monkeypatch):
    _models(monkeypatch, {"gpt": {"transport": "openclaw-cli", "model": "openai/gpt-test"}})
    monkeypatch.setattr(cma.subprocess, "run",
                        lambda argv, **k: subprocess.CompletedProcess(argv, 1, _openclaw_answer(ok=False), ""))
    with pytest.raises(RuntimeError, match="login expired"):
        cma.call_model("gpt", "x", max_usd=0.5)


def test_the_openclaw_prompt_fits_one_command_line_argument(monkeypatch):
    """openclaw takes the prompt only as --prompt, and Linux caps one argument at
    128 KiB: the slice is read smaller, and an oversized prompt is refused."""
    _models(monkeypatch, {"gpt": {"transport": "openclaw-cli", "model": "openai/gpt-test"}})
    f = cma.family_config("gpt")
    assert f["max_input_chars"] + 20_000 <= cma.OPENCLAW_PROMPT_BYTES <= 128 * 1024
    monkeypatch.setattr(cma.subprocess, "run", lambda *a, **k: pytest.fail("an oversized prompt was sent"))
    with pytest.raises(RuntimeError, match="too large"):
        cma.call_model("gpt", "x" * (cma.OPENCLAW_PROMPT_BYTES + 1), max_usd=0.5)


def test_the_forced_command_runs_only_one_inference(tmp_path):
    script = Path(cma.LIB) / "audit_infer_forced.sh"
    fake = tmp_path / "openclaw"
    fake.write_text('#!/bin/sh\nprintf "%s|" "$@"\n')
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)}
    ok = subprocess.run(["sh", str(script)], input="THE PROMPT", capture_output=True, text=True,
                        env={**env, "SSH_ORIGINAL_COMMAND": "audit-infer openai/gpt-test"})
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout == "infer|model|run|--local|--json|--model|openai/gpt-test|--prompt|THE PROMPT|"
    for cmd in ("rm -rf /", "audit-infer openai/gpt;id", "audit-infer a b", ""):
        r = subprocess.run(["sh", str(script)], input="x", capture_output=True, text=True,
                           env={**env, "SSH_ORIGINAL_COMMAND": cmd})
        assert r.returncode != 0 and "infer|" not in r.stdout, cmd


# ── the morning check judges what actually ran ──────────────────────────────
def _four(d, *, nested=None, skip=()):
    sha = head_sha()
    for agent in cma.AGENTS:
        if agent in skip:
            continue
        p = findings_file(d / f"{agent}.yaml", agent=agent, capability=f"cap-{agent}", commit=sha,
                          findings=[finding()])
        if nested:
            doc = yaml.safe_load(p.read_text())
            doc["commits"] = nested
            p.write_text(yaml.safe_dump(doc, sort_keys=False))
    return d


def test_a_repository_the_judge_does_not_have_is_noted_not_alerted(tmp_path, capsys):
    """2026-09-28: the box (the judge) has no copy of a module Miles's slice read;
    his file validates on the Mac. That is 'cannot check here', not a missed or
    invalid audit, and must not reach The Firm as one."""
    d = _four(tmp_path / "2026-09-28", nested={".datacore/modules/not-on-this-host": "a" * 40})
    assert cma.validate_findings(d / "miles.yaml", repo=ROOT), "the strict validator (AUD-7) stays strict"
    notes = []
    assert cma.night_alerts(d, unverifiable=notes) == []
    assert any("not-on-this-host" in n for n in notes)


def test_a_repository_the_judge_has_but_without_the_commit_is_still_alerted(tmp_path):
    d = _four(tmp_path / "2026-09-28", nested={".datacore/lib": "0" * 40})
    alerts = cma.night_alerts(d, unverifiable=[])
    assert len(alerts) == 4 and all("does not exist" in a for a in alerts)


def test_a_missing_file_points_at_the_job_that_should_have_written_it(tmp_path):
    d = _four(tmp_path / "2026-09-28", skip=("data",))
    (alert,) = cma.night_alerts(d)
    assert "data" in alert and "cadence" not in alert and "audit-nightly.log" in alert


def test_nothing_is_written_without_a_declared_system_space(sandbox, monkeypatch):
    """The fallback space is the personal one; on the box a ledger repair is
    pending there. Publishing refuses rather than write into it."""
    monkeypatch.setattr(cma, "SYSTEM_DECLARED", False)
    monkeypatch.setattr(cma, "_git", lambda *a, **k: pytest.fail("pulled into an undeclared space"))
    monkeypatch.setattr(cma, "publish", lambda *a, **k: pytest.fail("published into an undeclared space"))
    with pytest.raises(SystemExit, match="install.yaml"):
        cma.check(date(2026, 9, 28), write=True, send=False)
    with pytest.raises(SystemExit, match="install.yaml"):
        cma.run_nightly("miles", date(2026, 9, 28), commit=True)


# ── the writer validates its pins; the morning check needs no repository ────
# Owner, 2026-09-28: "why does the box need nightshift repo access, it is not
# running it?" The host that writes a findings file has the code it read, so it
# validates the pins then and records that in the file; the box only checks the
# file arrived, is well-formed and carries a successful write-time validation.
def _no_git(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError(f"the morning check touched a repository: {a}")
    monkeypatch.setattr(cma, "_git", refuse)
    cma._commit_exists.cache_clear()
    cma._blob.cache_clear()


def _stamped_four(d, **kw):
    _four(d, **kw)
    for p in sorted(d.glob("*.yaml")):
        assert cma.stamp_validation(p, repo=ROOT) == []
    return d


def test_the_writing_host_validates_its_pins_and_records_it(sandbox, monkeypatch):
    monkeypatch.setattr(cma, "rotation", lambda caps, night, agents=None: {a: "audits" for a in cma.AGENTS})
    monkeypatch.setattr(cma, "call_model", lambda *a, **k: {"model": "m", "usd": 0.01, "text": _answer(
        finding("AUD-3", ".datacore/lib/promise_evals.py:10"))})
    committed = []
    monkeypatch.setattr(cma, "_commit", lambda paths, message: committed.append(message) or "ok")
    out = cma.run_nightly("tris", date(2026, 9, 28), commit=True)
    v = yaml.safe_load(out.read_text())["validation"]
    assert v["ok"] is True and v["host"] and v["at"] and len(v["digest"]) == 64
    assert v["repos"]["."] == cma.published_head(ROOT), "the record names what was checked, at which commit"
    assert "validated at write time" in committed[0]
    assert cma.validate_findings(out, repo=ROOT) == [], "the strict validator (AUD-7) still accepts it"
    assert cma.write_time_errors(out) == []


def test_the_morning_check_reads_no_repository(tmp_path, monkeypatch):
    d = _stamped_four(tmp_path / "2026-09-28")
    _no_git(monkeypatch)
    assert cma.night_alerts(d, write_time=True) == []
    agg = cma.aggregate(d, write_time=True)
    assert agg["invalid"] == {} and len(agg["unconfirmed"]) + len(agg["confirmed"]) >= 1


def test_a_pin_to_a_module_the_judge_lacks_is_no_concern_of_the_judge(tmp_path, monkeypatch):
    """Miles's slice read the nightshift module; the box does not have it and
    does not need it: nightshift validated the pin when it wrote the file."""
    d = tmp_path / "2026-09-28"
    _four(d)
    p = d / "miles.yaml"
    doc = yaml.safe_load(p.read_text())
    doc["commits"] = {".datacore/modules/nightshift": "a" * 40}
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    for q in d.glob("*.yaml"):
        cma._stamp(q, {"ok": True, "host": "writer", "at": "2026-09-29T01:30:00+00:00", "repos": {}})
    _no_git(monkeypatch)
    assert cma.night_alerts(d, write_time=True) == []


@pytest.mark.parametrize("breakage", ["unstamped", "edited", "not-ok", "malformed"])
def test_the_morning_check_still_alerts_on_what_it_can_see(tmp_path, monkeypatch, breakage):
    d = _stamped_four(tmp_path / "2026-09-28")
    p = d / "data.yaml"
    doc = yaml.safe_load(p.read_text())
    if breakage == "unstamped":
        doc.pop("validation")
    elif breakage == "edited":
        doc["findings"][0]["claim"] = "changed after it was validated"
    elif breakage == "not-ok":
        doc["validation"]["ok"] = False
    elif breakage == "malformed":
        doc["commit"] = "main"
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    _no_git(monkeypatch)
    alerts = cma.night_alerts(d, write_time=True)
    assert len(alerts) == 1 and "data" in alerts[0], alerts
    assert "data" in cma.aggregate(d, write_time=True)["invalid"]


def test_the_box_check_judges_by_the_write_time_record(sandbox, monkeypatch, capsys):
    d = _stamped_four(cma.NIGHTLY / "2026-09-28")
    assert d.is_dir()
    _no_git(monkeypatch)
    assert cma.check(date(2026, 9, 28), write=False, send=False) == 0
    assert "0 alert(s)" in capsys.readouterr().out


def test_a_calibration_run_is_validated_where_it_is_written(sandbox, monkeypatch):
    monkeypatch.setattr(cma, "CALIBRATION", sandbox / "calibration")
    monkeypatch.setattr(cma, "fixture", lambda: (".", head_sha(), ".datacore/lib", ["promise_evals.py"]))
    monkeypatch.setattr(cma, "call_model", lambda *a, **k: {"model": "m", "usd": 0.0, "text": _answer(
        finding("G1", "promise_evals.py:10"))})
    out = cma.run_calibration("data")
    doc = yaml.safe_load(out.read_text())
    assert doc["validation"]["ok"] is True and cma.write_time_errors(out) == []


def test_an_audit_only_checkout_syncs_itself(sandbox, monkeypatch, tmp_path):
    """On hermes and plur-claw the audit reads from its own checkout, which no
    space sync maintains: it pulls (merge, never rebase) before reading, and a
    push refused because another host pushed first is merged and retried."""
    calls = []

    class R:
        def __init__(self, rc=0, out=""):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def fake_git(repo, *args):
        calls.append((Path(repo), args))
        return R()
    monkeypatch.setattr(cma, "_git", fake_git)
    cma.sync_sources()
    pulls = [(r, a) for r, a in calls if a[0] == "pull"]
    assert any(r == cma.SPACE and "--no-rebase" in a for r, a in pulls)
    assert all("--rebase" not in a for _, a in pulls)
    calls.clear()
    assert cma._push_after_merge("push failed (rejected); committed locally") == "pushed after merging"
    assert [a[0] for _, a in calls] == ["pull", "push"] and "--no-rebase" in calls[0][1]
    calls.clear()
    assert cma._push_after_merge("pushed") == "pushed" and calls == []
