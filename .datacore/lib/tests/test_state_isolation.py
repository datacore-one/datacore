"""The suite must never write into the machine's real private state directory.

On 2026-09-16 it did. `.datacore/conftest.py` points DATACORE_STATE at a
throwaway directory, but pytest collects conftest files only between rootdir and
the test, and with no ini file anywhere rootdir came from the arguments: running
`pytest tests/` from `.datacore/lib` put it one level below that conftest, so the
isolation quietly did not apply. The suite wrote its Org transaction journal into
the real `~/.datacore/state/org-transaction.json` and left it there, and since
`recover()` refuses a journal whose file has changed underneath it, EVERY Org
transaction on the machine then failed -- projection, ingest and the hourly
Phase-1 cycle among them.

`.datacore/pytest.ini` pins rootdir so the isolation always applies. This is the
test that says so, because the failure was invisible: the suite passed when run
one way and corrupted machine state when run another.
"""
import os
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from file_utils import private_state_directory  # noqa: E402


def test_state_directory_is_not_the_real_one():
    assert os.environ.get('DATACORE_STATE'), (
        'DATACORE_STATE is unset: .datacore/conftest.py did not load, so this '
        'run is writing to the real state directory'
    )
    real = Path.home() / '.datacore' / 'state'
    here = private_state_directory()
    assert real not in here.parents and here != real, here


def test_the_transaction_journal_resolves_inside_the_disposable_state():
    from org_transaction import journal_path
    assert str(journal_path()).startswith(os.environ['DATACORE_STATE'])
