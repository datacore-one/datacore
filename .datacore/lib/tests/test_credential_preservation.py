from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import stat

import pytest
from credential_store import adopt_oauth_token
from creds import RotationIndex, RotationEntry


def test_adoption_preserves_exact_original_refresh_chain_and_unknown_fields(tmp_path):
    path = tmp_path/'credentials.json'
    before = b'{ "claudeAiOauth": {"accessToken":"old", "refreshToken":"irreplaceable", "expiresAt":123, "extra":"keep"}, "other":{"keep":true}}'
    path.write_bytes(before)
    assert adopt_oauth_token(path, 'new') == 'old'
    assert path.with_suffix('.json.prev').read_bytes() == before
    backups = list(path.with_name(path.name+'.backups').glob('*.json'))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    after = json.loads(path.read_text())
    assert after['other'] == {'keep': True} and after['claudeAiOauth']['extra'] == 'keep'
    assert after['claudeAiOauth']['accessToken'] == 'new'
    assert 'refreshToken' not in after['claudeAiOauth'] and 'expiresAt' not in after['claudeAiOauth']
    for file in [path, path.with_suffix('.json.prev'), *backups]:
        assert stat.S_IMODE(file.stat().st_mode) == 0o600


@pytest.mark.parametrize('before', [b'{', b'[]', b'null', b'{"claudeAiOauth":null}'])
def test_corrupt_credential_store_is_never_reinitialized(tmp_path, before):
    path=tmp_path/'credentials.json'; path.write_bytes(before)
    with pytest.raises(ValueError):
        adopt_oauth_token(path, 'candidate')
    assert path.read_bytes() == before


def test_failed_backup_preserves_current_credential(tmp_path, monkeypatch):
    import credential_store
    path=tmp_path/'credentials.json'; before=b'{"other":"preserve"}'; path.write_bytes(before)
    monkeypatch.setattr(credential_store, 'atomic_write_text', lambda *a: (_ for _ in ()).throw(OSError('full')))
    with pytest.raises(OSError):
        adopt_oauth_token(path, 'candidate')
    assert path.read_bytes() == before


def test_concurrent_adoptions_retain_every_outgoing_version(tmp_path):
    path=tmp_path/'credentials.json'
    path.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'original', 'refreshToken': 'original-refresh'}}))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: adopt_oauth_token(path, f'candidate-{n}'), range(8)))
    tokens={json.loads(p.read_text())['claudeAiOauth']['accessToken'] for p in path.with_name(path.name+'.backups').glob('*.json')}
    tokens.add(json.loads(path.read_text())['claudeAiOauth']['accessToken'])
    assert tokens == {'original', *(f'candidate-{n}' for n in range(8))}


def test_rotation_save_refuses_stale_state_and_preserves_unknown_fields(tmp_path):
    path=tmp_path/'rotation.yaml'
    path.write_text('version: "1.0"\ncustom: keep\ncredentials:\n  - env_var: KEY\n    provider: example\n    custom_entry: preserve\n')
    first, stale = RotationIndex(path), RotationIndex(path)
    first.entries[0].status='active'; first.save()
    saved=path.read_bytes()
    stale.entries[0].status='expired'
    with pytest.raises(ValueError, match='changed'):
        stale.save()
    assert path.read_bytes() == saved
    assert b'custom: keep' in saved and b'custom_entry: preserve' in saved


def test_candidate_verification_failure_never_displaces_existing_store(tmp_path, monkeypatch):
    import io
    import sys
    import types
    from creds import CredentialManager
    path=tmp_path/'credential.json'; path.write_text('{"keep":"original"}')
    manager=CredentialManager(str(tmp_path))
    access=types.SimpleNamespace(_entry=lambda *a: {}, instance_name=lambda: 'local',
        _store_for=lambda entry: 'json:'+str(path), verify_value=lambda *a: ('FAIL','rejected'))
    monkeypatch.setattr(manager, '_access', lambda: access)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('candidate-token'))
    assert manager.cmd_adopt_token() == 1
    assert path.read_text() == '{"keep":"original"}'


def _secret_files(tmp_path):
    import yaml
    root=tmp_path/'secrets'; root.mkdir()
    env=root/'global.env'; env.write_text('# retained\nOLD_KEY=original\n')
    index=root/'credential-index.yaml'; index.write_text(yaml.safe_dump({'version': '1.0', 'credentials': [], 'custom': 'keep'}))
    return root, env, index


def test_new_credential_quotes_round_trip_without_shell_execution(tmp_path):
    import subprocess
    import credential_access
    from credential_store import add_credential
    root, env, index=_secret_files(tmp_path)
    sentinel=tmp_path/'must-not-exist'
    value="space ' quote ; $(touch "+str(sentinel)+") # literal"
    before=env.read_bytes()
    add_credential(root, env, {'id':'new','var_name':'TEST_KEY'}, value)
    assert env.read_bytes().startswith(before)
    assert credential_access._read_var(env, 'TEST_KEY') == value
    result=subprocess.run(['/bin/sh','-c','. "$1"; printf %s "$TEST_KEY"','test',str(env)],capture_output=True,text=True,timeout=5)
    assert result.returncode == 0 and result.stdout == value
    assert not sentinel.exists()


