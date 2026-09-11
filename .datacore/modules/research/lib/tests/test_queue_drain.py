import importlib.util, pathlib, sys, types
LIB = pathlib.Path(__file__).resolve().parents[1]
# The orchestrator imports the agent SDK at module level; the queue logic under
# test does not need it. Stub it so the module loads in a plain test env.
from unittest.mock import MagicMock
for name in ("claude_agent_sdk", "claude_agent_sdk.types"):
    if name not in sys.modules:
        sys.modules[name] = MagicMock()
spec = importlib.util.spec_from_file_location("ro", LIB / "research_orchestrator.py")
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)


def test_unfetchable_item_is_parked_after_three_attempts(tmp_path, monkeypatch):
    org = tmp_path / "research_learning.org"
    heading = "** TODO Read: paywalled thing (Bloomberg)"
    org.write_text("* Queue\n" + heading + "\n    :PROPERTIES:\n    :URL: https://x\n    :END:\n** TODO Read: another\n")
    monkeypatch.setattr(R, "RESEARCH_ORG", org)
    item = {"heading_line": heading, "title": "Read: paywalled thing", "url": "https://x"}
    assert R.note_fetch_failure(item) == 1
    assert R.note_fetch_failure(item) == 2
    assert "** TODO Read: paywalled thing" in org.read_text(), "two failures keep it TODO"
    assert R.note_fetch_failure(item) == 3
    text = org.read_text()
    assert "** WAITING Read: paywalled thing (Bloomberg)" in text
    assert ":RESULT: unfetchable after 3 attempts" in text
    assert ":FETCH_ATTEMPTS: 3" in text
    assert "** TODO Read: another" in text, "the neighbour is untouched"


def test_malformed_model_reply_reports_failure_without_name_error(monkeypatch):
    class Result:
        is_error = False
        result = "not JSON, and not repairable"
    async def query(**kwargs):
        yield Result()
    messages = []
    monkeypatch.setattr(R, "_sdk_query", query)
    monkeypatch.setattr(R, "_SdkResultMessage", Result)
    monkeypatch.setattr(R, "log", messages.append)
    assert R._claude_json("test", 1, "test") is None
    assert any("reply was not JSON" in message for message in messages)


def test_research_analysis_disables_tools_and_ambient_customizations(monkeypatch):
    options = {}
    def capture_options(**kwargs):
        options.update(kwargs)
        return kwargs
    class Result:
        is_error = False
        result = '{"result": "text"}'
    async def query(**kwargs):
        yield Result()
    monkeypatch.setattr(R, 'ClaudeAgentOptions', capture_options)
    monkeypatch.setattr(R, '_sdk_query', query)
    monkeypatch.setattr(R, '_SdkResultMessage', Result)
    assert R._claude_json('untrusted document', 1, 'test') == {'result': 'text'}
    assert options['tools'] == [] and options['mcp_servers'] == {}
    assert options['permission_mode'] == 'dontAsk'
    assert options['setting_sources'] == []
    assert 'strict-mcp-config' in options['extra_args']
    assert 'safe-mode' in options['extra_args']
    assert 'no-session-persistence' in options['extra_args']


def test_entity_drafts_stay_personal_and_frontmatter_cannot_be_injected(tmp_path, monkeypatch):
    import yaml
    personal = tmp_path / 'personal'
    shared = tmp_path / 'shared'
    (shared / '3-knowledge/reference/companies').mkdir(parents=True)
    shared_people = shared / 'people'
    shared_people.mkdir()
    monkeypatch.setattr(R, 'DATAFUND', shared)
    monkeypatch.setattr(R, 'COMPANIES_DIR', personal / 'companies')
    monkeypatch.setattr(R, 'PEOPLE_DIR_PERSONAL', personal / 'people')
    monkeypatch.setattr(R, 'PEOPLE_DIR_DF', shared_people)
    company = {'name': 'Example "draft"\nstatus: approved', 'category': 'fintech', 'website': 'https://example.test/\nprivate: true'}
    person = {'name': 'Example Person', 'role': 'role"\nstatus: approved', 'organization': 'organization'}
    company_path = R.write_companies([company], 'https://example.test/')[0]
    person_path = R.write_people([person], 'https://example.test/')[0]
    for path, values in [(company_path, company), (person_path, person)]:
        assert path.is_relative_to(personal)
        metadata = yaml.safe_load(path.read_text().split('---', 2)[1])
        assert metadata['status'] == 'draft'
        assert metadata['name'] == values['name']
    assert not list(shared.rglob('*.md'))


