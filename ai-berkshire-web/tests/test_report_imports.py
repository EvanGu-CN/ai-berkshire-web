from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from conftest import headers, member
from server import accounts, auth, history_store, report_imports as imports, reports_lib


def document(path='贵州茅台/investment-team-20261022.md', content='# 茅台投资研究\n\n合成测试报告。', **metadata):
    return {'path': path, 'content': content, 'metadata': metadata}


def post(client, user, docs, preview=False):
    return client.post('/api/reports/import' + ('/preview' if preview else ''),
                       headers=headers(user), json={'items': docs})


def test_preview_import_and_read_do_not_change_research_history_or_quota(env, monkeypatch):
    client, root = env
    user = member()
    uid = user['user']['id']
    monkeypatch.setattr(reports_lib, '_INDEX', [])
    monkeypatch.setattr(reports_lib, 'status', lambda: {'stale': False, 'built_at': ''})
    before_quota = accounts.quota_info(uid)['used']
    preview = post(client, user, [document()], preview=True).json()
    assert preview['counts']['ready'] == 1
    assert imports.list_reports() == []
    result = post(client, user, [document()]).json()
    assert result['counts']['imported'] == 1
    row = result['items'][0]
    listing = client.get('/api/reports?source=imported', headers=headers(root)).json()
    assert listing['total'] == 1 and listing['counts']['imported'] == 1
    item = listing['items'][0]
    assert item['group'] == '贵州茅台' and item['date'] == '2026-10-22'
    assert item['type'] == 'investment-team' and item['source'] == 'imported'
    assert item['author_id'] == uid and item['path'] == row['report_path']
    detail = client.get('/api/reports/content', params={'path': row['report_path']}, headers=headers(root)).json()
    assert detail['content'] == document()['content'] and detail['source_path'] == document()['path']
    assert client.get('/api/reports?source=imported&scope=mine', headers=headers(root)).json()['total'] == 0
    assert client.get('/api/reports?source=imported&scope=mine', headers=headers(user)).json()['total'] == 1
    assert client.get('/api/reports?source=imported&q=茅台', headers=headers(user)).json()['total'] == 1
    assert client.get('/api/reports?source=imported&group=贵州茅台&rtype=investment-team', headers=headers(user)).json()['total'] == 1
    assert client.get('/api/reports?source=generated', headers=headers(user)).json()['total'] == 0
    assert history_store.list_records(uid) == []
    assert accounts.quota_info(uid)['used'] == before_quota


def test_identical_body_deduplicates_across_paths_and_changed_body_creates_versions(env):
    client, root = env
    first = post(client, root, [document(), document('其他目录/同一报告.md')]).json()
    assert first['counts']['imported'] == 1 and first['counts']['duplicate'] == 1
    original_id = first['items'][0]['id']
    normalized = document(content='\ufeff# 茅台投资研究\r\n\r\n合成测试报告。\r\n')
    assert post(client, root, [normalized], preview=True).json()['counts']['duplicate'] == 1
    assert post(client, root, [normalized]).json()['counts']['duplicate'] == 1
    assert post(client, root, [document('改名后的目录/新名字.md', normalized['content'])]).json()['counts']['duplicate'] == 1
    windows_path = document(path=document()['path'].replace('/', '\\'))
    assert post(client, root, [windows_path]).json()['counts']['duplicate'] == 1
    assert post(client, root, [document(content='# 修订版本\n新的结论')]).json()['counts']['imported'] == 1
    assert len(imports.list_reports()) == 2
    assert imports.get_report(original_id)['content'] == document()['content']
    other = member()
    assert post(client, other, [document()]).json()['counts']['imported'] == 1


