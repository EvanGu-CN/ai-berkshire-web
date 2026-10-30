import json
import threading
import asyncio

import pytest
from server import app as server, auth, accounts, history_store as history, multi_agent, pipeline, skills_catalog, streaming
from conftest import headers, member
from starlette.requests import ClientDisconnect


def events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def request(client,user,**kwargs):
    return client.post('/api/research/run',headers=headers(user),json={'skill_id':'quality-screen','mode':'single','arguments':'示例公司',**kwargs})


def test_single_research_done_saved_and_counted_once(env,monkeypatch):
    client,_=env;user=member()
    monkeypatch.setattr(server,'stream_research',lambda prompt,uid: iter(['# 测试报告\n','结果内容']))
    result=request(client,user)
    assert result.status_code==200
    result_events=events(result)
    assert result_events[-1] == {'type':'done','full_text':'# 测试报告\n结果内容'}
    assert accounts.quota_info(user['user']['id'])['used']==1
    assert history.list_records(user['user']['id'])[0]['chars']>0


@pytest.mark.parametrize('mode', ['single', 'team'])
def test_silent_model_keeps_connection_alive_then_saves_once(env, monkeypatch, mode):
    client, _ = env
    user = member()
    release = threading.Event()

    def model(*_):
        assert release.wait(2)
        yield '# 合成验收报告\n完成内容'

    monkeypatch.setattr(streaming, 'HEARTBEAT_SECONDS', 0.01)
    monkeypatch.setattr(server, 'stream_research', model)
    monkeypatch.setattr(multi_agent, 'stream_research', model)
    timer = threading.Timer(0.1, release.set)
    timer.start()
    try:
        response = request(client, user, mode=mode)
    finally:
        release.set()
        timer.join()
    assert response.headers['x-accel-buffering'] == 'no'
    assert response.text.splitlines().count('') >= 2
    result = events(response)
    assert result[-1]['type'] == 'done'
    assert not any(ev['type'] == 'error' for ev in result)
    assert len(history.list_records(user['user']['id'])) == 1
    assert accounts.quota_info(user['user']['id'])['used'] == 1
    if mode == 'team':
        assert sum(ev['type'] == 'role_done' for ev in result) == 4


def test_disconnect_during_upstream_wait_closes_safely_and_preserves_partial(env, monkeypatch):
    _, _ = env
    user = member()
    ready = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original_finish = accounts.finish

    def model(*_):
        ready.set()
        assert release.wait(2)
        yield '已生成的合成验收内容'

    def finish(*args):
        original_finish(*args)
        finished.set()

    monkeypatch.setattr(server, 'stream_research', model)
    monkeypatch.setattr(accounts, 'finish', finish)
    monkeypatch.setattr(server, 'event_stream', lambda events, **_: streaming.keepalive_events(events, interval=0.01))
    response = server.api_research_run(server.ResearchRequest(skill_id='quality-screen', arguments='合成验收公司', mode='single'), user=user['user'])
    try:
        assert next(response) == '\n'
        assert ready.wait(1)
        response.close()
        release.set()
        assert finished.wait(1)
    finally:
        release.set()
        response.close()
    records = history.list_records(user['user']['id'])
    assert len(records) == 1 and '未完成' in records[0]['skill_title']
    assert accounts.quota_info(user['user']['id'])['used'] == 1


def test_disconnect_before_first_body_releases_usage_without_model_call(env, monkeypatch):
    _, _ = env
    user = member()
    monkeypatch.setattr(server, 'stream_research', lambda *_: pytest.fail('must not call model'))
    response = server.api_research_run(server.ResearchRequest(skill_id='quality-screen', arguments='合成验收公司', mode='single'), user=user['user'])

    async def run():
        async def send(_):
            raise OSError('connection closed')

        async def receive():
            await asyncio.sleep(1)
            return {'type': 'http.disconnect'}

        with pytest.raises(ClientDisconnect):
            await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)

    asyncio.run(run())
    assert accounts.quota_info(user['user']['id'])['used'] == 0
    assert accounts.usage_list(user['user']['id'])[0]['status'] == 'failed'
    assert not history.list_records(user['user']['id'])


