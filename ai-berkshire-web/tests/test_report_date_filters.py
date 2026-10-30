from datetime import date
import json

import pytest

from conftest import headers, member
from server import accounts, auth, history_store, memory_qa, memory_store as memory, report_dates as rd
from server import reports_lib, shared_reports


@pytest.fixture
def dated(env, tmp_path, monkeypatch):
    monkeypatch.setattr(rd, 'today', lambda: date(2026, 10, 25))
    monkeypatch.setattr(memory, 'DATA_DIR', tmp_path/'memory')
    monkeypatch.setattr(memory, 'DB_PATH', tmp_path/'memory'/'memory.db')
    monkeypatch.setattr(reports_lib, 'all_items', lambda: [])
    return env


def test_date_windows_share_month_week_timezone_and_unknown_rules():
    six = rd.DateFilter.build('6m', asof=date(2026, 10, 25))
    assert six.since == date(2026, 4, 25)
    assert six.matches({'date':'2026-04-25'}) and not six.matches({'date':'2026-04-24'})
    assert not six.matches({'date':'2026-10-26'})
    assert rd.DateFilter.build('1m', asof=date(2024, 3, 31)).since == date(2024, 2, 29)
    week = rd.DateFilter.build('1w', asof=date(2026, 10, 25))
    assert week.matches({'date':'2026-10-18T17:00:00Z'})
    assert rd.DateFilter.build('undated').matches({'date':'2026-02-31'})
    assert rd.DateFilter.build('all').matches({'date':''})
    assert not six.matches({'date':'', 'imported_at':'2026-10-25'})


@pytest.mark.parametrize('endpoint', ['/api/history','/api/reports','/api/memory/conclusions','/api/timeline/companies','/api/timeline/company?grp=目标'])
@pytest.mark.parametrize('query', ['period=invalid', 'period=custom&date_from=2026-10-25&date_to=2026-01-01', 'period=custom&date_from=bad', 'period=custom'])
def test_all_report_search_endpoints_reject_invalid_windows(dated, endpoint, query):
    client, user = dated
    join = '&' if '?' in endpoint else '?'
    assert client.get(endpoint+join+query, headers=headers(user)).status_code == 422


def test_history_filters_before_pagination_and_keeps_user_isolation(dated):
    client, user = dated
    other = member()
    for owner in (user, other):
        for index, day in enumerate(['2026-10-20','2026-05-01','2025-01-01','']):
            saved = history_store.save_record(owner['user']['id'],skill_id='test',skill_title=f'目标{index}',
                arguments='目标',content='合成验收',provider='demo',model='')
            with auth.connect() as conn:
                conn.execute('UPDATE history SET created_at=? WHERE id=?',(day,saved['id']))
    first = client.get('/api/history?period=6m&limit=1',headers=headers(user)).json()
    second = client.get('/api/history?period=6m&limit=1&offset=1',headers=headers(user)).json()
    assert first['total'] == second['total'] == 2
    assert first['items'][0]['id'] != second['items'][0]['id']
    assert client.get('/api/history?period=undated',headers=headers(user)).json()['total'] == 1
    assert client.get('/api/history?period=all',headers=headers(user)).json()['total'] == 4
    assert client.get('/api/history?period=custom&date_from=2026-05-01&date_to=2026-05-01',headers=headers(user)).json()['total'] == 1


