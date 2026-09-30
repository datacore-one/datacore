"""The in-flight tool-call policy (datacore#30): classification by
tool_effects.yaml, the decision per principal, the refusal on the ledger,
and the hook protocol. Fixture policy and ledger; never the real ones."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import tool_policy as tp  # noqa: E402

EFFECTS = tp.load_effects()   # the shipped vocabulary, config/tool_effects.yaml


@pytest.fixture
def policy_file(tmp_path):
    p = tmp_path / "approvals_policy.yaml"
    p.write_text(yaml.safe_dump({
        "version": 1, "approver": "human",
        "cosign_effects": ["email.send", "payment", "prod.deploy"],
        "known_effects": ["email.send", "payment", "prod.deploy"],
        "principals": {
            "gregor": {},
            "miles": {"never_effects": ["payment"], "may_delegate_to": ["tris", "data"]},
            "tris": {"never_effects": ["payment", "prod.deploy"]},
        },
    }))
    return p


# ── classification ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool,inp,expected", [
    # `write` (AUD-2, f104619) is any change: every shell command that is not a
    # chain of read-only commands, every file tool outside an audit's findings
    # file, every MCP verb of change. It binds only the auditor principal.
    ("Bash", {"command": "curl -X POST https://api.stripe.com/v1/charges -d amount=100"}, {"payment", "write"}),
    # Winston's Telegram sender is message.send, not e-mail (AGT-1, 5cf0026).
    ("Bash", {"command": "python3 ~/Data/.datacore/lib/winston_send.py 'hello'"}, {"message.send", "write"}),
    ("Bash", {"command": "sendmail x@example.org"}, {"email.send", "write"}),
    ("Bash", {"command": "gh release create v1.2.0 --notes x"}, {"prod.deploy", "write"}),
    ("Bash", {"command": "sudo systemctl restart datacored"}, {"prod.deploy", "write"}),
    ("Bash", {"command": "ls -la && git status"}, set()),
    ("Bash", {"command": "grep -rn 'stripe' docs/ | head"}, set()),
    ("WebFetch", {"url": "https://api.paypal.com/v2/checkout/orders"}, {"payment"}),
    ("mcp__gmail__send_email", {"to": "x@example.org", "body": "hi"}, {"email.send", "write"}),
    ("mcp__gateio__place_order", {"pair": "BTC_USDT"}, {"payment"}),
    # Reading cannot act; editing is a `write` and nothing more, whatever the text says.
    ("Read", {"file_path": "/x/deploy.sh"}, set()),
    ("Edit", {"file_path": "/x/pay.py", "new_string": "requests.post('https://api.stripe.com/v1/charges')"}, {"write"}),
])
def test_classify_by_the_shipped_vocabulary(tool, inp, expected):
    assert tp.classify(tool, inp, EFFECTS) == expected


def test_call_text_prefers_command_fields_then_json():
    # Decision S1 (2026-09-23): the text-key fields come first, and the JSON of
    # the whole input is always appended, so a field outside _TEXT_KEYS is matched.
    text = tp.call_text({"command": "ls", "description": "list"})
    first, _, rest = text.partition("\n")
    assert first == "ls"
    assert json.loads(rest) == {"command": "ls", "description": "list"}
    assert json.loads(tp.call_text({"a": 1, "b": [2]})) == {"a": 1, "b": [2]}
    assert tp.call_text("raw") == "raw"
    assert tp.call_text(None) == ""


# ── decision ────────────────────────────────────────────────────────────────

def test_never_effect_is_refused_whatever_the_grants(policy_file):
    d = tp.decide("miles", "Bash", {"command": "curl https://api.stripe.com/v1/charges"},
                  granted=["payment"], effects=EFFECTS, policy_path=policy_file)
    assert d.blocked and d.kind == "never" and "may never cause payment" in d.reason


def test_cosign_effect_without_grant_is_paused(policy_file):
    d = tp.decide("miles", "Bash", {"command": "sendmail x@example.org"},
                  effects=EFFECTS, policy_path=policy_file)
    assert d.blocked and d.kind == "cosign"
    assert "email.send needs a co-signed grant" in d.reason and "proposal" in d.reason


def test_cosign_effect_with_grant_is_allowed(policy_file):
    d = tp.decide("miles", "Bash", {"command": "sendmail x@example.org"},
                  granted=["email.send"], effects=EFFECTS, policy_path=policy_file)
    assert d.allow and d.kind == "granted"


def test_plain_calls_are_allowed(policy_file):
    d = tp.decide("tris", "Bash", {"command": "ls -la && git status"}, effects=EFFECTS, policy_path=policy_file)
    assert d.allow and d.kind == "allow" and d.effects == set()
    # A command that changes something is a `write` (AUD-2): not a never- or
    # cosign-effect for tris, so it runs.
    d = tp.decide("tris", "Bash", {"command": "pytest -q"}, effects=EFFECTS, policy_path=policy_file)
    assert d.allow and d.kind == "granted" and d.effects == {"write"}


def test_tris_may_never_deploy(policy_file):
    d = tp.decide("tris", "Bash", {"command": "npm publish"}, effects=EFFECTS, policy_path=policy_file)
    assert d.blocked and d.kind == "never" and "prod.deploy" in d.reason


def test_unlisted_principal_cannot_bypass_declared_limits(policy_file):
    with pytest.raises(ValueError, match="declared"):
        tp.limits_for("someone-new", policy_file)


# ── the record ──────────────────────────────────────────────────────────────

def _space(tmp_path):
    (tmp_path / "space" / ".datacore" / "events").mkdir(parents=True)
    return tmp_path / "space"


def test_refusal_is_recorded_on_the_task_space_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    space = _space(tmp_path)
    d = tp.Decision(False, {"payment"}, "miles may never cause payment", "never")
    assert tp.record_refusal(d, principal="miles", tool_name="Bash", space_dir=space,
                             task_id="org-1", actor="nightshift", detail="curl api.stripe.com")
    from ledger.log import read_events
    ev = read_events(space)
    assert len(ev) == 1 and ev[0].type == "metric.attest" and ev[0].actor == "nightshift"
    p = ev[0].payload
    assert p["metric"] == "policy.refusal" and p["principal"] == "miles"
    assert p["task"] == "org-1" and p["effects"] == ["payment"] and p["kind"] == "never"


def test_record_is_best_effort_without_a_ledger(tmp_path, capsys):
    d = tp.Decision(False, {"payment"}, "x", "never")
    assert tp.record_refusal(d, principal="miles", tool_name="Bash", space_dir=tmp_path / "nowhere",
                             actor="nightshift") is False
    assert "not recorded" in capsys.readouterr().err


# ── the hook ────────────────────────────────────────────────────────────────

def test_hook_denies_never_effect_and_records(tmp_path, monkeypatch, policy_file):
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    monkeypatch.setenv("DATACORE_ACTOR", "nightshift")
    space = _space(tmp_path)
    env = {"DATACORE_POLICY_PRINCIPAL": "miles", "DATACORE_POLICY_SPACE": str(space),
           "DATACORE_POLICY_TASK": "org-42"}
    out = tp.evaluate_hook({"tool_name": "Bash", "tool_input": {"command": "curl https://api.stripe.com/v1/charges"}},
                           env=env, effects=EFFECTS, policy_path=policy_file)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "may never cause payment" in out["hookSpecificOutput"]["permissionDecisionReason"]
    from ledger.log import read_events
    ev = read_events(space)
    assert ev[-1].payload["task"] == "org-42" and ev[-1].payload["tool"] == "Bash"


def test_hook_allows_plain_calls_and_granted_effects(tmp_path, policy_file):
    env = {"DATACORE_POLICY_PRINCIPAL": "miles", "DATACORE_POLICY_GRANTED": "email.send"}
    assert tp.evaluate_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}},
                            env=env, effects=EFFECTS, policy_path=policy_file) is None
    assert tp.evaluate_hook({"tool_name": "Bash", "tool_input": {"command": "sendmail x@example.org"}},
                            env=env, effects=EFFECTS, policy_path=policy_file) is None


def test_absent_policy_file_means_the_shipped_defaults(tmp_path):
    # ledger.policy falls back to its built-in defaults (the three cosign
    # effects, no principals) when the file is absent — so a deploy without
    # a grant is still paused, not waved through.
    env = {"DATACORE_POLICY_PRINCIPAL": "miles"}
    out = tp.evaluate_hook({"tool_name": "Bash", "tool_input": {"command": "npm publish"}},
                           env=env, effects=EFFECTS, policy_path=tmp_path / "missing.yaml", record=False)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_fails_closed_when_policy_is_unreadable(tmp_path, capsys):
    bad = tmp_path / "broken.yaml"
    bad.write_text("approver: [unclosed\ncosign_effects: {")
    env = {"DATACORE_POLICY_PRINCIPAL": "miles"}
    out = tp.evaluate_hook({"tool_name": "Bash", "tool_input": {"command": "npm publish"}},
                           env=env, effects=EFFECTS, policy_path=bad)
    assert out['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert "call refused" in capsys.readouterr().err


def test_hook_main_protocol(monkeypatch, capsys, tmp_path, policy_file):
    monkeypatch.setattr(tp, "DEFAULT_POLICY_FILE", policy_file)
    monkeypatch.setenv("DATACORE_POLICY_PRINCIPAL", "tris")
    monkeypatch.setenv("DATACORE_POLICY_SPACE", str(tmp_path / "nowhere"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "twine upload dist/*"}})))
    assert tp.hook_main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert tp.hook_main() == 0


def test_settings_json_wires_the_guard_on_every_tool():
    s = json.loads(tp.settings_json(Path("/opt/x/guard.py")))
    hook = s["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "*"
    assert hook["hooks"][0]["command"] == "python3 /opt/x/guard.py"
    assert tp.GUARD.name == "tool_policy_guard.py" and tp.GUARD.exists()


def test_principal_for_maps_a_writer_to_its_principal(tmp_path, monkeypatch):
    import actor_identity as ai
    reg = tmp_path / "principals.yaml"
    reg.write_text(yaml.safe_dump({"principals": {
        "miles": {"kind": "agent", "writes_as": ["miles", "nightshift"]}}}))
    monkeypatch.setattr(ai, "PRINCIPALS", reg)
    assert tp.principal_for("nightshift") == "miles"
    assert tp.principal_for("miles") == "miles"


def test_principal_for_refuses_an_undeclared_writer(tmp_path, monkeypatch):
    """An unknown writer must not become its own principal.

    This test used to assert `principal_for("stranger") == "stranger"`, and it
    was the last thing in the tree still asking for that. Returning the actor
    unchanged made the writer name its own policy principal, so a writer absent
    from principals.yaml got whatever `limits_for` returns for an unknown name
    -- no `never`, no `cosign` -- which is the widest grant in the system,
    handed out precisely to the identity nobody had declared.

    The suite this lives in is not run by CI, so the stale assertion sat green
    in nobody's run while the code was made to fail closed. Left there it reads
    as a regression and invites the refusal to be removed again.
    """
    import actor_identity as ai
    reg = tmp_path / "principals.yaml"
    reg.write_text(yaml.safe_dump({"principals": {
        "miles": {"kind": "agent", "writes_as": ["miles", "nightshift"]}}}))
    monkeypatch.setattr(ai, "PRINCIPALS", reg)
    with pytest.raises(ValueError, match="no declared principal"):
        tp.principal_for("stranger")


@pytest.mark.parametrize('payload', [None, [], 'text', {}, {'tool_name': None}])
def test_malformed_hook_requests_are_denied(payload):
    assert tp.evaluate_hook(payload, record=False)['hookSpecificOutput']['permissionDecision'] == 'deny'


def test_unclassified_call_still_requires_available_policy(tmp_path):
    result = tp.evaluate_hook({'tool_name': 'Read', 'tool_input': {'file_path': 'file'}},
        env={'DATACORE_POLICY_PRINCIPAL': 'missing'}, effects=EFFECTS,
        policy_path=tmp_path / 'missing.yaml', record=False)
    assert result['hookSpecificOutput']['permissionDecision'] == 'deny'


def test_refusal_does_not_copy_sensitive_tool_input(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_LEDGER_SIGN', '0')
    space = _space(tmp_path)
    decision = tp.Decision(False, {'payment'}, 'refused', 'never')
    assert tp.record_refusal(decision, principal='miles', tool_name='Bash',
        space_dir=space, actor='nightshift', detail='token=PRIVATE-TEST-TOKEN')
    # A refusal is a metric.attest: routine telemetry, in its own log (LED-8, e6c0928).
    logs = sorted((space / '.datacore').glob('*/*.jsonl'))
    assert (space / '.datacore/telemetry/nightshift.jsonl') in logs
    assert all('PRIVATE-TEST-TOKEN' not in f.read_text() for f in logs)


# ── uncommitted work belongs to other writers (incident 2026-09-30) ─────────
# 05:12 UTC on the box: the nightly inbox job's agent ran `git stash` in the
# personal space to get a pull through. The stash swallowed five uncommitted
# events of Winston's log, the ledger (correctly) refused the rewound log, and
# the hash-chain check went red. In a ledger space the working tree holds other
# writers' event logs; the ledger's own rule is "commit, never stash"
# (ledger_transport.py). So an unattended agent may not hide or discard
# uncommitted work in any form -- stash, reset, checkout/restore of paths,
# clean -- whatever a task grants. The owner, at the keyboard, still may.

DISCARDS = [
    "git stash",
    "git stash push -m tidy",
    "git stash -u",
    "git stash pop",
    "git stash list",
    "git stash && git pull --rebase && git stash pop",
    "cd ~/Data/0-personal && git stash",
    "git -C /srv/data/0-personal stash",
    "git -c core.pager=cat stash save wip",
    "git reset",
    "git reset --hard",
    "git reset --hard origin/main",
    "git reset HEAD org/inbox.org",
    "git reset --soft HEAD~1",
    "git -C 0-personal reset --mixed",
    "git checkout -- org/inbox.org",
    "git checkout org/someday.org",
    "git checkout org/inbox.org org/someday.org",
    "git checkout HEAD -- .datacore/events/winston.jsonl",
    "git checkout .",
    "git checkout -f main",
    "git checkout -p",
    "git restore org/inbox.org",
    "git restore --staged org/inbox.org",
    "git restore --source=HEAD --worktree .",
    "git clean -fd",
    "git clean -n",
    "git switch --discard-changes main",
    "git switch -f main",
]


@pytest.mark.parametrize("cmd", DISCARDS)
def test_every_form_of_hiding_uncommitted_work_is_classified(cmd):
    assert "worktree.discard" in tp.classify("Bash", {"command": cmd}, EFFECTS), cmd
    # Hermes, the runtime the box's inbox job runs on, calls its shell `terminal`.
    assert "worktree.discard" in tp.classify("terminal", {"command": cmd}, EFFECTS), cmd


def test_code_that_shells_out_to_a_discard_is_classified_too():
    code = 'import subprocess\nsubprocess.run(["git", "stash"], cwd="0-personal")'
    assert "worktree.discard" in tp.classify("execute_code", {"code": code}, EFFECTS)
    code = "subprocess.check_call(['git', '-C', 'x', 'reset', '--hard'])"
    assert "worktree.discard" in tp.classify("execute_code", {"code": code}, EFFECTS)


KEEPS = [
    "git status --short",
    "git log --oneline -5",
    "git diff org/inbox.org",
    "git show HEAD:org/inbox.org",
    "git add org/inbox.org org/next_actions.org",
    "git commit -m 'cos: process-inbox' -- org/inbox.org org/next_actions.org",
    "git pull --no-rebase",
    "git push",
    "git check-ignore .datacore/events/winston.jsonl",
    "git checkout main",
    "git checkout -b nightshift/fix-inbox",
    "git switch nightshift/fix-inbox",
    "git fetch origin && git rev-parse HEAD",
    "grep -rn stash docs/",
    "python3 .datacore/lib/ledger_transport.py --help",
]


@pytest.mark.parametrize("cmd", KEEPS)
def test_ordinary_git_and_commit_by_path_are_not_a_discard(cmd):
    assert "worktree.discard" not in tp.classify("Bash", {"command": cmd}, EFFECTS), cmd


def test_an_unattended_agent_is_refused_every_discard_whatever_its_grants(policy_file):
    data = yaml.safe_load(policy_file.read_text())
    data["principals"]["winston"] = {"never_effects": ["worktree.discard"]}
    policy_file.write_text(yaml.safe_dump(data))
    for cmd in DISCARDS:
        d = tp.decide("winston", "terminal", {"command": cmd}, granted=["worktree.discard", "data.delete"],
                      effects=EFFECTS, policy_path=policy_file)
        assert d.blocked and d.kind == "never" and "worktree.discard" in d.reason, cmd
    for cmd in KEEPS:
        d = tp.decide("winston", "terminal", {"command": cmd}, effects=EFFECTS, policy_path=policy_file)
        assert d.allow, (cmd, d.reason)
    # The owner at the keyboard is not an unattended principal.
    assert tp.decide("gregor", "Bash", {"command": "git stash"}, effects=EFFECTS,
                     policy_path=policy_file).allow


def test_the_shipped_policy_binds_every_agent_principal():
    """Every principal with limits (an agent) lists worktree.discard among its
    never-effects, in the tracked file and in this install's local one; the
    effect is part of the closed vocabulary."""
    from ledger.policy import load_policy
    policy = load_policy(tp.DEFAULT_POLICY_FILE)
    assert "worktree.discard" in set(policy.known_effects)
    agents = {n: e for n, e in (policy.principals or {}).items() if (e or {}).get("never_effects")}
    assert agents, "no agent principals declared"
    for name, entry in agents.items():
        assert "worktree.discard" in entry["never_effects"], name


# ── a principal's own repository (owner decision 2026-09-30) ────────────────
# Tris's live gateway moves onto the current core code, so her whole declared
# policy applies; the owner allows her ordinary pushes to her OWN space
# repository (tris-space) and nothing wider: a force push stays never
# (history.rewrite), a push to any other repository's main stays co-signed
# (push.shared), stash stays never (worktree.discard). The grant is scoped by
# the push's destination URL, resolved from the repository the command names;
# a push whose repository cannot be told stays co-signed (fail closed).

OWN_URL = "https://github.com/tris-on-hermes/tris-space.git"
OWN_REPOS = [r"^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)tris-on-hermes/tris-space(\.git)?/?$"]


def _git_repo(path: Path, url: str) -> Path:
    import subprocess
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", url], check=True)
    return path


@pytest.fixture
def own_repo_policy(tmp_path):
    never = ["payment", "prod.deploy", "history.rewrite", "email.send", "public.post", "worktree.discard"]
    p = tmp_path / "approvals_policy.yaml"
    p.write_text(yaml.safe_dump({
        "version": 1, "approver": "human",
        "cosign_effects": ["email.send", "payment", "prod.deploy", "data.delete", "push.shared"],
        "known_effects": ["email.send", "payment", "prod.deploy", "data.delete", "push.shared",
                          "history.rewrite", "public.post", "worktree.discard", "write"],
        "principals": {
            "gregor": {},
            "tris": {"never_effects": never, "own_repos": OWN_REPOS},
            "miles": {"never_effects": never},
        },
    }))
    return p


@pytest.fixture
def repos(tmp_path):
    return {"own": _git_repo(tmp_path / "tris-space", OWN_URL),
            "core": _git_repo(tmp_path / "datacore", "git@github.com:datacore-one/datacore.git"),
            "evil": _git_repo(tmp_path / "evil", "https://github.com/tris-on-hermes/tris-space-fork.git")}


def _tris(cmd, policy, tool="terminal", **extra):
    return tp.decide("tris", tool, {"command": cmd, **extra}, effects=EFFECTS, policy_path=policy)


def test_tris_may_push_to_her_own_repository(own_repo_policy, repos):
    own = repos["own"]
    for cmd in (f"git -C {own} push origin main",
                f"cd {own} && git add x && git commit -m 'tris: x' -- x && git push origin main",
                f"git push {OWN_URL} HEAD:main"):
        d = _tris(cmd, own_repo_policy)
        assert d.allow and d.kind == "granted" and "push.shared" in d.effects, (cmd, d.reason)
    # the Hermes terminal's own working directory, and the Claude Code tool name
    assert _tris("git push origin main", own_repo_policy, workdir=str(own)).allow
    assert _tris(f"git -C {own} push origin main", own_repo_policy, tool="Bash").allow


def test_tris_force_push_to_her_own_repository_is_never(own_repo_policy, repos):
    own = repos["own"]
    for cmd in (f"git -C {own} push --force origin main", f"git -C {own} push -f origin main",
                f"cd {own} && git push origin +main", f"git -C {own} push --force-with-lease origin main"):
        d = _tris(cmd, own_repo_policy)
        assert d.blocked and d.kind == "never" and "history.rewrite" in d.reason, cmd


def test_tris_push_to_another_repositorys_main_stays_cosigned(own_repo_policy, repos):
    for cmd in (f"git -C {repos['core']} push origin main",
                f"cd {repos['core']} && git push origin main",
                f"git -C {repos['evil']} push origin main",       # a look-alike name is not hers
                "git push git@github.com:datacore-one/datacore.git main",
                # one push to her own repo does not carry a second one elsewhere
                f"git -C {repos['own']} push origin main && git -C {repos['core']} push origin main",
                # cd elsewhere after hers: the push runs where the last cd left it
                f"cd {repos['own']} && cd {repos['core']} && git push origin main"):
        d = _tris(cmd, own_repo_policy)
        assert d.blocked and d.kind == "cosign" and "push.shared" in d.reason, cmd


def test_a_push_whose_repository_cannot_be_told_stays_cosigned(own_repo_policy, repos):
    # no -C, no cd, no workdir: the gateway's own cwd is not the terminal's
    for cmd in ("git push origin main", "cd relative/dir && git push origin main",
                f"git -C {repos['own']} push nosuchremote main",
                f"git --git-dir=/elsewhere/.git -C {repos['own']} push origin main",
                f"cd $REPO && git push origin main"):
        d = _tris(cmd, own_repo_policy)
        assert d.blocked and d.kind == "cosign", cmd


def test_own_repository_does_not_release_tags_or_other_effects(own_repo_policy, repos):
    own = repos["own"]
    d = _tris(f"git -C {own} push origin main --tags", own_repo_policy)
    assert d.blocked and d.kind == "never" and "prod.deploy" in d.reason
    d = _tris(f"cd {own} && rm -rf org && git push origin main", own_repo_policy)
    assert d.blocked and d.kind == "cosign" and "data.delete" in d.reason


def test_tris_rest_of_the_declared_policy_stands(own_repo_policy, repos):
    own = repos["own"]
    for cmd in (f"cd {own} && git stash", f"git -C {own} reset --hard", "git stash"):
        d = _tris(cmd, own_repo_policy)
        assert d.blocked and d.kind == "never" and "worktree.discard" in d.reason, cmd
    assert _tris(f"git -C {own} status", own_repo_policy).allow
    d = _tris("sendmail someone@example.org", own_repo_policy)
    assert d.blocked and d.kind == "never" and "email.send" in d.reason
    # a bare push names no shared branch: allowed, as before
    assert _tris("git push", own_repo_policy).allow


def test_own_repositories_are_per_principal(own_repo_policy, repos):
    """Nothing loosens for anyone else: miles pushing to tris-space is co-signed."""
    d = tp.decide("miles", "terminal", {"command": f"git -C {repos['own']} push origin main"},
                  effects=EFFECTS, policy_path=own_repo_policy)
    assert d.blocked and d.kind == "cosign" and "push.shared" in d.reason


@pytest.mark.parametrize("bad", ["tris-space", ["("], [""], [3]])
def test_malformed_own_repos_is_refused(tmp_path, bad):
    from ledger.policy import PolicyError, load_policy
    p = tmp_path / "approvals_policy.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "approver": "human", "cosign_effects": ["push.shared"],
                                 "principals": {"tris": {"own_repos": bad}}}))
    with pytest.raises(PolicyError):
        load_policy(p)


# ── a pull that stashes by itself (owner decision 2026-09-30, OPS-10) ───────
# `--autostash` hides uncommitted work around a pull, rebase or merge; when the
# stash does not re-apply cleanly, the work stays in the stash -- the incident,
# reached with one command. So it is a discard too, in every spelling: the
# flag, the `-c` config that turns it on for one call, and setting that config.

AUTOSTASHES = [
    "git pull --autostash",
    "git pull --rebase --autostash",
    "git -C ~/Data/3-plur pull --rebase --autostash",
    "cd ~/Data/4-firm && git pull --rebase --autostash && git push",
    "git rebase --autostash origin/main",
    "git merge --autostash origin/main",
    "git -c rebase.autoStash=true pull --rebase",
    "git -c rebase.autostash=true rebase origin/main",
    "git -c pull.rebase=true -c rebase.autoStash=true pull",
    "git -c merge.autoStash=true pull --no-rebase",
    "git -c rebase.autoStash pull --rebase",
    "git config rebase.autoStash true",
    "git config --global merge.autostash yes",
]

AUTOSTASH_KEEPS = [
    "git pull --rebase",
    "git pull --no-rebase",
    "git pull --rebase --no-autostash",
    "git -c rebase.autoStash=false pull --rebase",
    "git config --get rebase.autostash",
    "git merge --no-ff origin/main",
    "git rebase --abort",
]


@pytest.mark.parametrize("cmd", AUTOSTASHES)
def test_a_pull_rebase_or_merge_that_stashes_by_itself_is_a_discard(cmd):
    assert "worktree.discard" in tp.classify("Bash", {"command": cmd}, EFFECTS), cmd
    assert "worktree.discard" in tp.classify("terminal", {"command": cmd}, EFFECTS), cmd


def test_code_that_autostashes_is_a_discard_too():
    code = 'import subprocess\nsubprocess.run(["git", "pull", "--rebase", "--autostash"])'
    assert "worktree.discard" in tp.classify("execute_code", {"code": code}, EFFECTS)


@pytest.mark.parametrize("cmd", AUTOSTASH_KEEPS)
def test_pulls_that_stash_nothing_stay_open(cmd):
    assert "worktree.discard" not in tp.classify("Bash", {"command": cmd}, EFFECTS), cmd


def test_an_unattended_agent_is_refused_autostash_whatever_its_grants(policy_file):
    data = yaml.safe_load(policy_file.read_text())
    data["principals"]["winston"] = {"never_effects": ["worktree.discard"]}
    policy_file.write_text(yaml.safe_dump(data))
    for cmd in AUTOSTASHES:
        d = tp.decide("winston", "terminal", {"command": cmd}, granted=["worktree.discard"],
                      effects=EFFECTS, policy_path=policy_file)
        assert d.blocked and d.kind == "never" and "worktree.discard" in d.reason, cmd
    assert tp.decide("gregor", "Bash", {"command": "git pull --rebase --autostash"}, effects=EFFECTS,
                     policy_path=policy_file).allow


# ── a standing grant (owner decision 2026-09-30, Data's daily X post) ───────
# Data's scheduled X post runs through OpenClaw's gateway, whose environment
# is fixed, so no per-task grant can reach it. A principal's `standing_grants`
# release ONE co-signed effect for the calls a regex names -- here public.post
# for the comms poster script -- judged per shell command: every command in
# the call that causes the effect must be one the grant names. Never-effects
# and per-transaction effects are never released this way.

POSTER = "python3 ~/Data/.datacore/modules/comms/lib/x_poster.py --account plur 'Human: hi'"
X_GRANT = [{"effect": "public.post", "command": r"(^|/)\.datacore/modules/comms/lib/x_poster\.py\b",
            "why": "the daily X post"}]


@pytest.fixture
def standing_policy(tmp_path):
    p = tmp_path / "approvals_policy.yaml"
    p.write_text(yaml.safe_dump({
        "version": 1, "approver": "human",
        "cosign_effects": ["email.send", "payment", "public.post", "message.send", "push.shared"],
        "known_effects": ["email.send", "payment", "public.post", "message.send", "push.shared",
                          "worktree.discard"],
        "principals": {
            "gregor": {},
            "data": {"never_effects": ["worktree.discard"], "standing_grants": X_GRANT},
            "winston": {"never_effects": ["worktree.discard", "public.post"], "standing_grants": X_GRANT},
            "miles": {"never_effects": ["worktree.discard"]},
        },
    }))
    return p


def _as(principal, cmd, policy, tool="Bash"):
    inp = {"command": cmd} if tool in ("Bash", "terminal") else {"code": cmd}
    return tp.decide(principal, tool, inp, effects=EFFECTS, policy_path=policy)


def test_the_standing_grant_releases_the_named_poster(standing_policy):
    for cmd in (POSTER, f"cd ~/Data/2-plur-space && {POSTER}",
                f"set -a; . ~/Data/.datacore/env/.env; set +a; {POSTER}"):
        d = _as("data", cmd, standing_policy)
        assert d.allow and d.kind == "granted" and "public.post" in d.effects, (cmd, d.reason)
    assert _as("data", POSTER, standing_policy, tool="terminal").allow


def test_the_standing_grant_names_only_its_own_calls(standing_policy):
    for cmd in ("python3 engagement_post.py 'hi'",
                "curl -X POST https://api.x.com/2/tweets -d '{\"text\":\"hi\"}'",
                "python3 /tmp/x_poster.py 'hi'",                               # a look-alike elsewhere
                f"{POSTER} && python3 chrome_poster.py 'again'",                 # a second poster rides along
                f"{POSTER}; curl -X POST https://api.x.com/2/tweets -d x"):
        d = _as("data", cmd, standing_policy)
        assert d.blocked and d.kind == "cosign" and "public.post" in d.reason, cmd


def test_the_standing_grant_releases_only_its_effect(standing_policy):
    d = _as("data", f"{POSTER} && sendmail someone@example.org", standing_policy)
    assert d.blocked and d.kind == "cosign" and "email.send" in d.reason
    assert "public.post" not in d.reason


def test_the_standing_grant_does_not_reach_code_or_other_principals(standing_policy):
    code = "import subprocess; subprocess.run(['python3', '/srv/agent/.datacore/modules/comms/lib/x_poster.py'])"
    d = _as("data", code, standing_policy, tool="execute_code")
    assert d.blocked and d.kind == "cosign"
    d = _as("miles", POSTER, standing_policy)
    assert d.blocked and d.kind == "cosign" and "public.post" in d.reason


def test_a_standing_grant_never_releases_a_never_effect(standing_policy):
    d = _as("winston", POSTER, standing_policy)
    assert d.blocked and d.kind == "never" and "public.post" in d.reason


def test_a_standing_grant_never_releases_a_per_transaction_effect(tmp_path):
    p = tmp_path / "approvals_policy.yaml"
    p.write_text(yaml.safe_dump({
        "version": 1, "approver": "human", "cosign_effects": ["payment"],
        "principals": {"data": {"standing_grants": [{"effect": "payment", "command": "stripe"}]}}}))
    d = tp.decide("data", "Bash", {"command": "curl -X POST https://api.stripe.com/v1/charges -d amount=1"},
                  effects=EFFECTS, policy_path=p)
    assert d.blocked and d.kind == "cosign" and "payment" in d.reason


@pytest.mark.parametrize("bad", [
    "public.post",
    [{"effect": "public.post"}],
    [{"command": "x_poster"}],
    [{"effect": "public.post", "command": "("}],
    [{"effect": "", "command": "x"}],
    [{"effect": "public.post", "command": "x", "extra": 1}],
    ["public.post"],
])
def test_malformed_standing_grants_are_refused(tmp_path, bad):
    from ledger.policy import PolicyError, load_policy
    p = tmp_path / "approvals_policy.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "approver": "human", "cosign_effects": ["public.post"],
                                 "principals": {"data": {"standing_grants": bad}}}))
    with pytest.raises(PolicyError):
        load_policy(p)


# ── this install's grants for Data (owner decision 2026-09-30) ──────────────
# Checked against the merged policy the fleet runs (tracked + local), so a
# local file copied to a host is judged too. Skipped where data is undeclared.

def _install_policy_declares(name):
    from ledger.policy import load_policy
    return name in (load_policy(tp.DEFAULT_POLICY_FILE).principals or {})


@pytest.mark.skipif(not _install_policy_declares("data"), reason="this install declares no data principal")
def test_this_install_lets_datas_daily_post_and_blog_push_through(tmp_path):
    site = _git_repo(tmp_path / "website", "git@github.com:plur-ai/website.git")
    other = _git_repo(tmp_path / "plur-space", "git@github.com:plur-ai/plur-space.git")
    ok = [POSTER.replace("~/Data", "/srv/agent/Data"),
          f"git -C {site} push origin main",
          f"cd {site} && git add src && git commit -m 'blog: x' -- src && git push origin main"]
    for cmd in ok:
        d = tp.decide("data", "Bash", {"command": cmd})
        assert d.allow, (cmd, d.reason)
    d = tp.decide("data", "Bash", {"command": f"git -C {site} push --force origin main"})
    assert d.blocked and d.kind == "never" and "history.rewrite" in d.reason
    d = tp.decide("data", "Bash", {"command": f"git -C {other} push origin main"})
    assert d.blocked and d.kind == "cosign" and "push.shared" in d.reason
    d = tp.decide("data", "Bash", {"command": "python3 engagement_post.py 'hi'"})
    assert d.blocked and d.kind == "cosign" and "public.post" in d.reason