def test_team_close_waits_for_inflight_role_threads(monkeypatch):
    ready = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    calls = []
    lock = threading.Lock()

    def model(*_):
        with lock:
            calls.append(1)
            if len(calls) == 4:
                ready.set()
        assert release.wait(2)
        yield '合成内容'

    monkeypatch.setattr(multi_agent, 'stream_research', model)
    events = multi_agent.iter_team_research('合成技能', '合成公司')
    assert next(events)['type'] == 'role_start'
    assert ready.wait(1)

    def close():
        events.close()
        closed.set()

    closer = threading.Thread(target=close)
    closer.start()
    try:
        assert not closed.wait(0.05)
    finally:
        release.set()
        closer.join(1)
    assert closed.is_set()
    assert len(calls) == 4, 'cancelled team must not start synthesis'


def test_failed_model_no_false_done_no_charge_and_no_raw_exception(env,monkeypatch):
    client,_=env;user=member()
    def broken(*_):
        raise RuntimeError('sensitive-provider-response')
        yield ''
    monkeypatch.setattr(server,'stream_research',broken)
    result=request(client,user)
    assert 'sensitive-provider-response' not in result.text
    assert events(result)[-1]['type']=='error'
    assert not any(e['type']=='done' for e in events(result))
    assert accounts.quota_info(user['user']['id'])['used']==0
    assert not history.list_records(user['user']['id'])


def test_partial_research_preserved_and_counted(env,monkeypatch):
    client,_=env;user=member()
    def partial(*_):
        yield '已产出的部分内容'
        raise RuntimeError('upstream disconnected')
    monkeypatch.setattr(server,'stream_research',partial)
    response=request(client,user)
    assert events(response)[-1]['type']=='error'
    rec=history.list_records(user['user']['id'])[0]
    assert '未完成' in rec['skill_title']
    assert accounts.quota_info(user['user']['id'])['used']==1


def test_invalid_skill_or_limit_rejection_never_calls_model(env,monkeypatch):
    client,_=env;user=member()
    monkeypatch.setattr(server,'stream_research',lambda *_: pytest.fail('must not call provider'))
    assert request(client,user,skill_id='missing').status_code==404
    assert request(client,user,skill_id='../../README').status_code==422
    accounts.save_settings({'free_quota':1})
    accounts.reserve(user['user']['id'],'research')
    result=request(client,user)
    assert result.status_code==429
    assert '额度' in result.json()['detail']


def test_no_memory_hits_does_not_charge(env,monkeypatch):
    client,_=env;user=member()
    monkeypatch.setattr(server.mq,'ask',lambda *a,**kw:([],iter(['没有匹配的资料'])))
    result=client.post('/api/memory/ask',headers=headers(user),json={'question':'测试问题'})
    assert events(result)[-1]['type']=='done'
    assert accounts.quota_info(user['user']['id'])['used']==0


def test_memory_and_extraction_are_in_quota(env,monkeypatch):
    client,_=env;user=member()
    monkeypatch.setattr(server.mq,'ask',lambda *a,**kw:([{'title':'source'}],iter(['answer'])))
    result=client.post('/api/memory/ask',headers=headers(user),json={'question':'测试问题'})
    assert events(result)[-1]['type']=='done'
    monkeypatch.setattr(server.rl,'get_report',lambda path:{'title':'test','content':'content'})
    monkeypatch.setattr(server.mq,'extract_conclusion_llm',lambda *a,**kw:{'summary':'test'})
    monkeypatch.setattr(server.ms,'upsert_conclusion_llm',lambda *_:None)
    assert client.post('/api/memory/extract',headers=headers(user),json={'path':'test'}).status_code==200
    assert accounts.quota_info(user['user']['id'])['used']==2


def test_failed_memory_releases_reservation(env,monkeypatch):
    client,_=env;user=member()
    def broken():
        raise RuntimeError('sensitive-provider-response')
        yield ''
    monkeypatch.setattr(server.mq,'ask',lambda *a,**kw:([{'title':'source'}],broken()))
    result=client.post('/api/memory/ask',headers=headers(user),json={'question':'测试问题'})
    assert events(result)[-1]['type']=='error'
    assert accounts.quota_info(user['user']['id'])['used']==0


