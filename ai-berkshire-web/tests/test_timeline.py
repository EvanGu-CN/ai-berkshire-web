import time

import pytest

from conftest import headers, member
from server import auth, history_store, memory_store as memory, report_imports, reports_lib
from server import report_dates as rd
from datetime import date


@pytest.fixture
def timeline(env, tmp_path, monkeypatch):
    framework = tmp_path / 'framework'
    reports = framework / 'reports' / '时间线测试公司'
    reports.mkdir(parents=True)
    items = []
    for date, score, verdict in [('2026-10-01', 8, '通过'), ('2026-10-15', 6, '建议观望')]:
        file = reports / (date + '.md')
        file.write_text(f'# 官方研究 {date}\n\n评分：{score}/10\n\n最终结论：{verdict}。这是合成报告的测试结论与证据。', encoding='utf-8')
        items.append({'path': file.relative_to(framework).as_posix(), 'group': '时间线测试公司',
                      'title': '官方研究 ' + date, 'date': date, 'type': '研究'})
    monkeypatch.setattr(reports_lib, 'AI_BERKSHIRE', framework)
    monkeypatch.setattr(reports_lib, 'REPORTS_DIR', framework / 'reports')
    monkeypatch.setattr(reports_lib, '_INDEX', items)
    monkeypatch.setattr(reports_lib, '_BUILT_AT', time.time())
    monkeypatch.setattr(memory, 'DATA_DIR', tmp_path / 'memory')
    monkeypatch.setattr(memory, 'DB_PATH', tmp_path / 'memory' / 'memory.db')

    def forbidden_reindex(*args, **kwargs):
        raise AssertionError('Timeline must not require full memory reindexing')

    monkeypatch.setattr(memory, 'reindex_all', forbidden_reindex)
    return env


def test_timeline_works_with_an_empty_memory_index(timeline):
    client, user = timeline
    assert memory.index_status()['conclusions'] == 0
    companies = client.get('/api/timeline/companies', headers=headers(user)).json()['items']
    assert companies == [{'grp': '时间线测试公司', 'n': 2, 'score_cnt': None,
                          'first_date': '2026-10-01', 'last_date': '2026-10-15'}]
    response = client.get('/api/timeline/company', params={'grp': '时间线测试公司'}, headers=headers(user))
    assert response.status_code == 200
    result = response.json()
    assert result['stats']['report_count'] == 2 and result['stats']['score_count'] == 2
    assert result['scores'] == [['2026-10-01', 8.0], ['2026-10-15', 6.0]]
    assert result['stats']['score_first_max'] == result['stats']['score_latest_max'] == 10
    assert result['drifts'][0]['path'] == result['items'][1]['path']
    assert memory.index_status()['docs'] == memory.index_status()['conclusions'] == 0


def test_timeline_date_window_filters_directory_and_page_before_rule_reads(timeline, monkeypatch):
    client, user = timeline
    monkeypatch.setattr(rd,'today',lambda:date(2026,10,25))
    company = client.get('/api/timeline/companies?period=custom&date_from=2026-10-15&date_to=2026-10-15',headers=headers(user)).json()
    assert company['total'] == 1 and company['items'][0]['n'] == 1
    response = client.get('/api/timeline/company?grp=时间线测试公司&period=custom&date_from=2026-10-15&date_to=2026-10-15',headers=headers(user))
    assert response.json()['total'] == 1 and response.json()['items'][0]['date'] == '2026-10-15'
    assert client.get('/api/timeline/companies?period=undated',headers=headers(user)).json()['total'] == 0
    assert client.get('/api/timeline/company?grp=时间线测试公司&period=1w',headers=headers(user)).json()['total'] == 0


def test_timeline_includes_generated_and_imported_reports_and_can_open_all_sources(timeline):
    client, user = timeline
    uid = user['user']['id']
    history_store.save_record(uid, skill_id='investment-research', skill_title='站内研究',
                              arguments='时间线测试公司', content='# 站内研究\n\n最终结论：建议观望。', provider='demo', model='demo')
    imported = report_imports.process(uid, [{'path': '时间线测试公司/20261025.md',
        'content': '# 本地导入研究\n\n最终结论：有条件通过，这是合成测试。'}], save=True)
    assert imported['counts']['imported'] == 1
    result = client.get('/api/timeline/company', params={'grp': '时间线测试公司'}, headers=headers(user)).json()
    assert len(result['items']) == 4
    assert {row['report_source'] for row in result['items']} == {'official', 'generated', 'imported'}
    for row in result['items']:
        assert client.get('/api/reports/content', params={'path': row['path']}, headers=headers(user)).status_code == 200
    # Other signed-in members have the same existing global shared-report access.
    other = member()
    assert client.get('/api/timeline/companies', headers=headers(other)).json()['items'][0]['n'] == 4