def test_news_actions_cannot_inject_structure_and_preserve_existing_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(R, 'DATA_DIR', tmp_path)
    inbox = tmp_path / 'inbox.org'
    before = '* Inbox\r\n** TODO Existing\r\n:PROPERTIES:\r\n:ID: existing\r\n:END:\r\n'
    inbox.write_bytes(before.encode())
    R.append_news_actions(inbox, [{'title': 'Review\r\n* DONE injected', 'context': '* TODO forged\n#+CALL: run'}], tmp_path / 'brief.md')
    result = inbox.read_bytes().decode()
    assert result.startswith(before)
    assert '\n* DONE injected' not in result and '\n* TODO forged' not in result
    assert '\n#+CALL:' not in result
    assert ': * TODO forged' in result


def test_subscription_cookie_requires_exact_https_host(monkeypatch):
    monkeypatch.setenv('WSJ_COOKIES', 'session=private')
    for url in ['https://evil.example/wsj.com', 'https://wsj.com.evil.example/',
                'https://wsj.com@evil.example/', 'http://wsj.com/story']:
        assert R._cookies_for(url) is None
    assert R._cookies_for('https://www.wsj.com/story') == 'session=private'


def test_public_research_fetch_does_not_silently_use_url_proxies(monkeypatch):
    from unittest.mock import Mock
    monkeypatch.setattr(R, '_SETTINGS', {})
    monkeypatch.setenv('JINA_API_KEY', 'test-key')
    direct = Mock(return_value=None)
    proxy = Mock(side_effect=AssertionError('external proxy not authorized'))
    monkeypatch.setattr(R, '_fetch_direct', direct)
    monkeypatch.setattr(R, '_fetch_jina', proxy)
    monkeypatch.setattr(R, '_fetch_wayback', proxy)
    assert R.fetch_url('https://example.test/public') is None
    direct.assert_called_once()
    proxy.assert_not_called()


def test_research_publication_tracks_only_its_outputs_and_reports_failure(tmp_path, monkeypatch):
    from publication_manifest import PublicationManifest
    import subprocess
    repo = tmp_path / 'personal'; repo.mkdir()
    def git(*args):
        result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
        assert result.returncode == 0, result.stderr
        return result.stdout
    git('init', '-q', '-b', 'main')
    git('config', 'user.name', 'Audit'); git('config', 'user.email', 'audit@example.invalid')
    git('config', 'core.hooksPath', str(tmp_path / 'empty-hooks'))
    (repo / 'base').write_text('base'); git('add', 'base'); git('commit', '-qm', 'base')
    monkeypatch.setattr(R, 'PERSONAL', repo)
    messages = []; monkeypatch.setattr(R, 'log', messages.append)
    recorded = []
    class Capture(PublicationManifest):
        def publish(self, message, **kwargs):
            recorded.extend(self.paths)
            return super().publish(message, push=True)  # no remote: truthful failure after local commit
    monkeypatch.setattr(R, 'PublicationManifest', Capture)
    @R.track_publication
    def pipeline():
        R.create_note(repo / 'notes', 'own.md', 'own output')
        (repo / 'unrelated.md').write_text('unrelated private draft')
        git('add', 'unrelated.md')
    assert pipeline() == 1
    assert recorded == [repo / 'notes/own.md']
    assert git('diff', '--cached', '--name-only').strip() == b'unrelated.md'
    assert git('show', 'HEAD:notes/own.md') == b'own output'
    assert any('Publication failed' in message for message in messages)
    assert not any(message.startswith('Published ') for message in messages)