def test_team_all_roles_use_requesting_user(env,monkeypatch):
    calls=[]
    monkeypatch.setattr(multi_agent,'stream_research',lambda prompt,uid: calls.append(uid) or iter(['test output']))
    monkeypatch.setattr(multi_agent.pa,'build_role_prompt',lambda *a,**kw:'test')
    monkeypatch.setattr(multi_agent.pa,'build_synthesis_prompt',lambda *a,**kw:'synthesis')
    result=list(multi_agent.iter_team_research('skill','company',user_id='specific-user'))
    assert calls==['specific-user']*5
    assert result[-1]['type']=='done'


def test_pipeline_demo_uses_user_config_and_respects_chosen_stage(env,monkeypatch):
    calls=[]
    monkeypatch.setattr(pipeline,'get_active_config',lambda uid: calls.append(uid) or {'provider':'demo'})
    monkeypatch.setattr(pipeline.hs,'save_record',lambda *a,**kw:{'id':'test'})
    result=list(pipeline.run_pipeline('test','checklist'.split(),user_id='specific-user'))
    assert result[0]['type']=='pipeline_start'
    assert result[0]['stages']==['checklist']
    assert set(calls)=={'specific-user'}


@pytest.mark.parametrize('text',[
    'not json','[]','[{"company":"test","pass":"false"}]',
    '[{"company":"other","pass":true}]','[{"company":"test","pass":true},{"company":"test","pass":false}]'
])
def test_pipeline_never_auto_passes_invalid_results(text):
    with pytest.raises(ValueError):pipeline._parse_stage_json(text,['test'])


@pytest.mark.parametrize('prefix,suffix', [
    ('[六关检查]\n```json\n', '\n```\n[完毕]'),
    ('<think>[{"company":"test","pass":false}]</think>\n', ''),
    ('公司列表：["test"]\n', '\n参考：[1]'),
])
def test_pipeline_parses_final_array_without_greedy_bracket_capture(prefix, suffix):
    verdict = {'company': 'test', 'pass': True, 'reason': '理由包含 [ROE] 和转义引号 "'}
    text = prefix + json.dumps([verdict], ensure_ascii=False) + suffix
    assert pipeline._parse_stage_json(text, ['test']) == [verdict]


def test_pipeline_rejects_ambiguous_valid_arrays():
    text = '[{"company":"test","pass":true}]\n[{"company":"test","pass":false}]'
    with pytest.raises(ValueError):
        pipeline._parse_stage_json(text, ['test'])


@pytest.mark.parametrize('recover', [True, False])
def test_checklist_format_retry_is_bounded_preserves_history_and_quota(env, monkeypatch, recover):
    client, _ = env
    user = member()
    calls = []
    monkeypatch.setattr(pipeline, 'get_active_config', lambda uid: {'provider': 'openai', 'api_key': 'test'})
    def model(prompt, uid):
        calls.append((prompt, uid))
        if len(calls) == 1 or (recover and len(calls) == 3):
            yield '[{"company":"测试公司","pass":true,"reason":"完整校验"}]'
        elif len(calls) <= 3:
            yield 'invalid JSON with sensitive-provider-response'
        else:
            yield '# 深度研究\n测试内容'
    monkeypatch.setattr(pipeline, 'stream_research', model)
    response = client.post('/api/pipeline/run', headers=headers(user), json={
        'targets': '测试公司', 'stages': ['screen', 'checklist', 'deep'],
    })
    result = events(response)
    assert sum(e['type'] == 'stage_retry' for e in result) == 1
    assert all(uid == user['user']['id'] for _, uid in calls)
    assert '上次返回未通过格式校验' in calls[2][0]
    assert 'sensitive-provider-response' not in response.text
    assert accounts.quota_info(user['user']['id'])['used'] == 1
    records = history.list_records(user['user']['id'])
    assert len(records) == 1
    assert '完整校验' in history.get_record(user['user']['id'], records[0]['id'])['content']
    if recover:
        assert len(calls) == 4
        assert result[-1]['type'] == 'pipeline_end'
    else:
        assert len(calls) == 3
        assert result[-1]['type'] == 'error'
        assert 'Checklist' in result[-1]['message']
        assert '已自动重试一次' in result[-1]['message']
        assert not any(e['type'] in ('pipeline_end', 'deep_start') for e in result)


