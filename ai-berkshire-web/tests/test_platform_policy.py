import json

import pytest

from conftest import headers, member
from server import accounts, auth, platform_policy as policy, settings_store
from server import ensure_framework


@pytest.fixture
def custom_policy(tmp_path, monkeypatch):
    path = tmp_path / 'platform.json'
    path.write_text(json.dumps({
        'membership_tiers': {'basic': {'label': '基础团队', 'quota': 2},
                             'team': {'label': '协作团队', 'quota': 7}},
        'default_tier': 'basic', 'timezone': 'UTC', 'session_days': 7,
        'invite_expiry_hours': 24, 'invite_daily_limit': 3,
        'usage_actions': {'research': '生成任务'},
    }), encoding='utf-8')
    monkeypatch.setenv('VC_PLATFORM_CONFIG', str(path))
    policy.configuration.cache_clear()
    yield path
    policy.configuration.cache_clear()


def test_custom_membership_ids_and_quotas_are_not_tied_to_research(env, custom_policy):
    client, root = env
    user = member()
    uid = user['user']['id']
    assert user['user']['tier'] == 'basic'
    assert accounts.quota_info(uid)['quota'] == 2
    assert accounts.quota_info(uid)['timezone'] == 'UTC'
    assert accounts.quota_info(uid)['resets_at'].endswith('+00:00')
    public = client.get('/api/auth/policy').json()['platform']
    assert set(public['membership_tiers']) == {'basic', 'team'}
    assert public['metering_unit'] == 'request' and 'tzinfo' not in public
    assert client.put(f'/api/admin/users/{uid}', headers=headers(root), json={'tier': 'team'}).status_code == 200
    response = client.put('/api/admin/account-policy', headers=headers(root), json={'quotas': {'team': 3}})
    assert response.status_code == 200
    assert accounts.quota_info(uid)['quota'] == 3
    assert client.put('/api/admin/account-policy', headers=headers(root), json={'quotas': {'unknown': 1}}).status_code == 422
    assert client.put('/api/admin/account-policy', headers=headers(user), json={'quotas': {'team': 0}}).status_code == 403
    reserved = accounts.reserve(uid, 'generic-task')
    assert accounts.quota_info(uid)['used'] == 1
    accounts.finish(reserved, False)
    assert accounts.quota_info(uid)['used'] == 0


@pytest.mark.parametrize('value', [
    {'api_key': 'not-allowed'},
    {'membership_tiers': {'invalid id': {'label': 'Invalid', 'quota': 1}}},
    {'membership_tiers': {'free': {'label': 'Free', 'quota': True}}},
    {'timezone': 'not/a-timezone'},
    {'session_days': 0},
])
def test_platform_configuration_rejects_invalid_or_secret_fields(custom_policy, value):
    custom_policy.write_text(json.dumps(value), encoding='utf-8')
    policy.configuration.cache_clear()
    with pytest.raises(ValueError):
        policy.configuration()


def test_fresh_model_defaults_are_empty_and_existing_configuration_is_preserved(env):
    _, root = env
    cfg = settings_store.get_active_config(root['user']['id'])
    assert cfg['provider'] == cfg['provider_preset'] == 'demo'
    assert cfg['api_key'] == cfg['base_url'] == cfg['model'] == ''
    payload = {'provider': 'openai', 'base_url': 'https://model.example.invalid/v1',
               'model': 'custom-model', 'api_key': 'synthetic-model-key', 'name': 'Saved connection'}
    settings_store.update_config(root['user']['id'], cfg['id'], payload)
    auth.init_db()
    saved = settings_store.get_active_config(root['user']['id'])
    assert all(saved[key] == value for key, value in payload.items())


def test_new_bootstrap_name_does_not_replace_existing_admin(env, monkeypatch):
    _, root = env
    before = auth.list_users()
    monkeypatch.setattr(auth, 'ADMIN_USERNAME', 'another-bootstrap-name')
    monkeypatch.setattr(auth, 'ADMIN_PASSWORD', 'another-bootstrap-password')
    auth.init_db()
    assert auth.list_users() == before
    assert accounts.root_admin_id() == root['user']['id']


def test_no_automatic_framework_download_or_report_export(env, tmp_path, monkeypatch):
    client, root = env
    monkeypatch.setattr(ensure_framework, 'AI_BERKSHIRE', tmp_path / 'absent-framework')
    monkeypatch.delenv('VC_AUTO_DOWNLOAD_FRAMEWORK', raising=False)
    monkeypatch.setattr(ensure_framework.subprocess, 'run', lambda *a, **k: pytest.fail('unexpected network request'))
    assert ensure_framework.ensure_framework()['action'] == 'disabled'
    assert client.post('/api/admin/sync-reports', headers=headers(root)).status_code == 410
