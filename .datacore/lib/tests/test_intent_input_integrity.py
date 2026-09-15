"""Intent reports must retain source identity and refuse incomplete evidence."""
import os

import pytest

from intent_tasks import place
from priority_score import IntentGraph


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def node(identity='goal', title='Shipping', extra=''):
    return f'* {title}\n:PROPERTIES:\n:INTENT_ID: {identity}\n:LEVEL: 1\n{extra}:END:\n'


def space(root, relative, name):
    path = root / relative
    write(path / '.datacore/config.yaml', f'space: {{name: {name}, type: team}}\n')
    write(path / 'org/intents.org', node())
    return path


@pytest.mark.parametrize('source', ['.datacore/intents.org', '.datacore/cos/priorities.yaml',
                                    '.datacore/tags.yaml', 'team/org/next_actions.org'])
def test_unreadable_input_cannot_be_an_empty_graph_or_task_set(tmp_path, source):
    space(tmp_path, 'team', 'team')
    path = write(tmp_path / source, '')
    path.write_bytes(b'\xff')
    with pytest.raises((ValueError, OSError)):
        place(tmp_path, IntentGraph.load(tmp_path))
    assert path.read_bytes() == b'\xff'


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'directory', 'parent-alias'])
def test_graph_source_cannot_alias_another_file(tmp_path, kind):
    root = tmp_path / 'data'
    target = write(tmp_path / 'outside/intents.org', node())
    path = root / '.datacore/intents.org'
    if kind == 'parent-alias':
        root.mkdir()
        (root / '.datacore').symlink_to(target.parent, target_is_directory=True)
    else:
        path.parent.mkdir(parents=True)
        if kind == 'symlink':
            path.symlink_to(target)
        elif kind == 'hardlink':
            os.link(target, path)
        else:
            path.mkdir()
    with pytest.raises((ValueError, OSError)):
        IntentGraph.load(root)
    assert target.read_text() == node()


def test_space_ids_survive_directory_renumbering(tmp_path):
    selected = space(tmp_path, '5-alpha', 'alpha')
    write(selected / 'org/next_actions.org', '* NEXT Plain task\n:PROPERTIES:\n:INTENT: 5-alpha:goal\n:END:\n')
    first = place(tmp_path, IntentGraph.load(tmp_path))
    assert first['index'] == {'alpha:goal': 1}
    selected.rename(tmp_path / 'nested-alpha')
    assert place(tmp_path, IntentGraph.load(tmp_path)) == first


def test_local_serves_and_explicit_task_references_precede_root(tmp_path):
    write(tmp_path / '.datacore/intents.org', node())
    selected = space(tmp_path, '5-alpha', 'alpha')
    write(selected / 'org/intents.org', node() + node('child', 'Child', ':SERVES: goal\n'))
    write(selected / 'org/next_actions.org', '* NEXT Plain task\n:PROPERTIES:\n:INTENT: goal\n:END:\n')
    graph = IntentGraph.load(tmp_path)
    assert graph.nodes['alpha:child'].serves == ('alpha:goal',)
    assert place(tmp_path, graph)['index'] == {'alpha:goal': 1}


def test_space_tag_meanings_do_not_leak_to_other_spaces(tmp_path):
    for label in ['alpha', 'beta']:
        selected = space(tmp_path, 'nested/' + label, label)
        write(selected / '.datacore/tags.yaml', 'domains:\n  delivery: {intent: goal}\n')
        write(selected / 'org/next_actions.org', '* NEXT Plain task :delivery:\n')
    graph = IntentGraph.load(tmp_path)
    assert place(tmp_path, graph)['index'] == {'alpha:goal': 1, 'beta:goal': 1}
    assert graph.match('No match', '', ('delivery',)) is None


def test_system_reserved_tag_binding_has_priority(tmp_path):
    write(tmp_path / '.datacore/intents.org', node('system'))
    write(tmp_path / '.datacore/tags.yaml', 'domains:\n  reserved: {intent: system}\n')
    selected = space(tmp_path, 'alpha', 'alpha')
    write(selected / '.datacore/tags.yaml', 'domains:\n  reserved: {intent: goal}\n')
    assert IntentGraph.load(tmp_path).match('Shipping', 'alpha', ('reserved',)).id == 'system'