def test_same_company_day_type_and_title_keep_every_file_across_batches(env, monkeypatch):
    client, root = env
    monkeypatch.setattr(reports_lib, '_INDEX', [])
    monkeypatch.setattr(reports_lib, 'status', lambda: {'stale': False, 'built_at': ''})
    meta = {'title': '同日同类投研', 'group': '贵州茅台', 'type': 'investment-team', 'date': '2026-10-22'}
    docs = [document(f'贵州茅台/agent-{i}/investment-team-20261022.md',
                     f'# 同日同类研究\n\n第 {i} 份独立判断。', **meta)
            for i in range(3)]
    preview = post(client, root, docs, preview=True).json()
    assert preview['counts']['ready'] == 3 and not imports.list_reports()
    first = post(client, root, docs[:1]).json()
    second = post(client, root, docs[1:] + docs[:1]).json()
    assert first['counts']['imported'] == 1
    assert second['counts']['imported'] == 2 and second['counts']['duplicate'] == 1
    result = client.get('/api/reports', params={'source': 'imported', 'group': meta['group'],
                        'rtype': meta['type'], 'period': 'custom',
                        'date_from': meta['date'], 'date_to': meta['date']}, headers=headers(root)).json()
    assert result['total'] == 3 and result['counts']['imported'] == 3
    assert {item['source_path'] for item in result['items']} == {doc['path'] for doc in docs}
    assert len({item['path'] for item in result['items']}) == 3
    for item in result['items']:
        detail = client.get('/api/reports/content', params={'path': item['path']}, headers=headers(root)).json()
        original = next(doc for doc in docs if doc['path'] == item['source_path'])
        assert detail['content'] == original['content'] and detail['title'] == meta['title']
    assert post(client, root, docs, preview=True).json()['counts']['duplicate'] == 3


def _legacy_import_table(conn):
    # Exact legacy column order and body-only constraint, with any saved rows intact.
    conn.execute('CREATE TABLE legacy_imports (id TEXT PRIMARY KEY,user_id TEXT NOT NULL,'
                 'source_path TEXT NOT NULL,title TEXT NOT NULL,company TEXT NOT NULL,'
                 'report_type TEXT NOT NULL,report_date TEXT NOT NULL,content TEXT NOT NULL,'
                 'content_hash TEXT NOT NULL,imported_at TEXT NOT NULL,UNIQUE(user_id,content_hash))')
    conn.execute('INSERT INTO legacy_imports SELECT * FROM imported_reports')
    conn.execute('DROP TABLE imported_reports')
    conn.execute('ALTER TABLE legacy_imports RENAME TO imported_reports')
    conn.commit()


def test_legacy_unique_constraint_migrates_without_changing_existing_rows(env):
    from ops.continuity import snapshot, verify
    client, root = env
    saved = post(client, root, [document()]).json()['items'][0]
    with sqlite3.connect(auth.DB_PATH) as conn:
        _legacy_import_table(conn)
        before = conn.execute('SELECT * FROM imported_reports').fetchall()
    baseline = snapshot(auth.DB_PATH)
    auth.init_db()
    auth.init_db()  # Repeated starts must not rewrite rows or recreate the table.
    verify(auth.DB_PATH, baseline)
    with sqlite3.connect(auth.DB_PATH) as conn:
        assert conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert conn.execute('SELECT * FROM imported_reports').fetchall() == before
    assert imports.get_report(saved['id'])['content'] == document()['content']
    assert post(client, root, [document()]).json()['counts']['duplicate'] == 1
    assert post(client, root, [document('贵州茅台/other-agent.md')]).json()['counts']['duplicate'] == 1
    assert post(client, root, [document('贵州茅台/other-agent.md', '# 不同正文研究')]).json()['counts']['imported'] == 1
    assert len(imports.list_reports()) == 2


def test_legacy_constraint_migration_rolls_back_on_failure(env):
    client, root = env
    post(client, root, [document()])
    with sqlite3.connect(auth.DB_PATH) as conn:
        _legacy_import_table(conn)
        before = conn.execute('SELECT * FROM imported_reports').fetchall()
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='imported_reports'").fetchone()[0]

        class FailNewIndex:
            def execute(self, sql, *args):
                if 'CREATE UNIQUE INDEX' in sql:
                    raise sqlite3.OperationalError('synthetic migration failure')
                return conn.execute(sql, *args)

        with pytest.raises(sqlite3.OperationalError, match='synthetic migration failure'):
            imports.init_schema(FailNewIndex())
        assert conn.execute('SELECT * FROM imported_reports').fetchall() == before
        assert conn.execute("SELECT sql FROM sqlite_master WHERE name='imported_reports'").fetchone()[0] == schema
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='imported_reports_file_identity'").fetchone()
        # A retry can complete after the underlying failure is resolved.
        imports.init_schema(conn)
        assert conn.execute('SELECT * FROM imported_reports').fetchall() == before


