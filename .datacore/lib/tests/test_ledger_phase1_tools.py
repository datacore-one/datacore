"""Phase 1 tooling: repair the ledger, generate org from it only when flipped, flip and reverse."""
import importlib.util, json, pathlib, subprocess, sys
import pytest

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
    (space / '.datacore/ledger-edit-protocol').write_text('1\n')
    log = EventLog(space, "mac")
    log.append("item.create", {"id": "old-alpha", "title": "Alpha task", "tags": ["research"], "effective_tags": ["research"], "space": "9-drill", "level": 1})
    log.append("item.create", {"id": "new-alpha", "title": "Alpha task", "tags": ["research"], "effective_tags": ["research"], "space": "9-drill", "level": 1})
    log.append("item.create", {"id": "beta", "title": "Beta task (old title)", "tags": ["AI"], "effective_tags": ["AI"], "space": "9-drill", "level": 1})
    log.append("item.create", {"id": "ghost", "title": "Nobody has this in org", "tags": [], "effective_tags": [], "space": "9-drill", "level": 1})
    return space


def test_prepare_preserves_unmatched_and_similarly_named_work_and_updates_drift(tmp_path):
    P = _load("ledger_phase1_prepare"); space = _space(tmp_path)
    plan = P.plan(space)
    assert plan['unmatched'] == ['ghost', 'old-alpha']
    assert [event['payload']['id'] for event in plan['events']] == ['beta']
    P.apply(space, "mac", plan)
    from ledger.fold import fold, closure_kind
    from ledger.log import read_events
    st = fold(read_events(space))
    assert st.items['old-alpha'].status == st.items['ghost'].status == 'created'
    assert st.items["new-alpha"].status == "created"
    assert st.items["beta"].title == "Beta task" and st.items["beta"].payload["effective_tags"] == []
    assert P.plan(space)['events'] == [], 'idempotent'


def test_project_org_only_generates_in_phase_1_and_keeps_the_header(tmp_path):
    G = _load("ledger_project_org"); space = _space(tmp_path)
    before = (space / "org" / "next_actions.org").read_text()
    assert "not generated" in G.project_space(space) and (space / "org" / "next_actions.org").read_text() == before
    (space / ".datacore" / "ledger-phase").write_text("1\n")
    assert G.project_space(space).startswith("REFUSED"), "a known ID does not make its stale ledger title safe"
    assert (space / "org" / "next_actions.org").read_text() == before
    from ledger.log import EventLog
    EventLog(space, "mac").append("item.update", {"id": "beta", "title": "Beta task", "tags": []})
    assert G.project_space(space).startswith("generated")
    text = (space / "org" / "next_actions.org").read_text()
    assert text.startswith("#+TITLE: Drill\n#+STARTUP: overview\n#+TAGS: AI(a) research(r)\n"), "org header kept"
    assert "Generated from the ledger" in text and "DO NOT EDIT" not in text
    assert "Alpha task" in text and "Nobody has this in org" in text, "the ledger drives the file now"


def test_flip_refuses_until_ledger_and_org_agree_then_flips_and_reverses(tmp_path):
    F = _load("ledger_phase1_flip"); P = _load("ledger_phase1_prepare"); space = _space(tmp_path)
    _git(space, "init", "-q", "-b", "main"); _git(space, "config", "user.email", "t@t"); _git(space, "config", "user.name", "t")
    _git(space, "add", "-A"); _git(space, "commit", "-q", "-m", "base")
    assert F.flip(space, apply=True) == 1, "unreconciled source changes: refused"
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
    closes = [event for event in plan['events'] if event['type'] == 'item.dismiss']
    assert len(closes) == 1 and closes[0]['payload']['id'] == 'beta' and closes[0]['payload']['kind'] == 'done'
    P.apply(space, "mac", plan)
    from ledger.fold import fold, closure_kind
    from ledger.log import read_events
    it = fold(read_events(space)).items["beta"]
    assert it.status == "dismissed" and closure_kind(it) == "done"


@pytest.mark.parametrize('side', ['source', 'ledger'])
def test_preparation_refuses_a_stale_plan_without_appending(tmp_path, side):
    from ledger.log import EventLog, read_events
    P = _load('ledger_phase1_prepare'); space = _space(tmp_path)
    proposed = P.plan(space)
    if side == 'source':
        target = space / 'org/next_actions.org'
        target.write_text(target.read_text() + 'new source notes\n')
    else:
        EventLog(space, 'other').append('item.update', {'id': 'beta', 'title': 'new remote title'})
    events = read_events(space)
    with pytest.raises(ValueError, match='stale'):
        P.apply(space, 'mac', proposed)
    assert read_events(space) == events


def test_phase1_cache_cannot_be_used_as_authoritative_preparation_input(tmp_path):
    P = _load('ledger_phase1_prepare'); space = _space(tmp_path)
    (space / '.datacore/ledger-phase').write_text('1\n')
    with pytest.raises(ValueError, match='authored Phase 0'):
        P.plan(space)


