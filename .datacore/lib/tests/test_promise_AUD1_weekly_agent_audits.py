"""Promise AUD-1 (owner, 2026-09-29):

    Each of the four agents audits Datacore once a week, on its own night and
    with its own model, and files what it finds as GitHub issues that Miles
    picks up.

    (Four audit nights, three quiet nights.)

Kind: contract + deterministic + production.

Who "the four agents" are, their model families and their machines come from
the install's principal registry (principals.yaml: `cross_model_audit.agents`
and each principal's `hosts`); "Miles" is the principal whose role is
"chief of operations", and his GitHub account is that principal's `github`.
No agent, host or account is named in this file.

(a) Contract (the install's job list, tracked + manifest.local.yaml): the
    scheduled audit jobs -- jobs that run `cross_model_audit.py` for one
    `--agent`, other than the calibrate / check / review / validate / rotation
    commands -- give each of the four agents exactly ONE audit night a week (a
    cron with one fixed minute, hour and weekday), the four on four different
    weekdays, each job on a machine that agent runs on, and the four agents on
    four different model families.

(b) Deterministic: what an audit run must do with its findings. THE INTERFACE
    THE IMPLEMENTATION MUST SATISFY:

      cross_model_audit.file_issues(findings_path, *, repo: str) -> list[dict]

    For the findings file at `findings_path` (today's schema), every finding
    ends up tracked by exactly one OPEN GitHub issue in `repo`, through the
    `gh` CLI found on PATH:
      * a finding not yet tracked gets a new issue (`gh issue create`, or
        `gh api repos/<repo>/issues` with POST);
      * each new issue carries the label `audit-finding`; names the auditing
        agent and its model family (as labels or in the body); carries the
        pinned 40-hex commit, the finding's promise id and its evidence
        (path:line) in the body; and is assigned to the chief of operations'
        GitHub account (the queue Miles works from);
      * a finding already tracked by an open `audit-finding` issue -- the same
        promise and the same evidence path:line, whichever agent filed it and
        however the claim is worded -- gets no second issue;
      * returns one entry per finding, in file order:
          {"finding": <1-based index>, "url": <issue url>, "created": bool}
        and writes the same list beside the findings file as
        `<agent>.issues.yaml` = {agent, model, commit, issues: [...]}, so the
        findings file itself (and its write-time validation digest) is left
        unchanged. A file with no findings files nothing and records
        `issues: []`.
    The fake `gh` used here understands `issue list/create/view/edit/comment`,
    `label list/create`, `auth status`, `search issues` and
    `api repos/<repo>/issues[...]` (GET, POST); anything else exits 1 with
    "fake gh: unsupported", so an unexpected call shows up as a red, not a hang.

(c) Production (read-only; @production): in the last 7 days each of the four
    agents has an audit night (a findings file under the system space's
    audits/nightly/<date>/<agent>.yaml) on its own model family; the four
    nights fall on four different dates; and each night's `<agent>.issues.yaml`
    tracks every finding with an issue that exists on GitHub, labelled
    `audit-finding` and assigned to the chief of operations (zero findings:
    `issues: []`). Checked through the logged-in `gh`, read-only.

Seeded failures this must catch: every agent auditing every night; two agents
sharing a night or a family; an audit job on another agent's machine; findings
that stay in a YAML file and never become issues; an issue per finding per
night (duplicates); issues nobody is assigned.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
INSTALL = Path(os.environ.get("DATACORE_ROOT") or ROOT)
MODULE = LIB / "cross_model_audit.py"
LABEL = "audit-finding"
OPS_ROLE = "chief of operations"
NOT_AUDITS = {"calibrate", "check", "review", "validate", "rotation"}
DOW = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "7": 0}


def _roster(path: Path | None = None) -> dict[str, str]:
    import roster
    return {str(a): str(f) for a, f in (roster.section("cross_model_audit", path).get("agents") or {}).items()}


# ── (a) contract ─────────────────────────────────────────────────────────────

def _audit_jobs() -> dict[str, list[dict]]:
    """agent -> the scheduled jobs that audit Datacore as that agent."""
    from jobs.manifest import effective_doc
    doc = effective_doc(LIB / "jobs" / "manifest.yaml")
    out: dict[str, list[dict]] = {}
    for j in doc.get("jobs") or []:
        cmd = str((j or {}).get("cmd") or "")
        if "cross_model_audit.py" not in cmd:
            continue
        try:
            argv = shlex.split(cmd)
        except ValueError:
            argv = cmd.split()
        at = next(i for i, t in enumerate(argv) if t.endswith("cross_model_audit.py"))
        rest = argv[at + 1:]
        sub = rest[0] if rest and not rest[0].startswith("-") else "nightly"
        if sub in NOT_AUDITS or "--agent" not in rest:
            continue
        agent = rest[rest.index("--agent") + 1]
        out.setdefault(agent, []).append(j)
    return out


def _weekday(schedule: str) -> int | None:
    """The one weekday a cron fires on (0 = Sunday), or None if it is not one fixed weekly time."""
    f = str(schedule or "").split()
    if len(f) != 5 or not (f[0].isdigit() and f[1].isdigit()) or f[2:4] != ["*", "*"]:
        return None
    d = f[4].lower()[:3]
    if d.isdigit() and 0 <= int(d) <= 7:
        return int(d) % 7
    return DOW.get(d)


def test_each_agent_audits_once_a_week_on_its_own_night_machine_and_model():
    import roster
    agents = _roster()
    assert len(agents) == 4, f"expected four auditing agents in principals.yaml cross_model_audit.agents, found {agents}"
    problems = []
    if len(set(agents.values())) != 4:
        problems.append(f"the four agents are not on four different model families: {agents}")
    jobs = _audit_jobs()
    nights = {}
    for agent in sorted(agents):
        mine = jobs.get(agent, [])
        if len(mine) != 1:
            problems.append(f"{agent} has {len(mine)} scheduled audit jobs, expected exactly one: "
                            f"{[j.get('name') for j in mine]}")
            continue
        job = mine[0]
        day = _weekday(job.get("schedule"))
        if day is None:
            problems.append(f"{agent} audits on {job.get('schedule')!r}, not one fixed night a week")
        else:
            nights[agent] = day
        hosts = (roster.entries().get(agent) or {}).get("hosts") or []
        if job.get("machine") not in hosts:
            problems.append(f"{agent}'s audit runs on {job.get('machine')!r}, not on its own machine {hosts}")
    stray = sorted(set(jobs) - set(agents))
    if stray:
        problems.append(f"audit jobs for agents outside the four: {stray}")
    if nights and len(set(nights.values())) != len(nights):
        problems.append(f"two agents audit on the same weekday: {nights}")
    assert not problems, "the weekly audit schedule is not one own night per agent:\n  - " + "\n  - ".join(problems)


# ── (b) deterministic: findings become issues for the chief of operations ────

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
STATE = Path(os.environ["FAKE_GH_STATE"])
st = json.loads(STATE.read_text()) if STATE.exists() else {"issues": [], "next": 1}
argv = sys.argv[1:]
with open(STATE.with_suffix(".calls.jsonl"), "a") as fh:
    fh.write(json.dumps(argv) + "\n")

def save():
    STATE.write_text(json.dumps(st))

def opts(args, multi=("--label", "-l", "--assignee", "-a", "-f", "-F", "--field", "--raw-field", "--add-label", "--add-assignee")):
    o, pos, i = {}, [], 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            k, _, v = a.partition("=")
            if not _ and i + 1 < len(args) and not args[i + 1].startswith("--") and k not in ("--paginate",):
                v = args[i + 1]; i += 1
            o.setdefault(k, []).append(v)
        else:
            pos.append(a)
        i += 1
    return o, pos

def first(o, *keys, default=None):
    for k in keys:
        if o.get(k):
            return o[k][-1]
    return default

def many(o, *keys):
    out = []
    for k in keys:
        for v in o.get(k, []):
            out += [x.strip() for x in v.split(",") if x.strip()]
    return out

def body_of(o):
    f = first(o, "--body-file", "-F")
    if f:
        return sys.stdin.read() if f == "-" else Path(f).read_text()
    return first(o, "--body", "-b", default="")

def view(i, fields=None):
    full = {"number": i["number"], "url": i["url"], "html_url": i["url"], "title": i["title"],
            "body": i["body"], "state": i["state"].upper(), "labels": [{"name": l} for l in i["labels"]],
            "assignees": [{"login": a} for a in i["assignees"]]}
    return {k: full[k] for k in fields} if fields else full

def create(repo, title, body, labels, assignees):
    n = st["next"]; st["next"] += 1
    i = {"number": n, "url": f"https://github.com/{repo}/issues/{n}", "title": title, "body": body,
         "state": "open", "labels": labels, "assignees": assignees, "repo": repo}
    st["issues"].append(i); save()
    return i

def match(i, q):
    words = [w for w in q.replace('"', " ").split() if ":" not in w]
    hay = (i["title"] + "\n" + i["body"]).lower()
    return all(w.lower() in hay for w in words)

def listing(o, repo):
    state = (first(o, "--state", "-s", default="open") or "open").lower()
    labels = many(o, "--label", "-l")
    q = first(o, "--search", "-S", default="")
    out = [i for i in st["issues"] if i["repo"] == repo and (state == "all" or i["state"] == state)
           and all(l in i["labels"] for l in labels) and match(i, q)]
    fields = [f for f in (first(o, "--json", default="") or "").split(",") if f]
    return [view(i, fields or None) for i in out]

def find(ref):
    n = int(str(ref).rstrip("/").split("/")[-1].lstrip("#"))
    return next(i for i in st["issues"] if i["number"] == n)

if not argv:
    sys.exit(1)
if argv[0] == "auth":
    sys.exit(0)
if argv[0] == "label":
    print("[]" if "--json" in argv else ""); sys.exit(0)
if argv[:2] == ["search", "issues"]:
    o, pos = opts(argv[2:])
    repo = first(o, "--repo", "-R")
    q = " ".join(pos)
    hits = [view(i) for i in st["issues"] if (not repo or i["repo"] == repo) and match(i, q)
            and (first(o, "--state", default="open") in ("all", i["state"]))]
    fields = [f for f in (first(o, "--json", default="") or "").split(",") if f]
    print(json.dumps([{k: h[k] for k in fields} if fields else h for h in hits])); sys.exit(0)
if argv[0] == "issue":
    sub = argv[1]; o, pos = opts(argv[2:])
    repo = first(o, "--repo", "-R", default=os.environ.get("GH_REPO", ""))
    if sub == "list":
        print(json.dumps(listing(o, repo))); sys.exit(0)
    if sub == "create":
        i = create(repo, first(o, "--title", "-t", default=""), body_of(o), many(o, "--label", "-l"),
                   many(o, "--assignee", "-a"))
        print(i["url"]); sys.exit(0)
    if sub == "view":
        fields = [f for f in (first(o, "--json", default="") or "").split(",") if f]
        print(json.dumps(view(find(pos[0]), fields or None))); sys.exit(0)
    if sub == "edit":
        i = find(pos[0]); i["labels"] += many(o, "--add-label"); i["assignees"] += many(o, "--add-assignee"); save(); sys.exit(0)
    if sub == "comment":
        sys.exit(0)
if argv[0] == "api":
    o, pos = opts(argv[1:])
    path = pos[0].lstrip("/") if pos else ""
    method = (first(o, "-X", "--method", default=None) or ("POST" if o.get("-f") or o.get("-F") or o.get("--field") or o.get("--raw-field") else "GET")).upper()
    parts = path.split("?")[0].split("/")
    if len(parts) == 4 and parts[0] == "repos" and parts[3] == "issues":
        repo = parts[1] + "/" + parts[2]
        if method == "GET":
            items = [i for i in st["issues"] if i["repo"] == repo and i["state"] == "open"]
            print(json.dumps([dict(view(i), labels=[{"name": l} for l in i["labels"]]) for i in items])); sys.exit(0)
        fields = {}
        for v in o.get("-f", []) + o.get("-F", []) + o.get("--field", []) + o.get("--raw-field", []):
            k, _, val = v.partition("=")
            fields.setdefault(k, []).append(val)
        i = create(repo, (fields.get("title") or [""])[-1], (fields.get("body") or [""])[-1],
                   fields.get("labels[]", []), fields.get("assignees[]", []))
        print(json.dumps(view(i))); sys.exit(0)
print("fake gh: unsupported " + " ".join(argv), file=sys.stderr)
sys.exit(1)
'''

REPO = "example-org/datacore"
OPS_ACCOUNT = "ops-agent-account"
AGENTS = {"ops": "claude", "second": "deepseek", "third": "glm", "fourth": "gpt"}


def _principals(path: Path) -> Path:
    doc = {
        "principals": {
            "owner": {"kind": "human", "decision": "final", "writes_as": []},
            "ops": {"kind": "agent", "role": OPS_ROLE, "github": OPS_ACCOUNT, "hosts": ["host-a"], "writes_as": []},
            "second": {"kind": "agent", "role": "chief of staff", "github": "second-account", "hosts": ["host-b"], "writes_as": []},
            "third": {"kind": "agent", "role": "research", "github": "third-account", "hosts": ["host-c"], "writes_as": []},
            "fourth": {"kind": "agent", "role": "communications", "github": "fourth-account", "hosts": ["host-d"], "writes_as": []},
        },
        "cross_model_audit": {"agents": AGENTS, "nightly_cap_usd": {a: 1.0 for a in AGENTS}},
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _audit_module(monkeypatch, principals: Path):
    if not MODULE.is_file():
        pytest.fail(f"not built: {MODULE.relative_to(ROOT)} does not exist", pytrace=False)
    import actor_identity
    monkeypatch.setattr(actor_identity, "PRINCIPALS", principals)
    spec = importlib.util.spec_from_file_location("cross_model_audit_aud1", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not callable(getattr(mod, "file_issues", None)):
        pytest.fail("not built: cross_model_audit.file_issues(findings_path, *, repo) does not exist, so "
                    "an audit's findings never become GitHub issues", pytrace=False)
    return mod


def _head() -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                          timeout=30, check=True).stdout.strip()


def _findings(path: Path, agent: str, commit: str, findings: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"agent": agent, "model": AGENTS[agent], "capability": "tasks",
                                    "night": "2026-09-28", "commit": commit, "findings": findings},
                                   sort_keys=False), encoding="utf-8")
    return path


def _finding(promise: str, evidence: str, claim: str) -> dict:
    return {"promise": promise, "claim": claim, "evidence": evidence, "severity": "high",
            "seeded_failure": "a planted change that should turn the promise red"}


@pytest.fixture
def gh(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH, encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    state = tmp_path / "gh-state.json"
    monkeypatch.setenv("FAKE_GH_STATE", str(state))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def issues() -> list[dict]:
        return json.loads(state.read_text())["issues"] if state.exists() else []
    return issues


def _labels_and_body(i: dict) -> str:
    return (" ".join(i["labels"]) + "\n" + i["title"] + "\n" + i["body"]).lower()


def test_an_audits_findings_become_one_issue_each_for_the_chief_of_operations(tmp_path, monkeypatch, gh):
    m = _audit_module(monkeypatch, _principals(tmp_path / "principals.yaml"))
    sha = _head()
    night = tmp_path / "nightly" / "2026-09-28"
    first = [_finding("TSK-2", ".datacore/lib/spaces.py:101", "Discovery can miss a nested space."),
             _finding("INB-8", ".datacore/lib/org_workspace_adapter.py:83", "Captures are counted twice."),
             _finding("AUD-2", ".datacore/lib/cross_model_audit.py:57", "The audit can write outside its folder.")]
    a = _findings(night / "second.yaml", "second", sha, first)
    before = a.read_bytes()

    got = m.file_issues(a, repo=REPO)
    made = gh()
    problems = []
    if len(made) != 3:
        problems.append(f"three findings made {len(made)} issues, expected one each")
    for i in made:
        text = _labels_and_body(i)
        if LABEL not in i["labels"]:
            problems.append(f"issue #{i['number']} is not labelled {LABEL!r}: {i['labels']}")
        if "second" not in text or "deepseek" not in text:
            problems.append(f"issue #{i['number']} does not name the auditing agent and its model family")
        if sha not in i["body"]:
            problems.append(f"issue #{i['number']} does not carry the pinned commit {sha[:10]}...")
        if OPS_ACCOUNT not in i["assignees"]:
            problems.append(f"issue #{i['number']} is not assigned to the chief of operations "
                            f"({OPS_ACCOUNT}); assignees {i['assignees']}")
    for f in first:
        if not any(f["promise"] in i["body"] and f["evidence"] in i["body"] for i in made):
            problems.append(f"no issue carries finding {f['promise']} at {f['evidence']}")
    if [e.get("finding") for e in (got or [])] != [1, 2, 3] or not all(e.get("url") for e in got or []):
        problems.append(f"file_issues should return one {{finding, url, created}} per finding, got {got!r}")
    record = night / "second.issues.yaml"
    rec = yaml.safe_load(record.read_text()) if record.is_file() else None
    if not rec or len(rec.get("issues") or []) != 3:
        problems.append(f"no record of the issues beside the findings file ({record.name}): {rec!r}")
    if a.read_bytes() != before:
        problems.append("the findings file was changed (its write-time validation would no longer hold)")
    assert not problems, "an audit's findings did not become issues as promised:\n  - " + "\n  - ".join(problems)

    # the same night again, and another agent reporting one of the same problems: no duplicates
    again = m.file_issues(a, repo=REPO)
    b = _findings(night / "third.yaml", "third", sha, [
        _finding("TSK-2", ".datacore/lib/spaces.py:101", "Nested spaces are not found by discovery."),
        _finding("KNW-8", ".datacore/lib/spaces.py:260", "A different, new problem.")])
    other = m.file_issues(b, repo=REPO)
    made = gh()
    assert len(made) == 4, (f"re-filing the same findings, and a second agent reporting an already-open "
                            f"problem, should add exactly one issue (the new problem); there are now "
                            f"{len(made)}: {[(i['number'], i['title'][:50]) for i in made]}")
    assert not any(e.get("created") for e in again), f"re-filing created issues again: {again!r}"
    assert [e.get("created") for e in other] == [False, True], (
        f"the second agent's already-open finding should point at the existing issue: {other!r}")


def test_an_audit_with_no_findings_files_nothing(tmp_path, monkeypatch, gh):
    m = _audit_module(monkeypatch, _principals(tmp_path / "principals.yaml"))
    empty = _findings(tmp_path / "nightly" / "2026-09-29" / "fourth.yaml", "fourth", _head(), [])
    assert m.file_issues(empty, repo=REPO) == []
    assert gh() == [], "a night with no findings opened issues"
    rec = empty.with_name("fourth.issues.yaml")
    assert rec.is_file() and (yaml.safe_load(rec.read_text()) or {}).get("issues") == [], (
        "a night with no findings should still record that it filed nothing (issues: [])")


# ── (c) production ───────────────────────────────────────────────────────────

def _gh_view(url: str) -> dict | None:
    r = subprocess.run(["gh", "issue", "view", url, "--json", "url,state,labels,assignees"],
                       capture_output=True, text=True, timeout=60)
    return json.loads(r.stdout) if r.returncode == 0 else None


@pytest.mark.production
def test_last_week_each_agent_audited_on_its_own_night_and_filed_its_issues():
    import roster
    import spaces
    agents = _roster(INSTALL / ".datacore" / "registry" / "principals.yaml")
    assert len(agents) == 4, f"could not tell: principals.yaml names {len(agents)} auditing agents, not four"
    system = spaces.space_for("system", INSTALL)
    assert system, "could not tell: install.yaml declares no system space"
    nightly = INSTALL / system / "1-tracks" / "dev" / "audits" / "nightly"
    ops = roster.by_role(OPS_ROLE, INSTALL / ".datacore" / "registry" / "principals.yaml")
    ops_account = ((roster.entries(INSTALL / ".datacore" / "registry" / "principals.yaml").get(ops) or {})
                   .get("github"))
    today = date.today()
    window = [today - timedelta(days=d) for d in range(0, 7)]
    problems, nights = [], {}
    for agent, family in sorted(agents.items()):
        ran = [d for d in window if (nightly / d.isoformat() / f"{agent}.yaml").is_file()]
        if not ran:
            problems.append(f"{agent} has no audit night in the last 7 days")
            continue
        nights[agent] = ran
        for d in ran:
            f = nightly / d.isoformat() / f"{agent}.yaml"
            doc = yaml.safe_load(f.read_text()) or {}
            if doc.get("model") != family:
                problems.append(f"{agent} audited {d} on {doc.get('model')!r}, not its own family {family!r}")
            rec_path = f.with_name(f"{agent}.issues.yaml")
            if not rec_path.is_file():
                problems.append(f"{agent} {d}: {len(doc.get('findings') or [])} finding(s), no issues filed "
                                f"(no {rec_path.name})")
                continue
            rec = (yaml.safe_load(rec_path.read_text()) or {}).get("issues") or []
            if len(rec) != len(doc.get("findings") or []):
                problems.append(f"{agent} {d}: {len(doc.get('findings') or [])} finding(s) but {len(rec)} tracked")
            for e in rec:
                seen = _gh_view(str(e.get("url")))
                if seen is None:
                    problems.append(f"{agent} {d}: could not find issue {e.get('url')} on GitHub")
                    continue
                if LABEL not in [l.get("name") for l in seen.get("labels") or []]:
                    problems.append(f"{e.get('url')} is not labelled {LABEL}")
                if ops_account not in [a.get("login") for a in seen.get("assignees") or []]:
                    problems.append(f"{e.get('url')} is not assigned to the chief of operations")
    for agent, ran in nights.items():
        if len(ran) > 1:
            problems.append(f"{agent} audited on {len(ran)} nights this week, not once: {[d.isoformat() for d in ran]}")
    firsts = [ran[0] for ran in nights.values()]
    if len(set(firsts)) != len(firsts):
        problems.append(f"agents shared an audit night: {({a: r[0].isoformat() for a, r in nights.items()})}")
    assert not problems, "this week's audits did not run as promised:\n  - " + "\n  - ".join(problems)