def test_failed_index_publication_rolls_back_new_secret(tmp_path, monkeypatch):
    import org_transaction
    from credential_store import add_credential
    root, env, index=_secret_files(tmp_path)
    before=(env.read_bytes(),index.read_bytes())
    real=org_transaction.atomic_write_text
    def fail(path, content):
        if Path(path) == index:
            raise OSError('index full')
        return real(path, content)
    monkeypatch.setattr(org_transaction, 'atomic_write_text', fail)
    with pytest.raises(OSError, match='full'):
        add_credential(root, env, {'id':'new','var_name':'NEW_KEY'}, 'candidate')
    assert (env.read_bytes(),index.read_bytes()) == before


def test_invalid_index_duplicate_or_escape_cannot_append_secret(tmp_path):
    from credential_store import add_credential
    root, env, index=_secret_files(tmp_path)
    before=env.read_bytes(); index.write_text('credentials: null\n')
    with pytest.raises(ValueError):
        add_credential(root, env, {'id':'new','var_name':'NEW_KEY'}, 'candidate')
    assert env.read_bytes() == before
    outside=tmp_path/'outside.env'; outside.write_text('preserve')
    with pytest.raises(ValueError, match='escapes'):
        add_credential(root, outside, {'id':'new','var_name':'NEW_KEY'}, 'candidate')
    assert outside.read_text() == 'preserve'


def test_credential_probes_never_follow_redirects_or_expose_error_content(monkeypatch):
    import io
    import urllib.error
    import urllib.request
    import secret_http
    import credential_access
    from unittest.mock import Mock
    captured=[]
    opener=Mock()
    monkeypatch.setattr(urllib.request,'build_opener',lambda *handlers: captured.extend(handlers) or opener)
    secret_http.urlopen(urllib.request.Request('https://example.test/',headers={'Authorization':'Bearer SYNTHETIC'}),timeout=1)
    assert captured[0].redirect_request(None,None,None,None,None,None) is None
    assert captured[1].proxies == {}
    def failure(*a, **kw):
        raise urllib.error.HTTPError('https://example.test/SYNTHETIC',401,'SYNTHETIC',{},io.BytesIO(b'SYNTHETIC'))
    monkeypatch.setattr(credential_access,'_secret_urlopen',failure)
    state,detail=credential_access.verify_value('GH_TOKEN','SYNTHETIC')
    assert state == 'FAIL' and detail == 'HTTP 401'


def test_shared_env_parser_retains_shell_quoted_literals(tmp_path):
    import shlex
    from env_utils import parse_env_file
    import ask_models
    value="literal ' quote and $(no-execution)"
    path=tmp_path/'env'; path.write_text('export TEST_KEY='+shlex.quote(value)+'\n')
    assert parse_env_file(path) == {'TEST_KEY':value}


def test_parallel_credential_adds_preserve_all_rows_and_values(tmp_path):
    from credential_store import add_credential
    from env_utils import parse_env_file
    import yaml
    root,env,index=_secret_files(tmp_path)
    def add(n):
        return add_credential(root,env,{'id':f'key{n}','var_name':f'KEY_{n}'},f'value-{n}')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(add, range(8)))
    assert len(yaml.safe_load(index.read_text())['credentials']) == 8
    assert parse_env_file(env) == {'OLD_KEY':'original', **{f'KEY_{n}':f'value-{n}' for n in range(8)}}


@pytest.mark.parametrize('commit_fails',[False,True])
def test_credential_commit_never_includes_unrelated_staged_files(tmp_path,commit_fails):
    import subprocess
    import yaml
    from creds import CredentialManager
    root=tmp_path/'.datacore/secrets';root.mkdir(parents=True)
    env=root/'global.env';env.write_text('OLD=keep\n')
    index=root/'credential-index.yaml';index.write_text(yaml.safe_dump({'credentials':[]}))
    def git(*args):
        return subprocess.run(['git','-c','core.hooksPath=/dev/null','-C',str(root),*args],check=True,capture_output=True,text=True)
    git('init','-q');git('config','user.email','audit@example.invalid');git('config','user.name','Synthetic audit')
    unrelated=root/'unrelated.txt';unrelated.write_text('original')
    git('add','.');git('commit','-qm','baseline')
    before=git('rev-parse','HEAD').stdout
    unrelated.write_text('staged unrelated edit');git('add','unrelated.txt')
    if commit_fails:
        # Git refuses the new commit while preserving files and staged work.
        (root/'.git/HEAD.lock').write_text('held by synthetic test')
    result=CredentialManager(str(tmp_path)).cmd_add('new',var_name='NEW_KEY',value='synthetic',scope='global',tier='medium',category='test',provider='test',description='synthetic test credential')
    assert result == (1 if commit_fails else 0)
    if commit_fails:
        assert git('rev-parse','HEAD').stdout == before
    else:
        assert set(git('diff-tree','--no-commit-id','--name-only','-r','HEAD').stdout.splitlines()) == {'global.env','credential-index.yaml'}
        assert git('show','HEAD:unrelated.txt').stdout == 'original'
    assert 'unrelated.txt' in git('diff','--cached','--name-only').stdout
    assert 'NEW_KEY=synthetic' in env.read_text()
    assert yaml.safe_load(index.read_text())['credentials'][0]['id'] == 'new'