def test_timeline_keeps_different_reports_with_the_same_metadata(timeline):
    client, user = timeline
    docs = [{'path': f'时间线测试公司/agent-{i}/investment-team-20261015.md',
             'content': f'# 同日研究\n\n第 {i} 份判断，最终结论：通过。',
             'metadata': {'title': '同日研究', 'group': '时间线测试公司',
                          'type': 'investment-team', 'date': '2026-10-15'}} for i in range(3)]
    imported = report_imports.process(user['user']['id'], docs, save=True)
    assert imported['counts']['imported'] == 3
    companies = client.get('/api/timeline/companies', headers=headers(user)).json()['items']
    assert companies[0]['n'] == 5
    result = client.get('/api/timeline/company', params={'grp': '时间线测试公司'}, headers=headers(user)).json()
    assert result['total'] == 5
    paths = {row['report_path'] for row in imported['items']}
    assert paths.issubset({item['path'] for item in result['items']})


def test_existing_llm_conclusion_is_preserved_without_reindexing(timeline):
    client, user = timeline
    path = reports_lib.all_items()[0]['path']
    memory.upsert_conclusion_llm(path, {'title': '已有深度抽取', 'grp': '时间线测试公司',
        'date': '2026-10-01', 'score': 82, 'score_max': 100, 'verdict': '保留原判断', 'summary': '已有的人工触发模型抽取'})
    original = memory.get_conclusion(path)
    result = client.get('/api/timeline/company', params={'grp': '时间线测试公司'}, headers=headers(user)).json()
    first = next(row for row in result['items'] if row['path'] == path)
    assert first['source'] == 'llm' and first['score'] == 82 and first['verdict'] == '保留原判断'
    assert result['stats']['score_scales'] == [10.0, 100.0]
    assert memory.get_conclusion(path) == original


def test_old_rule_cache_does_not_keep_obsolete_score_parsing(timeline):
    _, _user = timeline
    path = reports_lib.all_items()[0]['path']
    memory.upsert_conclusion_llm(path, {'score': 8, 'score_max': 1, 'grp': '时间线测试公司'})
    with memory._lock:
        conn = memory._connect()
        try:
            conn.execute("UPDATE conclusions SET source='rule' WHERE path=?", (path,))
            conn.commit()
        finally:
            conn.close()
    original = memory.get_conclusion(path)
    first = next(row for row in memory.get_timeline('时间线测试公司')['items'] if row['path'] == path)
    assert first['source'] == 'rule' and first['score_max'] == 10
    assert memory.get_conclusion(path) == original


def test_undated_reports_are_visible_without_inventing_a_date_or_latest_verdict(timeline):
    client, user = timeline
    result = report_imports.process(user['user']['id'], [{'path': '时间线测试公司/brief.md', 'content': '# 短文'}], save=True)
    response = client.get('/api/timeline/company', params={'grp': '时间线测试公司'}, headers=headers(user)).json()
    unknown = next(row for row in response['items'] if row['path'] == result['items'][0]['report_path'])
    assert unknown['date'] == '' and unknown['source'] == 'none' and unknown['score'] is None
    assert response['stats']['report_count'] == 3 and response['stats']['undated_count'] == 1
    assert response['stats']['first_date'] == '2026-10-01' and response['stats']['last_date'] == '2026-10-15'
    assert response['stats']['verdict_latest'] == '建议观望'
    assert len(response['scores']) == 2


def test_company_catalog_refreshes_after_import_and_deletion_without_erasing_old_conclusions(timeline):
    client, user = timeline
    result = report_imports.process(user['user']['id'], [{'path': '新公司/undated.md', 'content': '# 短文'}], save=True)
    row = result['items'][0]
    assert any(company['grp'] == '新公司' for company in memory.list_companies())
    memory.upsert_conclusion_llm(row['report_path'], {'grp': '新公司', 'verdict': '既有抽取'})
    assert client.delete('/api/reports/imported/' + row['id'], headers=headers(user)).status_code == 200
    assert not any(company['grp'] == '新公司' for company in memory.list_companies())
    assert memory.get_timeline('新公司')['items'] == []
    assert memory.get_conclusion(row['report_path'])['verdict'] == '既有抽取'


def test_company_catalog_is_not_truncated_at_80_groups(timeline, monkeypatch):
    monkeypatch.setattr(reports_lib, '_INDEX', [
        {'path': f'reports/公司{i}/report.md', 'group': f'公司{i}', 'date': ''} for i in range(180)])
    assert len(memory.list_companies()) == 180


def test_timeline_authentication_and_empty_library(timeline, monkeypatch):
    client, user = timeline
    assert client.get('/api/timeline/companies').status_code == 401
    assert client.get('/api/timeline/company?grp=测试').status_code == 401
    monkeypatch.setattr(reports_lib, '_INDEX', [])
    assert client.get('/api/timeline/companies', headers=headers(user)).json()['items'] == []
    assert client.get('/api/timeline/company?grp=测试', headers=headers(user)).status_code == 404


@pytest.mark.parametrize('text,score,maximum', [
    ('综合评分：85/100', 85, 100), ('评分：8.5/10', 8.5, 10),
    ('综合评分：0/100', 0, 100), ('（80/100分）', 80, 100),
    ('评分：11/10', None, None), ('评分：1/0', None, None), ('综合评分：-1/10', None, None),
])
def test_rule_score_keeps_multi_digit_denominators_and_rejects_invalid_scores(text, score, maximum):
    result = memory.extract_conclusion_rule('评分测试', text) or {}
    assert result.get('score') == score and result.get('score_max') == maximum
