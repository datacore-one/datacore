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


#: The install every test here runs in, built per test (no install-local file is
#: read: CI has no principals.yaml, no install.yaml and no system space). The
#: roster is the contract's four agents on four families; model ids are test
#: ids, never real ones.
ROSTER = {
    "agents": {"miles": "claude", "winston": "deepseek", "tris": "glm", "data": "gpt"},
    "nightly_cap_usd": {"miles": 2.0, "winston": 0.5, "tris": 0.5, "data": 1.0},
    "models": {"claude": {"transport": "claude-cli", "model": ""},
               "deepseek": {"transport": "openrouter", "model": "test/deepseek"},
               "glm": {"transport": "openrouter", "model": "test/glm"},
               "gpt": {"transport": "openclaw-cli", "model": "openai/gpt-test"}},
}
PROMISE_LIST = {"capabilities": [
    {"key": "audits", "promises": [{"id": "AUD-3", "promise": "two families confirm a finding", "today": "red"}]},
    {"key": "tasks", "promises": [{"id": "TSK-2", "promise": "a task has one id", "today": "green"}]},
]}


@pytest.fixture(autouse=True)
def _temp_install(tmp_path, monkeypatch):
    import roster
    monkeypatch.setattr(roster, "section", lambda key, path=None: ROSTER if key == "cross_model_audit" else {})
    monkeypatch.setattr(cma, "AGENTS", dict(ROSTER["agents"]))
    monkeypatch.setattr(cma, "NIGHTLY_CAP_USD", dict(ROSTER["nightly_cap_usd"]))
    monkeypatch.setattr(cma, "SYSTEM_DECLARED", True)
    promises = tmp_path / "install" / "promises"
    promises.mkdir(parents=True)
    (promises / "part1.yaml").write_text(yaml.safe_dump(PROMISE_LIST, sort_keys=False))
    monkeypatch.setattr(cma, "PROMISES", promises)


#: The real function, for the test that exercises it on a repository it builds.
_PUBLISHED_HEAD = cma.published_head


@pytest.fixture(autouse=True)
def _pinned_to_local_head(monkeypatch):
    """Slices here are pinned to the checkout's own HEAD, which always exists
    locally. The real pin (published_head) is the upstream commit whenever HEAD
    is a local commit not yet pushed -- true on this machine whenever any session
    has committed and not pushed, which made these tests depend on the live
    branch state (2026-09-28). test_a_slice_is_pinned_to_a_commit_others_can_fetch
    checks the real rule on a temporary repository of its own."""
    monkeypatch.setattr(cma, "published_head", cma.head)


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
    path = cma.CALIBRATION / "keys" / "ledgerlite.yaml"
    if not path.is_file():
        pytest.skip("the answer key is the install's system-space data; this checkout has none")
    key = yaml.safe_load(path.read_text())
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
    # `assistant` is declared in the committed policy, so this holds on any
    # install, not only one whose private policy names its agents.
    assert "write" in tool_policy.decide("assistant", *edit).effects
    assert tool_policy.decide("assistant", *edit).allow


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
    assert _PUBLISHED_HEAD(work) == published
    (work / "f.txt").write_text("2\n")
    git(work, "commit", "-q", "-am", "local only")
    assert git(work, "rev-parse", "HEAD") != published
    assert _PUBLISHED_HEAD(work) == published, "an unpushed HEAD was pinned"



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
    assert v["repos"]["."] == head_sha(), "the record names what was checked, at which commit"
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


def test_the_key_is_asked_of_the_hosts_broker_not_the_audit_checkout(monkeypatch):
    """On hermes the audit runs with DATACORE_ROOT at its own checkout, which has
    no credential index; the broker's declared location is the host's data root."""
    seen = {}

    class R:
        returncode, stdout, stderr = 0, "k" * 20 + "\n", ""

    def fake_run(cmd, **kw):
        seen["env"] = kw.get("env")
        return R()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("DATACORE_ROOT", "/somewhere/audit-src/datacore")
    monkeypatch.setattr(cma.subprocess, "run", fake_run)
    assert cma._secret("OPENROUTER_API_KEY") == "k" * 20
    assert seen["env"] is not None and "DATACORE_ROOT" not in seen["env"]


