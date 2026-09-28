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

    def fake_post(url, body, headers, timeout):
        seen.update(url=url, body=body, headers=headers)
        return {"model": "glm-local", "choices": [{"message": {"content": "findings: []"}}], "usage": {}}
    monkeypatch.setattr(cma, "_post_json", fake_post)
    monkeypatch.setattr(cma, "_secret", lambda name: pytest.fail("a local model needs no key"))
    out = cma.call_model("glm", "x", max_usd=0.1)
    assert seen["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert "Authorization" not in seen["headers"] and seen["body"]["model"] == "glm-local"
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
    assert seen["body"]["reasoning"] == {"max_tokens": f["max_reasoning_tokens"]}
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
