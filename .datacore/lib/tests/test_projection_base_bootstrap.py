"""A space with no projection base and a drifted org file must have an exit.

reconcile() with no base demands the org file already agree with the ledger,
and the base is only written after a projection succeeds. So a space that
drifted before its first render can never render again: projection refuses,
and Phase-1 ingest IS the projection, so ingest refuses too. Four of ten spaces
here were in that state and the hourly cycle had failed since 2026-09-09.

--adopt-org records where reconciliation starts. It must write ONLY the base:
the org file and the ledger are evidence and stay untouched.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import pytest  # noqa: E402

from ledger.projection_state import STATE, load_base  # noqa: E402
from ledger.log import EventLog  # noqa: E402
import ledger_project_org as projector  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    # Without this these tests inherit whatever DATACORE_STATE the run was
    # given, and a sibling suite's retained transaction journal makes them fail
    # only when run together -- which is exactly how they first failed.
    state = tmp_path / 'runtime-state'
    state.mkdir(mode=0o700)
    monkeypatch.setenv('DATACORE_STATE', str(state))


def _space(tmp_path):
    space = tmp_path / '1-fixture'
    (space / '.datacore/events').mkdir(parents=True)
    (space / '.datacore').joinpath('space.yaml').write_text('name: fixture\ntype: team\n', encoding='utf-8')
    (space / 'org').mkdir()
    log = EventLog(space, 'mac')
    log.append('item.create', {'id': 'i1', 'title': 'Recorded task'})
    return space


def test_adopt_gives_a_drifted_space_an_exit(tmp_path, monkeypatch):
    space = _space(tmp_path)
    org = space / 'org/next_actions.org'
    org.write_text('* TODO Authored heading\n:PROPERTIES:\n:ID: i1\n:END:\n', encoding='utf-8')
    monkeypatch.setattr(projector, 'phase', lambda _s: 1)

    before = org.read_text(encoding='utf-8')
    assert 'REFUSED' in projector.project_space(space), 'precondition: it is stuck'

    result = projector.project_space(space, adopt_org=True)
    assert 'adopted' in result
    assert (space / STATE).exists(), 'a base now exists'
    assert load_base(space) is not None
    assert org.read_text(encoding='utf-8') == before, 'the org file must not be rewritten'


def test_adopt_is_inert_once_a_base_exists(tmp_path, monkeypatch):
    space = _space(tmp_path)
    org = space / 'org/next_actions.org'
    org.write_text('* TODO Authored heading\n:PROPERTIES:\n:ID: i1\n:END:\n', encoding='utf-8')
    monkeypatch.setattr(projector, 'phase', lambda _s: 1)
    assert 'adopted' in projector.project_space(space, adopt_org=True)

    # The second call must NOT take the adopt path again. Once a base exists the
    # space is unblocked, so it projects normally -- and a normal projection
    # updating the base is the whole point. What must not happen is adopt
    # quietly re-baselining a space that already had one.
    second = projector.project_space(space, adopt_org=True)
    assert 'adopted' not in second, 'adopt establishes a FIRST base only'
    assert 'generated' in second or 'REFUSED' in second


def test_adopt_is_opt_in(tmp_path, monkeypatch):
    space = _space(tmp_path)
    (space / 'org/next_actions.org').write_text(
        '* TODO Authored heading\n:PROPERTIES:\n:ID: i1\n:END:\n', encoding='utf-8')
    monkeypatch.setattr(projector, 'phase', lambda _s: 1)
    assert 'REFUSED' in projector.project_space(space), 'refusal stays the default'
    assert not (space / STATE).exists(), 'no base is written without asking'