def test_an_audit_only_checkout_pushes_under_its_own_git_guards(sandbox, monkeypatch):
    """The shared git hooks judge a push with the guard scripts under DATA_DIR
    (default ~/Data). On plur-claw ~/Data is the agent's space repo, whose
    vendored lib lacks the ledger write gate, so the hook failed closed
    (2026-09-28). An audit-only checkout is a complete, current tree: its own
    guards judge its push. The hooks still run; nothing is skipped."""
    monkeypatch.setattr(cma, "rotation", lambda caps, night, agents=None: {a: "audits" for a in cma.AGENTS})
    monkeypatch.setattr(cma, "call_model", lambda *a, **k: {"model": "m", "usd": 0.0, "text": "findings: []"})
    monkeypatch.setattr(cma, "sync_sources", lambda: None)
    monkeypatch.delenv("DATA_DIR", raising=False)
    seen = []
    monkeypatch.setattr(cma, "_commit", lambda paths, message: seen.append(os.environ.get("DATA_DIR")) or "pushed")
    cma.run_nightly("data", date(2026, 9, 28), commit=True, sync=True)
    assert seen == [str(cma.ROOT)]
    assert "SKIP_PRE_PUSH" not in os.environ


def test_an_openrouter_family_can_think_through_a_large_slice_and_still_answer():
    """Night of 2026-09-29: DeepSeek (249k chars) and GLM (292k chars) both
    thought through the whole 24000-token ceiling despite low effort and gave
    no answer. The ceiling is 64000 (billing is per token produced), and a
    worst-case run must still start under the smallest nightly cap ($0.50)
    with a full-size slice."""
    for fam in ("deepseek", "glm"):
        f = cma.FAMILIES[fam]
        assert f["max_output_tokens"] + f["max_reasoning_tokens"] >= 64_000, fam
        full_slice = "x" * f["max_input_chars"]
        assert cma.estimate_usd(fam, full_slice) < 0.50, fam


# ── AUD-1 (2026-09-29): one audit night a week per agent; findings become issues ─
WEEK = {"miles": 1, "winston": 2, "tris": 3, "data": 4}     # cron weekday, 0 = Sunday


def _job(name, agent, schedule, sub="nightly"):
    return {"name": name, "machine": "h", "schedule": schedule,
            "cmd": f"DATACORE_ROOT=~/Data python3 ~/Data/.datacore/lib/cross_model_audit.py {sub} "
                   f"--agent {agent} --commit >> ~/.datacore/state/audit-nightly.log 2>&1"}


def test_each_agents_audit_night_is_read_from_the_job_list():
    doc = {"jobs": [_job("a", "miles", "20 1 * * 1"), _job("b", "winston", "25 1 * * tue"),
                    _job("c", "tris", "30 1 * * *"), _job("d", "data", "35 2 * * 0", sub="calibrate"),
                    {"name": "e", "machine": "h", "schedule": "40 6 * * *",
                     "cmd": "python3 cross_model_audit.py check --send"}]}
    assert cma.audit_nights(doc) == {"miles": 1, "winston": 2, "tris": None}, (
        "a weekly job gives its weekday, a nightly one None; calibration and the check are not audit nights")


def test_the_morning_check_expects_only_the_agents_whose_night_it_is(tmp_path, monkeypatch):
    monkeypatch.setattr(cma, "audit_nights", lambda doc=None: dict(WEEK))
    monday = tmp_path / "2026-09-28"          # a Monday: miles's night
    monday.mkdir()
    assert cma.expected_agents(date(2026, 9, 28)) == ["miles"]
    alerts = cma.night_alerts(monday, agents=cma.expected_agents(date(2026, 9, 28)))
    assert len(alerts) == 1 and "miles" in alerts[0], alerts
    assert cma.expected_agents(date(2026, 10, 2)) == [], "Friday is a quiet night: nobody is expected"


def test_a_quiet_night_is_not_an_alert(sandbox, monkeypatch, capsys):
    monkeypatch.setattr(cma, "audit_nights", lambda doc=None: dict(WEEK))
    _no_git(monkeypatch)
    sent = []
    monkeypatch.setattr(cma, "send_to_firm", lambda text: sent.append(text) or True)
    assert cma.check(date(2026, 10, 2), write=False, send=True) == 0      # a Friday, no folder at all
    assert sent == [] and "0 alert(s)" in capsys.readouterr().out
    assert cma.check(date(2026, 9, 29), write=False, send=True) == 1      # Tuesday: winston's night, missed
    assert len(sent) == 1 and "winston" in sent[0] and "miles" not in sent[0]