def test_unified_library_filters_all_sources_before_counts_and_pages(dated, monkeypatch):
    client, user = dated
    rows = [{'path':f'reports/目标/{day}.md','title':'目标官方','group':'目标','date':day} for day in ['2026-10-20','2026-01-01','']]
    monkeypatch.setattr(reports_lib,'all_items',lambda:rows)
    monkeypatch.setattr(reports_lib,'list_reports',lambda **kw:{'items':rows})
    from server import report_imports
    report_imports.process(user['user']['id'],[{'path':'目标/20261022.md','content':'# 目标导入\n合成内容。'}],save=True)
    saved = history_store.save_record(user['user']['id'],skill_id='test',skill_title='目标站内',arguments='目标',content='合成内容',provider='demo',model='')
    with auth.connect() as conn:
        conn.execute('UPDATE history SET created_at=? WHERE id=?',('2026-10-21',saved['id']))
    result = client.get('/api/reports?period=1m&limit=1',headers=headers(user)).json()
    assert result['total'] == 3
    assert result['counts'] == {'official':1,'generated':1,'imported':1,'all':3}
    assert result['items'][0]['source'] == 'imported'
    assert client.get('/api/reports?period=undated',headers=headers(user)).json()['total'] == 1
    assert client.get('/api/reports?period=all',headers=headers(user)).json()['total'] == 5


def seed_memory():
    with memory._lock:
        conn = memory._connect()
        memory.init_db(conn)
        for index, day in enumerate(['2026-10-20','2026-01-01','','invalid']):
            path = f'report-{index}'
            conn.execute('INSERT INTO docs VALUES (?,?,?,?,?,?,?,?)',(path,'目标报告','目标',day,'研究','目标合成内容',20,'2026-10-25'))
            conn.execute('INSERT INTO terms VALUES (?,?,?)',('目标',path,100 if index == 1 else 1))
            conn.execute('INSERT INTO conclusions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(path,'目标报告','目标',day,'通过',8,10,'','目标摘要','{}','rule','2026-10-25'))
        conn.commit(); conn.close()


def test_memory_date_filter_applies_before_top_n_and_conclusion_pagination(dated):
    client, user = dated
    seed_memory()
    dates = rd.DateFilter.build('1m')
    assert [row['path'] for row in memory.search('目标',top_n=1,date_filter=dates)] == ['report-0']
    result = client.get('/api/memory/conclusions?period=1m&limit=1',headers=headers(user)).json()
    assert result['total'] == 1 and result['items'][0]['path'] == 'report-0'
    assert client.get('/api/memory/conclusions?period=undated',headers=headers(user)).json()['total'] == 2
    assert client.get('/api/memory/conclusions?period=all',headers=headers(user)).json()['total'] == 4


def test_memory_filters_respect_known_unknown_dates_without_rebuilding_index(dated, monkeypatch):
    client, user = dated
    seed_memory()
    monkeypatch.setattr(shared_reports, 'all_items', lambda: [{'path':'report-0','source':'official','date_source':'文件修改时间','date':'2026-10-20'}])
    assert memory.search('目标', date_filter=rd.DateFilter.build('1m')) == []
    result = client.get('/api/memory/conclusions?period=undated',headers=headers(user)).json()
    assert result['total'] == 3
    assert next(row for row in result['items'] if row['path']=='report-0')['date'] == ''


def test_memory_qa_uses_only_selected_dates_and_does_not_call_model_without_hits(dated, monkeypatch):
    client, user = dated
    seed_memory()
    calls = []
    monkeypatch.setattr(memory_qa,'stream_research',lambda prompt,uid:calls.append(prompt) or iter(['合成回答']))
    result = client.post('/api/memory/ask',headers=headers(user),json={'question':'目标','period':'1m'})
    events = [json.loads(line) for line in result.text.splitlines() if line.strip()]
    assert [hit['path'] for hit in events[0]['hits']] == ['report-0']
    assert len(calls) == 1 and 'report-1' not in calls[0]
    before = accounts.quota_info(user['user']['id'])['used']
    result = client.post('/api/memory/ask',headers=headers(user),json={'question':'目标','period':'custom','date_from':'2020-01-01','date_to':'2020-02-01'})
    assert result.status_code == 200 and len(calls) == 1
    assert accounts.quota_info(user['user']['id'])['used'] == before
    assert client.post('/api/memory/ask',headers=headers(user),json={'question':'目标','period':'bad'}).status_code == 422
