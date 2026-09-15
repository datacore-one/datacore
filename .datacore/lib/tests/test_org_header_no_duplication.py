"""A projected header must not grow by one line per cycle.

_with_org_header preserves the authored `#+` lines from the existing file, and
the projector emits some of those itself. Anything on both sides accumulates:
the file's copy is carried forward, the projector adds a fresh one, and next
cycle the fresh one is in the preamble too.

SEQ_TODO was caught on 2026-09-08 and excluded by name. #+FILETAGS is emitted
by projector.py when items share a common filetag, was not on the list, and
grew to 196 copies in 9-practice and 24 in 0-personal -- inflating the
inherited tags of every item in those files.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import pytest  # noqa: E402

import ledger_project_org as projector  # noqa: E402

BODY = ('# -*- GENERATED FILE — DO NOT EDIT -*-\n\n'
        '#+SEQ_TODO: TODO(t) | DONE(d!)\n'
        '#+FILETAGS: :practice:\n\n'
        '* TODO A task\n')


def _cycle(space, target, times):
    counts = []
    for _ in range(times):
        out = projector._with_org_header(space, target, BODY, remember=False)
        target.write_text(out, encoding='utf-8')
        counts.append(out.count('#+FILETAGS'))
    return counts


@pytest.mark.parametrize('directive', ['#+FILETAGS: :practice:', '#+SEQ_TODO: TODO(t) | DONE(d!)'])
def test_a_projector_directive_does_not_accumulate(tmp_path, directive):
    space = tmp_path / '9-fixture'
    (space / 'org').mkdir(parents=True)
    target = space / 'org/next_actions.org'
    target.write_text(f'#+TITLE: Fixture\n{directive}\n\n' + BODY, encoding='utf-8')

    counts = _cycle(space, target, 5)
    assert len(set(counts)) == 1, f'{directive} grew across cycles: {counts}'
    assert counts[0] == 1, 'exactly one copy survives'


def test_authored_directives_are_still_preserved(tmp_path):
    space = tmp_path / '9-fixture'
    (space / 'org').mkdir(parents=True)
    target = space / 'org/next_actions.org'
    target.write_text('#+TITLE: Fixture\n#+CATEGORY: Practice\n#+STARTUP: overview\n\n' + BODY,
                      encoding='utf-8')
    out = projector._with_org_header(space, target, BODY, remember=False)
    for line in ('#+TITLE: Fixture', '#+CATEGORY: Practice', '#+STARTUP: overview'):
        assert out.count(line) == 1, f'{line} must survive exactly once'


def test_an_existing_pile_up_collapses_on_the_next_projection(tmp_path):
    """Ten days of accumulation should not survive one cycle."""
    space = tmp_path / '9-fixture'
    (space / 'org').mkdir(parents=True)
    target = space / 'org/next_actions.org'
    pile = '#+FILETAGS: :practice:\n' * 196
    target.write_text('#+TITLE: Fixture\n' + pile + '\n' + BODY, encoding='utf-8')
    out = projector._with_org_header(space, target, BODY, remember=False)
    assert out.count('#+FILETAGS') == 1