def test_an_agent_without_any_weekly_job_is_expected_every_night(monkeypatch):
    monkeypatch.setattr(cma, "audit_nights", lambda doc=None: {})
    assert cma.expected_agents(date(2026, 10, 2)) == sorted(cma.AGENTS), (
        "an install that declares no per-agent audit job keeps the every-night expectation")


def _gh_login(monkeypatch, login):
    monkeypatch.setattr(cma, "_gh_login", lambda: login)
    import roster
    monkeypatch.setattr(roster, "entries", lambda path=None: {
        "miles": {"role": "chief of operations", "github": "miles-account"},
        "tris": {"role": "research", "github": "tris-account"}})


def test_a_night_files_its_findings_as_issues_after_write_time_validation(sandbox, monkeypatch):
    monkeypatch.setattr(cma, "rotation", lambda caps, night, agents=None: {a: "audits" for a in cma.AGENTS})
    monkeypatch.setattr(cma, "call_model", lambda *a, **k: {"model": "m", "usd": 0.01, "text": _answer(
        finding("AUD-3", ".datacore/lib/promise_evals.py:10"))})
    _gh_login(monkeypatch, "tris-account")
    filed, committed = [], []

    def fake_file(path, *, repo):
        assert cma.write_time_errors(path) == [], "issues are filed only from a validated findings file"
        filed.append((Path(path), repo))
        rec = Path(path).with_name("tris.issues.yaml")
        rec.write_text("issues: []\n")
        return []
    monkeypatch.setattr(cma, "file_issues", fake_file)
    monkeypatch.setattr(cma, "issues_repo", lambda: "example-org/datacore")
    monkeypatch.setattr(cma, "_commit", lambda paths, message: committed.append(list(paths)) or "ok")
    out = cma.run_nightly("tris", date(2026, 9, 30), commit=True, issues=True)
    assert filed == [(out, "example-org/datacore")]
    assert committed == [[out], [out.with_name("tris.issues.yaml")]], (
        "the findings file is committed first, on its own; the issues record after it")


def test_issues_are_filed_only_under_the_agents_own_account(sandbox, monkeypatch):
    monkeypatch.setattr(cma, "rotation", lambda caps, night, agents=None: {a: "audits" for a in cma.AGENTS})
    monkeypatch.setattr(cma, "call_model", lambda *a, **k: {"model": "m", "usd": 0.01, "text": "findings: []"})
    _gh_login(monkeypatch, "the-owners-account")
    monkeypatch.setattr(cma, "file_issues", lambda *a, **k: pytest.fail("filed under another account"))
    with pytest.raises(SystemExit, match="tris-account"):
        cma.run_nightly("tris", date(2026, 9, 30), issues=True)
    assert (cma.NIGHTLY / "2026-09-30" / "tris.yaml").is_file(), "the findings stay; only the filing is refused"
    _gh_login(monkeypatch, None)
    with pytest.raises(SystemExit, match="not logged in|could not tell"):
        cma.run_nightly("tris", date(2026, 9, 30), issues=True)


def test_without_issues_a_night_never_calls_github(sandbox, monkeypatch):
    monkeypatch.setattr(cma, "rotation", lambda caps, night, agents=None: {a: "audits" for a in cma.AGENTS})
    monkeypatch.setattr(cma, "call_model", lambda *a, **k: {"model": "m", "usd": 0.01, "text": "findings: []"})
    monkeypatch.setattr(cma, "_gh", lambda *a, **k: pytest.fail("a night without --issues called gh"))
    cma.run_nightly("tris", date(2026, 9, 30))


