"""Phase 1 tooling: repair the ledger, generate org from it only when flipped, flip and reverse."""
import importlib.util, json, pathlib, subprocess, sys

LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, LIB / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)


ORG = """#+TITLE: Drill
#+STARTUP: overview
#+TAGS: AI(a) research(r)

* TODO Alpha task  :research:
:PROPERTIES:
:ID: new-alpha
:END:
* TODO Beta task
:PROPERTIES:
:ID: beta
:END:
"""


def _space(tmp_path):
    from ledger.log import EventLog
    space = tmp_path / "9-drill"; (space / "org").mkdir(parents=True); (space / ".datacore" / "events").mkdir(parents=True)
    (space / "org" / "next_actions.org").write_text(ORG)
    log = EventLog(space, "mac")
    log.append("item.create", {"id": "old-alpha", "title": "Alpha task", "tags": ["research"], "effective_tags": ["research"], "space": "9-drill", "level": 1})
    log.append("item.create", {"id": "new-alpha", "title": "Alpha task", "tags": ["research"], "effective_tags": ["research"], "space": "9-drill", "level": 1})
    log.append("item.create", {"id": "beta", "title": "Beta task (old title)", "tags": ["AI"], "effective_tags": ["AI"], "space": "9-drill", "level": 1})
    log.append("item.create", {"id": "ghost", "title": "Nobody has this in org", "tags": [], "effective_tags": [], "space": "9-drill", "level": 1})
    return space


def test_prepare_retires_twins_as_housekeeping_and_updates_drift(tmp_path):
    P = _load("ledger_phase1_prepare"); space = _space(tmp_path)
    plan = P.plan(space)
    dismissed = dict(plan["dismiss"])
    assert "old-alpha" in dismissed and "superseded by new-alpha" in dismissed["old-alpha"]
    assert "ghost" in dismissed and "no org task" in dismissed["ghost"]
    assert [u[0] for u in plan["update"]] == ["beta"], "only the task whose title/tags moved in org"
    P.apply(space, "mac", plan)
    from ledger.fold import fold, closure_kind
    from ledger.log import read_events
    st = fold(read_events(space))
    assert st.items["old-alpha"].status == "dismissed" and closure_kind(st.items["old-alpha"]) == "housekeeping"
    assert st.items["new-alpha"].status == "created"
    assert st.items["beta"].title == "Beta task" and st.items["beta"].payload["effective_tags"] == []
    assert P.plan(space)["dismiss"] == [] and P.plan(space)["update"] == [], "idempotent"


def test_project_org_only_generates_in_phase_1_and_keeps_the_header(tmp_path):
    G = _load("ledger_project_org"); space = _space(tmp_path)
    before = (space / "org" / "next_actions.org").read_text()
    assert "not generated" in G.project_space(space) and (space / "org" / "next_actions.org").read_text() == before
    (space / ".datacore" / "ledger-phase").write_text("1\n")
    assert G.project_space(space).startswith("generated")
    text = (space / "org" / "next_actions.org").read_text()
    assert text.startswith("#+TITLE: Drill\n#+STARTUP: overview\n#+TAGS: AI(a) research(r)\n"), "org header kept"
    assert "Generated from the ledger" in text and "DO NOT EDIT" not in text
    assert "Alpha task" in text and "Nobody has this in org" in text, "the ledger drives the file now"


def test_flip_refuses_until_ledger_and_org_agree_then_flips_and_reverses(tmp_path):
    F = _load("ledger_phase1_flip"); P = _load("ledger_phase1_prepare"); space = _space(tmp_path)
    _git(space, "init", "-q", "-b", "main"); _git(space, "config", "user.email", "t@t"); _git(space, "config", "user.name", "t")
    _git(space, "add", "-A"); _git(space, "commit", "-q", "-m", "base")
    assert F.flip(space, apply=True) == 1, "orphans in the ledger: refused"
    P.apply(space, "mac", P.plan(space))
    _git(space, "add", "-A"); _git(space, "commit", "-q", "-m", "prepared")
    assert F.flip(space, apply=True) == 0
    assert (space / ".datacore" / "ledger-phase").read_text().strip() == "1"
    assert subprocess.run(["git", "ls-files", "--error-unmatch", "org/next_actions.org"], cwd=space, capture_output=True).returncode != 0, "untracked while generated"
    assert "org/next_actions.org" in (space / ".gitignore").read_text()
    assert F.reverse(space, apply=True) == 0
    assert subprocess.run(["git", "ls-files", "--error-unmatch", "org/next_actions.org"], cwd=space, capture_output=True).returncode == 0, "tracked again"
    assert not (space / ".datacore" / "ledger-phase").exists()
    assert "Alpha task" in (space / "org" / "next_actions.org").read_text()


def test_header_copy_is_written_once(tmp_path):
    G = _load("ledger_project_org"); space = _space(tmp_path)
    (space / ".datacore" / "ledger-phase").write_text("1\n")
    G.project_space(space); first = (space / ".datacore" / "ledger-org-header").read_text()
    (space / "org" / "next_actions.org").write_text("#+TITLE: Changed by a host\n* TODO x\n")
    G.project_space(space)
    assert (space / ".datacore" / "ledger-org-header").read_text() == first, "the copy never changes after the flip"


def test_prepare_closes_items_that_org_already_finished(tmp_path):
    """DONE in org, live in the ledger: no claim exists, so it is dismissed with kind done."""
    P = _load("ledger_phase1_prepare"); space = _space(tmp_path)
    org = (space / "org" / "next_actions.org").read_text().replace("* TODO Beta task", "* DONE Beta task")
    (space / "org" / "next_actions.org").write_text(org)
    plan = P.plan(space)
    assert [c[0] for c in plan["close"]] == ["beta"] and plan["close"][0][1] == "done"
    P.apply(space, "mac", plan)
    from ledger.fold import fold, closure_kind
    from ledger.log import read_events
    it = fold(read_events(space)).items["beta"]
    assert it.status == "dismissed" and closure_kind(it) == "done"
