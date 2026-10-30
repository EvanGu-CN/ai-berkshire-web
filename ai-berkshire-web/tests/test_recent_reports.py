from datetime import date

import pytest

from conftest import headers
from server import accounts, history_store, report_imports, reports_lib, shared_reports as sr


def item(day, source='official', name='目标公司', suffix=''):
    return {'path': f'{source}/{name}/{day}{suffix}.md', 'title': f'{name}研究{suffix}',
            'group': name, 'source': source, 'date': day, 'created_at': day}


@pytest.fixture
def catalog(monkeypatch):
    rows = []
    monkeypatch.setattr(sr, '_today', lambda: date(2026, 10, 25))
    monkeypatch.setattr(sr, 'all_items', lambda: rows)
    return rows


@pytest.mark.parametrize('period,since', [('1w', '2026-10-19'), ('1m', '2026-09-25'),
                                        ('3m', '2026-07-25'), ('6m', '2026-04-25'),
                                        ('12m', '2025-10-25')])
def test_every_source_obeys_same_inclusive_report_date_window(catalog, period, since):
    for source in ('official', 'generated', 'imported'):
        catalog.extend([item(since, source), item('2026-10-25', source), item('2025-01-01', source),
                        item('2026-10-26', source), item('', source), item('2026-02-31', source)])
    result = sr.recent_matches('目标公司', period=period, limit=50)
    assert result['since'] == since and result['until'] == '2026-10-25'
    assert result['total'] == 6
    assert len(result['items']) == 6
    assert all(len(result[source]) == 2 for source in ('official', 'generated', 'imported'))


def test_default_is_six_calendar_months_and_old_days_parameter_remains_valid(catalog):
    catalog.extend([item('2026-07-13'), item('2026-05-16'), item('2026-04-24')])
    result = sr.recent_matches('目标公司')
    assert result['period'] == '6m' and result['total'] == 2
    assert sr.recent_matches('目标公司', days=30)['total'] == 0


@pytest.mark.parametrize('today,period,since', [(date(2026, 3, 31), '1m', '2026-02-28'),
                                             (date(2024, 3, 31), '1m', '2024-02-29'),
                                             (date(2024, 2, 29), '12m', '2023-02-28')])
def test_month_windows_handle_short_months_and_leap_years(catalog, monkeypatch, today, period, since):
    monkeypatch.setattr(sr, '_today', lambda: today)
    assert sr.recent_matches('目标公司', period=period)['since'] == since


def test_totals_count_all_sources_before_one_stable_global_pagination(catalog):
    catalog.extend(item(f'2026-10-{day:02}', source, suffix=str(index))
                   for index, (day, source) in enumerate([(20, 'official')] * 9 + [(21, 'generated')] * 5 + [(22, 'imported')] * 3))
    paths = []
    for offset in range(0, 17, 5):
        page = sr.recent_matches('目标公司', limit=5, offset=offset)
        assert page['total'] == 17 and page['has_next'] == (offset < 15)
        assert len(page['items']) == min(5, 17 - offset)
        paths.extend(row['path'] for row in page['items'])
    assert len(set(paths)) == 17
    assert paths[0].startswith('imported/') and paths[-1].startswith('official/')


def test_import_mtime_and_unknown_dates_are_not_recent(catalog):
    catalog.extend([dict(item('', 'imported'), imported_at='2026-10-25T10:00:00+08:00'),
                    dict(item('2026-10-25'), date_source='文件修改时间'),
                    dict(item('2026-10-25'), path='reports/目标公司/undated.md', date_source='文件名'),
                    dict(item('2026-10-25'), date_source='文件名')])
    assert sr.recent_matches('目标公司')['total'] == 1


def test_generated_timestamps_use_beijing_calendar_date(catalog):
    catalog.extend([dict(item('', 'generated'), created_at='2026-10-18T17:00:00Z'),
                    dict(item('', 'generated'), created_at='2026-10-25T17:00:00Z')])
    result = sr.recent_matches('目标公司', period='1w')
    assert [row['date'] for row in result['items']] == ['2026-10-19']


def test_metadata_matching_ignores_incidental_body_mentions_and_does_not_charge(env, monkeypatch):
    client, user = env
    monkeypatch.setattr(reports_lib, 'all_items', lambda: [])
    uid = user['user']['id']
    history_store.save_record(uid, skill_id='test', skill_title='其他公司研究', arguments='其他公司',
                              content='正文顺带提及目标公司', provider='demo', model='')
    history_store.save_record(uid, skill_id='test', skill_title='综合研究', arguments='目标公司',
                              content='合成验收内容', provider='demo', model='')
    report_imports.process(uid, [{'path': '目标公司/undated.md', 'content': '# 目标公司\n无日期的合成验收资料', 'metadata': {}}], save=True)
    result = client.get('/api/reports/recent', params={'keyword': '目标公司'}, headers=headers(user)).json()
    assert result['total'] == 1 and result['items'][0]['source'] == 'generated'
    assert accounts.quota_info(uid)['used'] == 0
    assert client.get('/api/reports/recent', params={'keyword': '%'}, headers=headers(user)).json()['total'] == 0


@pytest.mark.parametrize('query', ['period=all', 'period=30', 'days=0', 'limit=11', 'offset=-1'])
def test_api_rejects_invalid_ranges_and_pages(env, query):
    client, user = env
    assert client.get('/api/reports/recent?' + query, headers=headers(user)).status_code == 422


def test_empty_keyword_does_not_load_catalog(catalog, monkeypatch):
    monkeypatch.setattr(sr, 'all_items', lambda: pytest.fail('empty query must not load catalog'))
    assert sr.recent_matches('')['total'] == 0