def test_a_finding_in_a_nested_repository_is_filed_in_that_repository(tmp_path, monkeypatch):
    import roster
    monkeypatch.setattr(roster, "by_role", lambda role, path=None: "miles")
    monkeypatch.setattr(roster, "entries", lambda path=None: {"miles": {"github": "miles-account"}})
    sha = head_sha()
    p = findings_file(tmp_path / "2026-09-30" / "tris.yaml", agent="tris", capability="c", commit=sha,
                      findings=[finding("AUD-3", ".datacore/modules/dev/lab.py:3"),
                                finding("TSK-2", ".datacore/lib/promise_evals.py:10")])
    doc = yaml.safe_load(p.read_text())
    doc["commits"] = {".datacore/modules/dev": "d" * 40}
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    monkeypatch.setattr(cma, "_remote_slug", lambda repo: "example-org/dev-module"
                        if Path(repo).name == "dev" else "example-org/datacore")
    calls = []

    def fake_gh(args, input=None):
        calls.append(args)
        out = "[]"
        if args[:2] == ["issue", "create"]:
            repo = args[args.index("-R") + 1]
            out = f"https://github.com/{repo}/issues/{len(calls)}\n"
        if args[:2] == ["issue", "view"]:
            out = json.dumps({"labels": [{"name": "audit-finding"}], "assignees": [{"login": "miles-account"}]})
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(cma, "_gh", fake_gh)
    got = cma.file_issues(p, repo="example-org/datacore")
    assert [e["url"].split("/issues/")[0] for e in got] == ["https://github.com/example-org/dev-module",
                                                            "https://github.com/example-org/datacore"]
    creates = [c for c in calls if c[:2] == ["issue", "create"]]
    assert all("miles-account" in c for c in creates) and all("audit-finding" in c for c in creates)
    assert "d" * 40 in creates[0][creates[0].index("--body") + 1], "the nested repository's own pin is named"


def test_an_issue_github_stripped_of_its_label_or_assignee_is_reported_not_trusted(tmp_path, monkeypatch):
    """An account without triage rights may open an issue, but GitHub silently
    drops its labels and assignees: the issue exists and nobody's queue has it."""
    import roster
    monkeypatch.setattr(roster, "by_role", lambda role, path=None: "miles")
    monkeypatch.setattr(roster, "entries", lambda path=None: {"miles": {"github": "miles-account"}})
    p = findings_file(tmp_path / "2026-09-30" / "data.yaml", agent="data", capability="c", commit=head_sha(),
                      findings=[finding("TSK-2", ".datacore/lib/promise_evals.py:10")])

    def stripping_gh(args, input=None):
        if args[:2] == ["issue", "create"]:
            return subprocess.CompletedProcess(args, 0, "https://github.com/example-org/datacore/issues/9\n", "")
        if args[:2] == ["issue", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps({"labels": [], "assignees": []}), "")
        return subprocess.CompletedProcess(args, 0, "[]", "")
    monkeypatch.setattr(cma, "_gh", stripping_gh)
    with pytest.raises(RuntimeError, match="label|assignee"):
        cma.file_issues(p, repo="example-org/datacore")
    rec = yaml.safe_load(p.with_name("data.issues.yaml").read_text())
    assert rec["issues"][0]["url"].endswith("/issues/9") and rec["errors"], rec


def test_the_issues_record_beside_a_findings_file_is_itself_pinned_and_checkable(tmp_path, monkeypatch):
    """AUD-7 checks every YAML in a night's folder; the issues record (AUD-1) sits
    there too, so it must carry the same pin and the findings it tracks, and name
    the GitHub repository under a key that is not the findings schema's `repo`
    (a relative base path). 2026-09-29: `repo: datacore-one/datacore` moved the
    base and the record failed as 'no capability / commit does not exist'."""
    import roster
    monkeypatch.setattr(roster, "by_role", lambda role, path=None: "miles")
    monkeypatch.setattr(roster, "entries", lambda path=None: {"miles": {"github": "miles-account"}})
    p = findings_file(tmp_path / "2026-09-30" / "miles.yaml", agent="miles", capability="c", commit=head_sha(),
                      findings=[finding("TSK-2", ".datacore/lib/promise_evals.py:10")])

    def fake_gh(args, input=None):
        if args[:2] == ["issue", "create"]:
            return subprocess.CompletedProcess(args, 0, "https://github.com/example-org/datacore/issues/7\n", "")
        if args[:2] == ["issue", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(
                {"labels": [{"name": "audit-finding"}], "assignees": [{"login": "miles-account"}]}), "")
        return subprocess.CompletedProcess(args, 0, "[]", "")
    monkeypatch.setattr(cma, "_gh", fake_gh)
    cma.file_issues(p, repo="example-org/datacore")
    record = p.with_name("miles.issues.yaml")
    assert cma.validate_findings(record, repo=ROOT) == [], "the issues record is not pinned and checkable"
    rec = yaml.safe_load(record.read_text())
    assert rec["issues_repo"] == "example-org/datacore" and len(rec["issues"]) == 1
    empty = findings_file(tmp_path / "2026-10-01" / "miles.yaml", agent="miles", capability="c",
                          commit=head_sha(), findings=[])
    cma.file_issues(empty, repo="example-org/datacore")
    assert cma.validate_findings(empty.with_name("miles.issues.yaml"), repo=ROOT) == []
