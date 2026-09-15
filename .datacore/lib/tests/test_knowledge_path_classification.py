"""Knowledge routing preserves literal dot-prefixed directory names."""
import pytest

from knowledge_commit import KNOWLEDGE_PREFIXES, classify, is_knowledge


@pytest.mark.parametrize('prefix', KNOWLEDGE_PREFIXES)
@pytest.mark.parametrize('leading', ['', './', '././'])
def test_every_declared_knowledge_prefix_routes_with_literal_identity(prefix, leading):
    path = leading + prefix + 'fixture.txt'
    assert is_knowledge(path)
    assert classify([path]) == {'knowledge': [path], 'code': []}


@pytest.mark.parametrize('path', ['../org/task.org', '/org/task.org',
    '.datacore/state/../../modules/example.py', 'org/../src/code.py',
    '.../org/task.org', 'datacore/state/example.json', '.org/task.org',
    '.datacore/modules/example.py', 'org-private/task.org', '', None])
def test_neighboring_and_invalid_paths_do_not_gain_knowledge_routing(path):
    assert not is_knowledge(path)
