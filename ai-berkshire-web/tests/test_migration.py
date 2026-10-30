import sqlite3
from pathlib import Path
import zipfile

from server import auth, accounts, data_migrate as migration
from conftest import member


def configure(monkeypatch):
    monkeypatch.setattr(migration,'APP_DB',auth.DB_PATH)


def test_export_includes_committed_wal_records(env,tmp_path,monkeypatch):
    configure(monkeypatch)
    user=member()
    conn=auth.connect()
    conn.execute("INSERT INTO history VALUES (?,?,?,?,?,?,?,?,?,?)",('wal-record',user['user']['id'],'test','test','','preserved','demo','demo',accounts.now(),9))
    conn.commit()
    backup=tmp_path/'export.zip'
    assert migration.export_data(backup)['ok']
    with zipfile.ZipFile(backup) as zf:
        zf.extract('app.db',tmp_path/'restored')
    saved=sqlite3.connect(str(tmp_path/'restored'/'app.db'))
    assert saved.execute("SELECT content FROM history WHERE id='wal-record'").fetchone()[0]=='preserved'
    saved.close();conn.close()


def test_restore_preserves_accounts_policy_and_revokes_sessions(env,tmp_path,monkeypatch):
    configure(monkeypatch)
    user=member()
    accounts.save_settings({'free_quota':12})
    archive=tmp_path/'backup.zip'
    assert migration.export_data(archive)['ok']
    auth.register('later-user','test-member-password')
    result=migration.import_data(archive)
    assert result['ok'] and result['requires_login']
    assert Path(result['backup_path']).is_file()
    assert auth.get_user_by_token(user['token']) is None
    assert auth.login('member','test-member-password')
    assert auth.login('later-user','test-member-password') is None
    assert accounts.settings()['free_quota']==12


def test_restore_rejects_running_research_and_invalid_database(env,tmp_path,monkeypatch):
    configure(monkeypatch)
    user=member()
    archive=tmp_path/'backup.zip'
    migration.export_data(archive)
    accounts.reserve(user['user']['id'],'research')
    assert not migration.import_data(archive)['ok']
    bad=tmp_path/'invalid.zip'
    with zipfile.ZipFile(bad,'w') as zf:zf.writestr('app.db','not a sqlite database')
    assert not migration.import_data(bad)['ok']
    assert auth.get_user_by_token(user['token'])
