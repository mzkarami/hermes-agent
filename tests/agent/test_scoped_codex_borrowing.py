"""Explicit custom-home Codex borrowing; only synthetic credentials."""
import json
import time
from dataclasses import replace
import pytest

@pytest.fixture
def stores(tmp_path, monkeypatch):
    import hermes_cli.auth as auth
    import hermes_cli.config as config
    root = tmp_path / 'shared' / 'auth.json'
    local = tmp_path / 'company' / 'auth.json'
    for p in (root, local):
        p.parent.mkdir()
    row = dict(id='codex1', source='manual:device_code', auth_type='oauth', priority=0,
               access_token='synthetic-old', refresh_token='synthetic-refresh',
               expires_at_ms=int((time.time()+3600)*1000))
    root.write_text(json.dumps({'providers': {'nous': {'access_token': 'unrelated'}},
                               'credential_pool': {'openai-codex': [row], 'nous': [{'id':'secret'}]}}))
    local.write_text(json.dumps({'providers': {'local': {'value': 'keep'}}, 'credential_pool': {}}))
    monkeypatch.setenv('HERMES_HOME', str(local.parent))
    monkeypatch.setattr(auth, '_global_auth_file_path', lambda: None)
    monkeypatch.setattr(config, 'load_config_readonly', lambda: {'auth': {'shared_codex_auth_file': str(root)}})
    return root, local, row

def test_scope_and_native_selection(stores):
    from hermes_cli import auth
    from agent.credential_pool import load_pool
    root, local, row = stores
    before = local.read_text()
    assert auth._global_auth_file_path() is None
    assert auth._provider_fallback_auth_file_path('nous') is None
    assert auth.read_credential_pool('nous') == []
    assert set(auth.read_credential_pool()) == {'openai-codex'}
    assert auth._load_provider_state_with_source({}, 'nous') == (None, None)
    pool = load_pool('openai-codex')
    assert pool._borrowed_root_ids == {'codex1'}
    assert pool.select().access_token == row['access_token']
    assert local.read_text() == before

def test_local_override(stores):
    from hermes_cli import auth
    root, local, row = stores
    local.write_text(json.dumps({'credential_pool': {'openai-codex':[dict(row,id='local')]}}))
    assert auth.read_credential_pool('openai-codex')[0]['id'] == 'local'

def test_mock_refresh_writes_only_shared_pool(stores, monkeypatch):
    from agent.credential_pool import load_pool, CredentialPool
    root, local, row = stores
    before = local.read_text()
    pool = load_pool('openai-codex')
    calls=[]
    def rotate(self, entry):
        calls.append(entry.refresh_token)
        return replace(entry, access_token='synthetic-new', refresh_token='synthetic-next',
                       expires_at_ms=int((time.time()+7200)*1000))
    monkeypatch.setattr(CredentialPool, '_post_tokens_refresh', rotate)
    result = pool._refresh_entry(pool.entries()[0], force=True)
    assert result.access_token == 'synthetic-new'
    assert calls == ['synthetic-refresh']
    disk=json.loads(root.read_text())
    assert disk['credential_pool']['openai-codex'][0]['refresh_token'] == 'synthetic-next'
    assert disk['providers'] == {'nous': {'access_token':'unrelated'}}
    assert disk['credential_pool']['nous'] == [{'id':'secret'}]
    assert local.read_text() == before
    assert load_pool('openai-codex').select().access_token == 'synthetic-new'

def test_codex_write_hook_does_not_create_nous_handoff(stores, monkeypatch):
    from hermes_cli import auth, guest_handoff
    root, local, row = stores
    before_local = local.read_text()
    before_root = json.loads(root.read_text())
    calls = []
    real_hook = guest_handoff.note_store_write

    def observe(store, provider, state):
        calls.append(provider)
        return real_hook(store, provider, state)

    def unexpected_handoff(*args, **kwargs):
        pytest.fail('Codex borrowing must not trigger a Nous handoff')

    monkeypatch.setattr(auth, 'note_store_write', observe)
    monkeypatch.setattr(guest_handoff, 'note_replacement', unexpected_handoff)
    data = json.loads(root.read_text())
    data['providers']['openai-codex'] = {'tokens': {'access_token': 'synthetic'}}
    root.write_text(json.dumps(data))
    with auth._provider_state_transaction('openai-codex') as (store, state, source):
        assert isinstance(state, dict)
        state['tokens']['access_token'] = 'hook-rotated'
        auth._save_provider_state_to_source(store, 'openai-codex', state, source)
    after = json.loads(root.read_text())
    assert calls and set(calls) == {'openai-codex'}
    assert after['providers']['openai-codex']['tokens']['access_token'] == 'hook-rotated'
    assert after['providers']['nous'] == before_root['providers']['nous']
    assert after['credential_pool'] == before_root['credential_pool']
    assert local.read_text() == before_local


def test_singleton_write_through(stores):
    from hermes_cli import auth
    root, local, row = stores
    data=json.loads(root.read_text());data['providers']['openai-codex']={'tokens':{'access_token':'synthetic'}}
    root.write_text(json.dumps(data));before=local.read_text()
    with auth._provider_state_transaction('openai-codex') as (store,state,source):
        assert source == root.resolve()
        state['tokens']['access_token']='rotated'
        auth._save_provider_state_to_source(store,'openai-codex',state,source)
    assert json.loads(root.read_text())['providers']['openai-codex']['tokens']['access_token']=='rotated'
    assert local.read_text()==before

@pytest.mark.parametrize('value', ['relative/auth.json','/absolute/wrong.json', False])
def test_reject_invalid_optin(stores, monkeypatch, value):
    from hermes_cli import auth, config
    monkeypatch.setattr(config,'load_config_readonly',lambda:{'auth':{'shared_codex_auth_file':value}})
    with pytest.raises(ValueError):
        auth._provider_fallback_auth_file_path('openai-codex')
    assert auth._provider_fallback_auth_file_path('nous') is None

def test_quota_probe_refresh_writes_owner(stores, monkeypatch):
    from hermes_cli import auth_codex
    root, local, row = stores
    before=local.read_text()
    calls=[]
    def refresh(access, token):
        calls.append((access,token))
        return {'access_token':'probe-new','refresh_token':'probe-next'}
    monkeypatch.setattr(auth_codex,'_refresh_expired_codex_probe_token',refresh)
    monkeypatch.setattr(auth_codex,'_probe_codex_quota_restored',lambda token, **kw: token=='probe-new')
    assert auth_codex._probe_codex_pool_entry_quota_restored(row) is True
    assert calls==[('synthetic-old','synthetic-refresh')]
    assert json.loads(root.read_text())['credential_pool']['openai-codex'][0]['refresh_token']=='probe-next'
    assert local.read_text()==before


def test_no_optin_no_expansion(stores, monkeypatch):
    from hermes_cli import auth, config
    monkeypatch.setattr(config,'load_config_readonly',lambda:{})
    assert auth.read_credential_pool('openai-codex') == []
    assert auth._provider_fallback_auth_file_path('openai-codex') is None