def test_pipeline_quota_finish_on_error(env,monkeypatch):
    client,_=env;user=member()
    def fail(*a,**kw):
        raise RuntimeError('provider failed')
        yield {}
    monkeypatch.setattr(server.pl,'run_pipeline',fail)
    result=client.post('/api/pipeline/run',headers=headers(user),json={'targets':'测试公司','stages':['screen']})
    assert events(result)[-1]['type']=='error'
    assert accounts.quota_info(user['user']['id'])['used']==0


def test_pipeline_run_endpoint_streams_progress_saves_and_counts_once(env, monkeypatch):
    client, _ = env
    user = member()
    calls = []
    monkeypatch.setattr(pipeline, 'get_active_config', lambda uid: {'provider': 'openai', 'api_key': 'test'})
    def model(prompt, uid):
        calls.append(uid)
        if len(calls) <= 2:
            yield '[{"company":"测试公司",'
            yield '"pass":true,"reason":"测试通过"}]'
        else:
            yield '# 深度研究\n测试内容'
    monkeypatch.setattr(pipeline, 'stream_research', model)
    response = client.post('/api/pipeline/run', headers=headers(user), json={
        'targets': '测试公司', 'stages': ['screen', 'checklist', 'deep'], 'deep_count': 1,
    })
    assert response.status_code == 200
    result = events(response)
    assert result[0]['type'] == 'pipeline_start'
    assert result[-1] == {'type': 'pipeline_end', 'survivors': ['测试公司']}
    for stage in ('screen', 'checklist'):
        stage_events = [e for e in result if e.get('stage') == stage]
        assert [e['type'] for e in stage_events] == [
            'stage_start', 'stage_progress', 'stage_progress', 'stage_item', 'stage_end',
        ]
        assert stage_events[2]['chars'] > stage_events[1]['chars'] > 0
    assert calls == [user['user']['id']] * 3
    assert accounts.quota_info(user['user']['id'])['used'] == 1
    records = history.list_records(user['user']['id'])
    assert len(records) == 1
    assert records[0]['skill_id'] == 'industry-funnel'


@pytest.mark.parametrize('payload', [
    {'targets': '，、'}, {'targets': ','.join(['test'] * 31)},
    {'targets': 'test', 'stages': []}, {'targets': 'test', 'deep_count': 6},
])
def test_invalid_pipeline_never_starts_or_charges(env, monkeypatch, payload):
    client, _ = env
    user = member()
    monkeypatch.setattr(pipeline, 'run_pipeline', lambda *a, **kw: pytest.fail('must not start'))
    response = client.post('/api/pipeline/run', headers=headers(user), json=payload)
    assert response.status_code in (400, 422)
    assert accounts.quota_info(user['user']['id'])['used'] == 0


def test_pipeline_cancel_before_start_never_calls_llm(env,monkeypatch):
    cancel=threading.Event();cancel.set()
    monkeypatch.setattr(pipeline,'_run_stage',lambda *a,**kw:pytest.fail('cancelled task must not run'))
    monkeypatch.setattr(pipeline.hs,'save_record',lambda *a,**kw:{'id':'test'})
    result=list(pipeline.run_pipeline('测试公司',['screen'],user_id=env[1]['user']['id'],cancelled=cancel))
    assert not any(e['type']=='stage_start' for e in result)


def test_skill_path_cannot_escape_framework(env):
    assert skills_catalog.get_skill_content('../../README') is None


def test_all_team_roles_fail_without_producing_success_or_leaking_errors(env,monkeypatch):
    def broken(*a):
        raise RuntimeError('sensitive-provider-response')
        yield ''
    monkeypatch.setattr(multi_agent,'stream_research',broken)
    monkeypatch.setattr(multi_agent.pa,'build_role_prompt',lambda *a,**kw:'test')
    result=list(multi_agent.iter_team_research('skill','company',user_id='test'))
    assert result[-1]['type']=='error'
    assert not any(e['type'] in ('done','role_done') for e in result)
    assert 'sensitive-provider-response' not in json.dumps(result)