def test_preparation_preserves_body_properties_and_inherited_tags(tmp_path):
    from ledger.log import EventLog, read_events
    from ledger.fold import fold
    P = _load('ledger_phase1_prepare'); space = _space(tmp_path)
    EventLog(space, 'mac').append('item.create', {'id': 'section', 'title': 'Group', 'section': True,
        'state': None, 'level': 1, 'tags': ['parent']})
    target = space / 'org/next_actions.org'
    text = ORG.replace('* TODO', '** TODO')
    text = text.replace('** TODO Alpha', '* Group :parent:\n:PROPERTIES:\n:ID: section\n:END:\n** TODO Alpha')
    text = text.replace(':ID: beta', ':ID: beta\n:CONTEXT: valuable property') + 'valuable notes\n'
    target.write_text(text)
    P.apply(space, 'mac', P.plan(space))
    beta = fold(read_events(space)).items['beta'].payload
    assert beta['tags'] == [] and beta['effective_tags'] == ['parent']
    assert beta['parent'] == 'section'
    assert beta['org']['properties'] == {'CONTEXT': 'valuable property'}
    assert 'valuable notes' in beta['org']['body']


def _prepared_git_space(tmp_path):
    F = _load('ledger_phase1_flip')
    P = _load('ledger_phase1_prepare')
    space = _space(tmp_path)
    P.apply(space, 'mac', P.plan(space))
    _git(space, 'init', '-q', '-b', 'main')
    _git(space, 'config', 'user.email', 't@t')
    _git(space, 'config', 'user.name', 't')
    _git(space, 'add', '-A')
    _git(space, 'commit', '-qm', 'prepared')
    return F, space


def test_flip_refuses_known_id_with_uningested_body_and_preserves_index(tmp_path):
    F, space = _prepared_git_space(tmp_path)
    target = space / F.ORG
    target.write_text(target.read_text() + 'valuable new body\n')
    before = target.read_bytes()
    index = (space / '.git/index').read_bytes()
    assert F.flip(space, True) == 1
    assert target.read_bytes() == before
    assert (space / '.git/index').read_bytes() == index
    assert not (space / F.MARKER).exists()
    assert not (space / F.PENDING).exists()


def test_reverse_projection_refusal_does_not_remove_activation(tmp_path):
    F, space = _prepared_git_space(tmp_path)
    assert F.flip(space, True) == 0
    target = space / F.ORG
    target.write_text(target.read_text() + 'valuable new body\n')
    before = target.read_bytes()
    assert F.reverse(space, True) == 1
    assert F.phase(space) == 1
    assert F.IGNORE_LINE in (space / '.gitignore').read_text()
    assert target.read_bytes() == before


def test_failed_commit_keeps_complete_pending_transition_and_retry_finishes(tmp_path):
    F, space = _prepared_git_space(tmp_path)
    hook = space / '.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o700)
    index = (space / '.git/index').read_bytes()
    head = _git(space, 'rev-parse', 'HEAD').stdout
    assert F.flip(space, True) == 1
    assert F.phase(space) == 1
    assert json.loads((space / F.PENDING).read_text())['phase'] == 1
    assert (space / '.git/index').read_bytes() == index
    assert _git(space, 'rev-parse', 'HEAD').stdout == head
    assert 'Alpha task' in (space / F.ORG).read_text()
    hook.unlink()
    assert F.flip(space, True) == 0
    assert not (space / F.PENDING).exists()
    assert _git(space, 'diff', '--cached', '--name-only').stdout == ''
    assert _git(space, 'ls-files', str(F.ORG)).stdout == ''


def test_transition_refuses_and_preserves_unrelated_staged_work(tmp_path):
    F, space = _prepared_git_space(tmp_path)
    staged = space / 'another.txt'
    staged.write_text('unrelated work\n')
    _git(space, 'add', 'another.txt')
    before = (space / '.git/index').read_bytes()
    assert F.flip(space, True) == 1
    assert (space / '.git/index').read_bytes() == before
    assert not (space / F.MARKER).exists()


def test_reverse_rolls_back_deleted_marker_on_later_disk_failure(tmp_path, monkeypatch):
    import org_transaction as tx
    F, space = _prepared_git_space(tmp_path)
    assert F.flip(space, True) == 0
    originals = {path: (space / path).read_bytes() for path in (F.ORG, F.MARKER, pathlib.Path('.gitignore'))}
    write = tx.atomic_write_text
    def fail(path, content):
        if path == space / F.PENDING:
            raise OSError('disk failed while recording transition')
        return write(path, content)
    monkeypatch.setattr(tx, 'atomic_write_text', fail)
    assert F.reverse(space, True) == 1
    assert all((space / path).read_bytes() == content for path, content in originals.items())
    assert not (space / F.PENDING).exists()
