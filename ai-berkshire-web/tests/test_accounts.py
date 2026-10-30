from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import sqlite3

import pytest
from server import auth, accounts, history_store as history
from conftest import headers, member


def test_root_identity_survives_restart_without_role_reset(env):
    client, root = env
    normal = member()
    auth.update_user(normal['user']['id'], is_disabled=True)
    auth.init_db()
    assert auth.login(auth.ADMIN_USERNAME, 'test-bootstrap-password')['user']['is_root_admin']
    assert 'error' in auth.login('member', 'test-member-password')
    assert auth.login(auth.ADMIN_USERNAME, 'wrong') is None


def test_fresh_database_requires_explicit_admin_password(env, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, 'DB_PATH', tmp_path / 'empty.db')
    monkeypatch.setattr(auth, 'ADMIN_PASSWORD', '')
    with pytest.raises(RuntimeError, match='VC_ADMIN_PASS'):
        auth.init_db()


def test_legacy_database_migration_preserves_users_password_and_history(env, tmp_path, monkeypatch):
    db = tmp_path / 'legacy.db'
    salt, hashed = auth._hash_password('unchanged-password')
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE,salt TEXT,password_hash TEXT,is_admin INTEGER,created_at TEXT)')
    conn.execute('INSERT INTO users VALUES (?,?,?,?,?,?)', ('old-admin', auth.ADMIN_USERNAME, salt, hashed, 1, accounts.now()))
    conn.execute('CREATE TABLE history(id TEXT PRIMARY KEY,user_id TEXT,skill_id TEXT,skill_title TEXT,arguments TEXT,content TEXT,provider TEXT,model TEXT,created_at TEXT,chars INTEGER)')
    conn.execute('INSERT INTO history VALUES (?,?,?,?,?,?,?,?,?,?)', ('old-report','old-admin','test','历史报告','公司','旧报告正文','demo','demo',accounts.now(),5))
    conn.commit();conn.close()
    monkeypatch.setattr(auth, 'DB_PATH', db)
    auth.init_db();auth.init_db()
    assert auth.login(auth.ADMIN_USERNAME, 'unchanged-password')['user']['is_root_admin']
    assert history.get_record('old-admin','old-report')['content'] == '旧报告正文'
    assert accounts.quota_info('old-admin')['used'] == 1
    assert len(accounts.usage_list('old-admin')) == 1


def test_quota_is_atomic_and_does_not_depend_on_history(env):
    uid = member()['user']['id']
    accounts.save_settings({'free_quota':1})
    def attempt(_):
        try:return accounts.reserve(uid, 'research')
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(bool(r) for r in results) == 1
    rid = next(r for r in results if r)
    accounts.finish(rid, True);accounts.finish(rid, False)
    assert accounts.quota_info(uid)['used'] == 1
    record = history.save_record(uid, skill_id='test', skill_title='test',arguments='',content='output',provider='demo',model='demo')
    history.delete_record(uid,record['id'])
    assert accounts.quota_info(uid)['used'] == 1


def test_no_output_failure_releases_quota_and_restart_recovers(env):
    uid = member()['user']['id']
    accounts.save_settings({'free_quota':1})
    accounts.finish(accounts.reserve(uid,'research'), False)
    assert accounts.quota_info(uid)['used'] == 0
    accounts.reserve(uid,'pipeline')
    accounts.recover_interrupted()
    assert accounts.quota_info(uid)['used'] == 0


def test_china_midnight_reset_and_admin_exemption(env, monkeypatch):
    _, root = env
    uid = member()['user']['id']
    accounts.save_settings({'free_quota':1})
    monkeypatch.setattr(accounts,'now',lambda:'2026-10-23T23:59:59+08:00')
    accounts.finish(accounts.reserve(uid,'memory'),True)
    assert not accounts.quota_info(uid)['ok']
    monkeypatch.setattr(accounts,'now',lambda:'2026-10-24T00:00:00+08:00')
    assert accounts.quota_info(uid)['used'] == 0
    assert accounts.quota_info(root['user']['id'])['quota'] == 0


