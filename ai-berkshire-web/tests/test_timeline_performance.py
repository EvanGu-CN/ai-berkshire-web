from contextlib import closing
import sqlite3
import time

import pytest

from conftest import headers
from server import auth, memory_store as memory, reports_lib, shared_reports, timeline_service
from test_timeline import timeline


def large_catalog(monkeypatch, size=500):
    items = [{'path': f'reports/large/{index:04}.md', 'title': f'报告 {index:04}',
              'group': '大型专题', 'date': '2026-10-01', 'source': 'official'} for index in range(size)]
    monkeypatch.setattr(reports_lib, '_INDEX', items)
    return items


def test_large_company_reads_only_requested_page_and_reuses_derived_cache(timeline, monkeypatch):
    _, _user = timeline
    large_catalog(monkeypatch)
    reads = []

    def get_report(path):
        reads.append(path)
        return {'title': path, 'content': '# 合成报告\n\n评分：8/10\n\n最终结论：建议观望。'}

    monkeypatch.setattr(shared_reports, 'get_report', get_report)
    # An ongoing full memory reindex must not lock out the metadata list or a timeline page.
    with memory._lock:
        assert memory.list_companies()[0]['n'] == 500
        assert reads == []
        page1 = memory.get_timeline('大型专题')
        page2 = memory.get_timeline('大型专题', offset=20)
    assert len(reads) == 40
    assert page1['total'] == 500 and page1['limit'] == 20 and page1['has_next']
    assert page1['stats']['report_count'] == 20 and page1['stats']['scope'] == 'page'
    assert len(page2['items']) == 20
    assert not {item['path'] for item in page1['items']} & {item['path'] for item in page2['items']}
    assert memory.get_timeline('大型专题')['items'] == page1['items']
    assert len(reads) == 40
    assert memory.get_timeline('大型专题', offset=490)['has_next'] is False
    assert memory.get_timeline('大型专题', offset=500)['items'] == []


def test_company_search_paging_and_query_limits(timeline, monkeypatch):
    client, user = timeline
    monkeypatch.setattr(reports_lib, '_INDEX', [
        {'path': f'reports/{index}.md', 'group': f'公司{index:03}', 'date': ''} for index in range(180)])
    first = client.get('/api/timeline/companies', headers=headers(user)).json()
    second = client.get('/api/timeline/companies?offset=30', headers=headers(user)).json()
    assert first['total'] == second['total'] == 180 and len(first['items']) == len(second['items']) == 30
    assert first['has_next'] and not {row['grp'] for row in first['items']} & {row['grp'] for row in second['items']}
    found = client.get('/api/timeline/companies?q=公司179', headers=headers(user)).json()
    assert len(found['items']) == 1 and found['items'][0]['grp'] == '公司179'
    for query in ('limit=51', 'offset=-1', 'limit=0'):
        assert client.get('/api/timeline/companies?' + query, headers=headers(user)).status_code == 422
        assert client.get('/api/timeline/company?grp=公司000&' + query, headers=headers(user)).status_code == 422


def test_metadata_catalog_is_reused_and_invalidated_by_report_changes(timeline, monkeypatch):
    calls = []
    original = shared_reports.all_items

    def counted():
        calls.append(True)
        return original()

    monkeypatch.setattr(shared_reports, 'all_items', counted)
    memory.list_companies(); memory.list_companies()
    assert len(calls) == 1
    with closing(auth.connect()) as conn:
        conn.execute("INSERT INTO history VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ('new-id', 'owner', 'test', '新研究', '新公司', '新正文', 'demo', 'demo', '2026-10-25', 3))
        conn.commit()
    assert any(item['grp'] == '新公司' for item in memory.list_companies())
    assert len(calls) == 2


def test_file_update_invalidates_only_its_rule_cache(timeline):
    first = memory.get_timeline('时间线测试公司')
    assert first['stats']['score_latest'] == 6
    path = reports_lib._norm_path(reports_lib.all_items()[1]['path'])
    path.write_text('# 修订合成报告\n\n评分：9/10\n\n最终结论：建议买入。新增了证据。', encoding='utf-8')
    assert memory.get_timeline('时间线测试公司')['stats']['score_latest'] == 9


def test_large_numeric_text_has_a_bounded_extraction_window(timeline, monkeypatch):
    large_catalog(monkeypatch, size=1)
    text = '# 大报告\n' + '9' * 500_000 + '\n最终结论：建议观望，保留原文供完整阅读。'
    seen = []
    original = memory.extract_conclusion_rule

    def extract(title, content):
        seen.append(len(content))
        return original(title, content)

    monkeypatch.setattr(shared_reports, 'get_report', lambda path: {'title': '大报告', 'content': text})
    monkeypatch.setattr(memory, 'extract_conclusion_rule', extract)
    started = time.perf_counter()
    page = memory.get_timeline('大型专题')
    assert max(seen) <= 120_002
    assert page['items'][0]['limited_text'] and page['items'][0]['verdict'] == '建议观望'
    assert time.perf_counter() - started < 3


def test_cache_busy_does_not_block_a_page(timeline):
    memory.get_timeline('时间线测试公司')
    path = reports_lib._norm_path(reports_lib.all_items()[1]['path'])
    path.write_text('# 缓存被占用时的新报告\n\n评分：9/10', encoding='utf-8')
    with closing(sqlite3.connect(timeline_service._cache_file())) as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            assert memory.get_timeline('时间线测试公司')['stats']['score_latest'] == 9
        finally:
            conn.rollback()


def test_entry_assets_are_versioned_and_entry_html_revalidates(env):
    client, _ = env
    response = client.get('/')
    assert response.status_code == 200
    assert 'no-cache' in response.headers['cache-control']
    assert '/static/app.js?v=' in response.text and '/static/workspace.js?v=' in response.text
    assert '/static/workspace.css?v=' in response.text
    assert client.get('/', headers={'If-None-Match': response.headers['etag']}).status_code == 304


def test_asset_content_change_updates_script_url_even_when_html_is_unchanged(tmp_path):
    from server.web_assets import render_index
    (tmp_path / 'index.html').write_text('<script src="/static/app.js"></script>')
    script = tmp_path / 'app.js'
    script.write_text('old code')
    old_html, old_tag = render_index(tmp_path)
    script.write_text('new code')
    html, tag = render_index(tmp_path)
    assert old_html != html and old_tag != tag