def test_frontmatter_and_index_metadata_do_not_impersonate_author_or_use_external_ids(env):
    client, root = env
    content = '---\ntitle: 原标题\ncompany: 海尔智家\nskill_id: earnings-review\ndate: 2026-10-12\nauthor_id: someone-else\nid: existing-id\n---\n# 正文标题\n合成报告'
    row = post(client, root, [document('reports/财报.md', content, title='索引标题', group='海尔智家', date='2026-10-18')]).json()['items'][0]
    assert row['title'] == '索引标题' and row['date'] == '2026-10-18'
    assert row['type'] == 'earnings-review' and row['group'] == '海尔智家'
    saved = imports.get_report(row['id'])
    assert saved['author_id'] == root['user']['id'] and saved['id'] != 'existing-id'
    assert saved['content'] == content


@pytest.mark.parametrize('path,content,status', [
    ('../private.md', '# test', 'error'),
    ('C:\\secret\\report.md', '# test', 'error'),
    ('/etc/report.md', '# test', 'error'),
    ('safe/../../report.md', '# test', 'error'),
    ('.git/private.md', '# test', 'skipped'),
    ('company/README.md', '# index', 'skipped'),
    ('index.json', '{}', 'skipped'),
    ('company/report.md', '  \n ', 'error'),
    ('company/report.md', '# binary\x00content', 'error'),
])
def test_invalid_and_non_report_inputs_are_explained_and_not_saved(env, path, content, status):
    client, root = env
    result = post(client, root, [document(path, content)]).json()
    assert result['items'][0]['status'] == status and result['items'][0]['reason']
    assert result['counts']['imported'] == 0 and not imports.list_reports()


def test_missing_or_invalid_report_date_stays_unknown(env):
    client, root = env
    for path in ('company/report.md', 'company/report-20260231.md'):
        row = post(client, root, [document(path, '# ' + path)]).json()['items'][0]
        assert row['date'] == ''
        assert imports.get_report(row['id'])['date'] == ''


def test_import_authentication_and_owner_only_deletion(env):
    client, root = env
    data = {'items': [document()]}
    for endpoint in ('/api/reports/import', '/api/reports/import/preview'):
        assert client.post(endpoint, json=data).status_code == 401
    owner = member()
    row = post(client, owner, [document()]).json()['items'][0]
    delete = '/api/reports/imported/' + row['id']
    assert client.delete(delete).status_code == 401
    assert client.delete(delete, headers=headers(root)).status_code == 404
    assert imports.get_report(row['id'])
    assert client.delete(delete, headers=headers(owner)).status_code == 200
    assert not imports.get_report(row['id'])
    assert client.get('/api/reports/content', params={'path': row['report_path']}, headers=headers(owner)).status_code == 404


def test_concurrent_repeat_import_is_idempotent(env):
    _, root = env
    uid = root['user']['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: imports.process(uid, [document()], save=True), range(4)))
    assert sum(result['counts']['imported'] for result in results) == 1
    assert len(imports.list_reports()) == 1


def test_concurrent_distinct_paths_with_the_same_body_save_only_once(env):
    _, root = env
    docs = [document(f'贵州茅台/agent-{i}/investment-team-20261022.md') for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda doc: imports.process(root['user']['id'], [doc], save=True), docs))
    assert sum(result['counts']['imported'] for result in results) == 1
    assert sum(result['counts']['duplicate'] for result in results) == len(docs) - 1
    assert len(imports.list_reports()) == 1