def test_structured_dip_registry_is_read_without_dropping_intent_entries(tmp_path):
    selected = space(tmp_path, 'alpha', 'alpha')
    write(selected / '.datacore/tags.yaml', 'version: 1\nspace: alpha\ntags:\n  domain:\n    - id: delivery\n      intent: goal\n')
    matched = IntentGraph.load(tmp_path).match('Plain task', 'alpha', ('delivery',))
    assert matched is not None and matched.id == 'alpha:goal'


def test_missing_explicit_intent_cannot_be_replaced_by_keyword_guess(tmp_path):
    selected = space(tmp_path, 'alpha', 'alpha')
    write(selected / 'org/next_actions.org', '* NEXT Shipping\n:PROPERTIES:\n:INTENT: missing\n:END:\n')
    result = place(tmp_path, IntentGraph.load(tmp_path))
    assert result['by_method']['none'] == 1
    assert result['index'] == {}


def test_keyword_guess_cannot_move_task_to_an_unrelated_space(tmp_path):
    selected = space(tmp_path, 'alpha', 'alpha')
    space(tmp_path, 'beta', 'beta')
    write(selected / 'org/intents.org', node(title='Different'))
    assert IntentGraph.load(tmp_path).match('Shipping', 'alpha') is None


@pytest.mark.parametrize('bad', [node() + node(), node('a', extra=':SERVES: b\n') + node('b', extra=':SERVES: a\n'),
                               node(extra=':SERVES: goal\n'), node(extra=':SWITCH: offf\n')])
def test_ambiguous_or_unsafe_graph_is_refused(tmp_path, bad):
    write(tmp_path / '.datacore/intents.org', bad)
    with pytest.raises(ValueError):
        IntentGraph.load(tmp_path)


@pytest.mark.parametrize('bad', ['spotlight: [', 'spotlight: []\nspotlight: [ghost]\n',
                               'spotlight: [{rank: -1}]\n', 'spotlight: [{keywords: string}]\n'])
def test_invalid_spotlight_is_not_silently_neutral(tmp_path, bad):
    write(tmp_path / '.datacore/cos/priorities.yaml', bad)
    with pytest.raises(ValueError):
        IntentGraph.load(tmp_path)


def test_review_and_retry_states_still_count_as_unfinished_work(tmp_path):
    selected = space(tmp_path, 'alpha', 'alpha')
    write(selected / 'org/next_actions.org', ''.join(
        f'* {state} Plain task\n:PROPERTIES:\n:INTENT: goal\n:END:\n'
        for state in ['TODO', 'NEXT', 'WAITING', 'REVIEW', 'QUEUED', 'WORKING', 'FAILED', 'DONE', 'CANCELLED', 'DEFERRED']))
    result = place(tmp_path, IntentGraph.load(tmp_path))
    assert result['total'] == 7
    assert result['index'] == {'alpha:goal': 7}


def test_duplicate_space_identity_cannot_silently_merge_graphs(tmp_path):
    space(tmp_path, 'first', 'same')
    space(tmp_path, 'second', 'same')
    with pytest.raises(ValueError):
        IntentGraph.load(tmp_path)


def test_marked_installation_does_not_steal_global_references(tmp_path):
    selected = space(tmp_path, '.', 'installation')
    write(selected / '.datacore/intents.org', node())
    graph = IntentGraph.load(tmp_path)
    assert graph.resolve_id('goal') == 'goal'
    assert graph.resolve_id('@root:goal', 'installation') == 'goal'


def test_unresolved_or_redundant_crosslinks_do_not_inflate_priority(tmp_path):
    write(tmp_path / '.datacore/intents.org', node('goal') + node('broken', extra=':SERVES: missing\n'))
    graph = IntentGraph.load(tmp_path)
    assert not graph.is_high_leverage('broken')
    write(tmp_path / '.datacore/intents.org', node('goal') + '** Child\n:PROPERTIES:\n:INTENT_ID: child\n:LEVEL: 2\n:SERVES: goal\n:END:\n')
    graph = IntentGraph.load(tmp_path)
    assert not graph.is_high_leverage('child')


def test_declared_custom_todo_states_remain_visible(tmp_path):
    selected = space(tmp_path, 'alpha', 'alpha')
    write(selected / 'org/next_actions.org', '#+SEQ_TODO: VERIFY | ARCHIVED\n* VERIFY Shipping\n* ARCHIVED Shipping\n')
    assert place(tmp_path, IntentGraph.load(tmp_path))['total'] == 1
