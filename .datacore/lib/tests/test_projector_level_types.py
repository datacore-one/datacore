"""A payload `level` that is not a depth must not fail the whole projection.

5-plur carries nine events written by `tris` with level 'task' or 'action' --
the key used for a task type, not an org heading depth. `min(recorded, depth)`
then compared str against int and raised TypeError, which failed that space's
projection, failed the hourly Phase-1 ingest, and left every space downstream
of it unprojected. The ledger is append-only, so those events do not go away:
the projector has to tolerate them.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import pytest  # noqa: E402

from ledger.projector import project  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog  # noqa: E402


def _space(tmp_path, level):
    space = tmp_path / '5-fixture'
    (space / '.datacore/events').mkdir(parents=True)
    (space / '.datacore').joinpath('space.yaml').write_text(
        'name: fixture\ntype: personal\n', encoding='utf-8')
    log = EventLog(space, 'tris')
    log.append('item.create', {'id': 'i1', 'title': 'Reachable task', 'level': level})
    return space


@pytest.mark.parametrize('level', ['task', 'action', '', None, 'deep', 3.5])
def test_unreadable_level_does_not_fail_the_projection(tmp_path, level):
    space = _space(tmp_path, level)
    text = project(fold_of(space), space='5-fixture', as_of=0).text
    assert 'Reachable task' in text, 'the item must still be projected'


def test_numeric_string_level_is_honoured(tmp_path):
    space = _space(tmp_path, '1')
    text = project(fold_of(space), space='5-fixture', as_of=0).text
    assert 'Reachable task' in text


def fold_of(space):
    from ledger.log import read_events
    return fold(read_events(space))