def test_database_blocks_identical_content_without_rewriting_historical_rows(env):
    from ops.continuity import snapshot, verify
    client, root = env
    post(client, root, [document()])
    # Simulate a copy already saved under the previous path-based rule.
    with sqlite3.connect(auth.DB_PATH) as conn:
        conn.execute('DROP TRIGGER imported_reports_deduplicate_content')
        conn.execute("INSERT INTO imported_reports SELECT 'historical-copy',user_id,'other/copy.md',"
                     'title,company,report_type,report_date,content,content_hash,imported_at FROM imported_reports')
        conn.commit()
    baseline = snapshot(auth.DB_PATH)
    auth.init_db()
    verify(auth.DB_PATH, baseline)
    assert len(imports.list_reports()) == 2
    assert post(client, root, [document('yet-another/copy.md')]).json()['counts']['duplicate'] == 1
    with sqlite3.connect(auth.DB_PATH) as conn:
        cursor = conn.execute("INSERT INTO imported_reports SELECT 'new-copy',user_id,'new/copy.md',"
                              'title,company,report_type,report_date,content,content_hash,imported_at '
                              'FROM imported_reports LIMIT 1')
        assert cursor.rowcount == 0
        assert conn.execute('SELECT COUNT(*) FROM imported_reports').fetchone()[0] == 2


def test_failed_batch_rolls_back_new_rows(env, monkeypatch):
    _, root = env
    calls = 0
    original_now = accounts.now

    def fail_second():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('synthetic write failure')
        return original_now()

    monkeypatch.setattr(accounts, 'now', fail_second)
    with pytest.raises(RuntimeError):
        imports.process(root['user']['id'], [document(), document(content='# another report')], save=True)
    assert imports.list_reports() == []


def test_import_limits_and_user_removal(env):
    client, _ = env
    owner = member()
    uid = owner['user']['id']
    too_large = document(content='中' * (imports.MAX_FILE_BYTES // 3 + 1))
    result = post(client, owner, [too_large]).json()
    assert result['counts']['error'] == 1
    assert client.post('/api/reports/import', headers=headers(owner), json={'items': [document()] * 65}).status_code == 422
    with pytest.raises(ValueError, match='8 MiB'):
        imports.process(uid, [document(content='x' * (imports.MAX_FILE_BYTES - 1))] * 3)
    post(client, owner, [document()])
    auth.init_db()
    assert len(imports.list_reports()) == 1
    assert auth.delete_user(uid)
    assert imports.list_reports() == []


def test_imports_survive_export_restore_and_continuity_detects_removal(env, tmp_path, monkeypatch):
    from ops.continuity import snapshot, verify
    from server import data_migrate
    client, root = env
    row = post(client, root, [document()]).json()['items'][0]
    copy = post(client, root, [document('贵州茅台/other-agent/investment-team-20261022.md',
                                      '# 另一份同日研究\n不同观点。')]).json()['items'][0]
    baseline = snapshot(auth.DB_PATH)
    assert 'imported_reports' in baseline
    assert '合成测试报告' not in json.dumps(baseline)
    monkeypatch.setattr(data_migrate, 'APP_DB', auth.DB_PATH)
    archive = tmp_path / 'reports-backup.zip'
    exported = data_migrate.export_data(archive)
    assert exported['counts']['imported_reports'] == 2
    imports.delete_report(root['user']['id'], row['id'])
    with pytest.raises(RuntimeError):
        verify(auth.DB_PATH, baseline)
    assert data_migrate.import_data(archive)['ok']
    assert imports.get_report(row['id'])['content'] == document()['content']
    assert imports.get_report(copy['id'])['content'] == '# 另一份同日研究\n不同观点。'
    # Restoring intentionally revokes sessions; imported report rows themselves remain unchanged.
    with sqlite3.connect(auth.DB_PATH) as conn:
        assert conn.execute('SELECT COUNT(*) FROM imported_reports').fetchone()[0] == 2


def test_recent_report_discovery_includes_imported_reports(env, monkeypatch):
    from server import shared_reports
    client, root = env
    monkeypatch.setattr(reports_lib, '_INDEX', [])
    post(client, root, [document('company/current.md', '# 最近的测试公司报告', date=accounts.now()[:10])])
    result = shared_reports.recent_matches('测试公司')
    assert len(result['imported']) == 1 and result['total'] == 1