def test_role_permissions_and_root_protection(env):
    client, root = env
    manager,normal = member('manager'),member()
    mid,uid=manager['user']['id'],normal['user']['id']
    assert client.get('/api/admin/users',headers=headers(normal)).status_code == 403
    assert client.put(f'/api/admin/users/{mid}',headers=headers(root),json={'is_admin':True}).status_code == 200
    assert client.put(f'/api/admin/users/{uid}',headers=headers(manager),json={'tier':'vip'}).status_code == 200
    for payload in ({'is_admin':True},{'is_admin':False}):
        assert client.put(f'/api/admin/users/{uid}',headers=headers(manager),json=payload).status_code == 403
    assert client.delete(f'/api/admin/users/{uid}',headers=headers(manager)).status_code == 403
    assert client.get('/api/admin/data/status',headers=headers(manager)).status_code == 403
    assert client.post(f'/api/admin/users/{root["user"]["id"]}/reset-password',headers=headers(manager),json={'new_password':'new-password-123'}).status_code == 403
    for payload in ({'is_admin':False},{'is_disabled':True}):
        assert client.put(f'/api/admin/users/{root["user"]["id"]}',headers=headers(root),json=payload).status_code == 400
    assert client.delete(f'/api/admin/users/{root["user"]["id"]}',headers=headers(root)).status_code == 400


def test_disabling_revokes_sessions_permanently(env):
    client, root = env
    normal=member();uid=normal['user']['id']
    auth.update_user(uid,is_disabled=True)
    assert client.get('/api/auth/me',headers=headers(normal)).status_code == 401
    auth.update_user(uid,is_disabled=False)
    assert client.get('/api/auth/me',headers=headers(normal)).status_code == 401
    assert auth.login('member','test-member-password')['token']


def test_password_change_validates_current_and_revokes_all_sessions(env):
    client,_=env
    normal=member();second=auth.login('member','test-member-password')
    endpoint='/api/account/password'
    assert client.put(endpoint,headers=headers(normal),json={'old_password':'wrong','new_password':'new-password-123'}).status_code == 400
    assert client.put(endpoint,headers=headers(normal),json={'old_password':'test-member-password','new_password':'new-password-123'}).status_code == 200
    for user in (normal,second):assert client.get('/api/auth/me',headers=headers(user)).status_code == 401
    assert auth.login('member','new-password-123')


def test_invites_single_use_atomic_and_duplicate_signup_rolls_back(env):
    client,root=env
    accounts.save_settings({'invite_required':True})
    code=accounts.generate_invite(root['user']['id'])['code']
    assert 'error' in auth.register(auth.ADMIN_USERNAME,'test-member-password',code)
    assert auth.register('first','test-member-password',code)['ok']
    assert 'error' in auth.register('second','test-member-password',code)
    assert 'error' in auth.register('third','test-member-password','invalid')
    assert accounts.list_invites(root['user']['id'])[0]['used']
    assert client.get('/api/auth/policy').json()['invite_required'] is True


def test_expired_invite_not_consumed_and_invites_scoped(env):
    _,root=env
    other=member()
    code=accounts.generate_invite(root['user']['id'])['code']
    with accounts.transaction() as conn:conn.execute('UPDATE invite_codes SET expires_at=? WHERE code=?',('2000-01-01T00:00:00+08:00',code))
    accounts.save_settings({'invite_required':True})
    assert 'error' in auth.register('newcomer','test-member-password',code)
    assert accounts.list_invites(other['user']['id']) == []


@pytest.mark.parametrize('value',[-1,1.5,True,10001,'20'])
def test_invalid_policy_rejected(env,value):
    client,root=env
    payload={**accounts.DEFAULTS,'free_quota':value}
    assert client.put('/api/admin/account-policy',headers=headers(root),json=payload).status_code == 422


def test_personal_records_configs_and_usage_isolated(env):
    client,root=env
    normal=member();uid=normal['user']['id']
    rec=history.save_record(uid,skill_id='test',skill_title='test',arguments='',content='private history',provider='demo',model='demo')
    assert client.get('/api/history/'+rec['id'],headers=headers(root)).status_code == 404
    assert client.delete('/api/history/'+rec['id'],headers=headers(root)).status_code == 404
    account=client.post('/api/settings',headers=headers(normal),json={'name':'private','api_key':'private-key-test'}).json()
    assert 'api_key' not in account
    assert all(c['id']!=account['id'] for c in client.get('/api/settings',headers=headers(root)).json()['configs'])
    assert client.put('/api/settings/'+account['id'],headers=headers(root),json={'name':'overwrite'}).status_code == 404
    accounts.finish(accounts.reserve(uid,'research'),True)
    assert client.get('/api/account/usage',headers=headers(root)).json()['items'] == []
