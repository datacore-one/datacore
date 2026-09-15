import pytest

from delegation_requirements import execution_gaps


@pytest.mark.parametrize('surface', [None, '', '  ', 'unassigned', ' UNASSIGNED ', 42])
def test_undefined_destination_is_not_an_execution_contract(surface):
    assert execution_gaps({'SURFACE': surface, 'DONE_WHEN': 'verified outcome'}) == ['SURFACE']


@pytest.mark.parametrize('key', ['DONE_WHEN', 'ACCEPTANCE_CRITERIA'])
def test_current_and_legacy_completion_condition_are_equivalent(key):
    assert execution_gaps({'SURFACE': 'example/repo', key: 'verified outcome'}) == []


def test_a_queue_reference_is_not_a_resolved_execution_contract():
    assert execution_gaps({'SOURCE_ID': 'another-task'}) == ['SURFACE', 'DONE_WHEN']
