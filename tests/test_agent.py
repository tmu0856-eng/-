"""检查动态工具流程、范围隔离与调用约束，不调用付费模型。"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import agent, db, llm, research
from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(llm, 'configured', lambda: True)
    for key in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL', 'EMBEDDING_MODEL'):
        monkeypatch.delenv(key, raising=False)
    with TestClient(app) as client:
        for pid in ('alpha', 'beta'):
            db.execute("INSERT INTO papers(id,title,status,page_count,created) VALUES(?,?,'ready',2,'2026-10-28')", (pid, pid + ' fixture paper'))
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
                       (pid + '-1', pid, 1, 'Synthetic fixture: Adam optimizer learning rate 0.001. Eight GPU devices.', '[10,20,100,200]', None))
        yield client


def call(name, arguments, cid='call1'):
    return {'id': cid, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}


def turns(monkeypatch, *values):
    iterator = iter(values)
    requests = []
    def turn(messages, schemas, **kw):
        requests.append(json.loads(json.dumps({'messages': messages, 'tools': schemas})))
        return next(iterator)
    monkeypatch.setattr(llm, 'tool_turn', turn)
    return requests


def final_claim(monkeypatch, source='S1'):
    monkeypatch.setattr(llm, 'complete_stream', lambda *a, **kw: iter([f'Learning rate is 0.001. ||| {source}\n']))


def test_dynamic_search_then_read_with_stable_sources(client, monkeypatch):
    requests = turns(monkeypatch,
        {'role':'assistant', 'tool_calls':[call('search_evidence', {'query':'Adam', 'paper_id':'alpha'})]},
        {'role':'assistant', 'tool_calls':[call('read_page', {'paper_id':'alpha','page':1}, 'call2')]},
        {'role':'assistant', 'content':'finished'})
    final_claim(monkeypatch)
    value = client.post('/api/ask', json={'question':'Adam learning rate', 'paper_ids':['alpha'], 'agent':True}).json()
    assert value['execution'] == 'agent' and value['tool_calls'] == 2
    assert len(value['sources']) == 1 and value['sources'][0]['source_id'] == 'S1'
    assert value['sources'][0]['bbox'] == [10,20,100,200]
    assert value['sources'][0]['paper_id'] == 'alpha'
    assert 'beta' not in json.dumps(requests[0]['messages'])
    assert requests[1]['messages'][-1]['role'] == 'tool'
    assert requests[1]['messages'][-1]['tool_call_id'] == 'call1'
    assert 'search_arxiv' not in json.dumps(requests[0]['tools'])


@pytest.mark.parametrize('name,args', [
    ('read_page', {'paper_id':'beta','page':1}),
    ('search_evidence', {'query':'Adam','paper_id':'beta'}),
    ('read_page', {'paper_id':'alpha','page':True}),
    ('search_evidence', {'query':'Adam','limit':7}),
    ('search_evidence', {'query':'Adam','paper_ids':['beta']}),
    ('delete_paper', {'paper_id':'alpha'}),
    ('search_arxiv', {'query':'attention'}),
])
def test_invalid_and_out_of_scope_calls_are_rejected(client, name, args):
    session = agent.Session(['alpha'], False)
    output, params, note, state = session.execute(name, json.dumps(args))
    assert 'error' in output and state == 'rejected'
    assert not session.sources
    assert db.one('SELECT COUNT(*) n FROM papers')['n'] == 2


def test_malformed_arguments_do_not_escape(client):
    session = agent.Session(['alpha'], False)
    assert session.execute('search_evidence', '{bad json')[3] == 'rejected'
    assert session.execute('search_evidence', '[]')[3] == 'rejected'


def test_repeat_tool_does_not_repeat_io(client, monkeypatch):
    session = agent.Session(['alpha'], False)
    args = json.dumps({'query':'Adam'})
    assert session.execute('search_evidence', args)[3] == 'done'
    monkeypatch.setattr(agent.retrieval, 'retrieve', lambda *a: pytest.fail('must not repeat retrieval'))
    assert session.execute('search_evidence', args)[3] == 'skipped'


def test_model_text_without_tools_is_never_displayed_as_answer(client, monkeypatch):
    turns(monkeypatch, {'role':'assistant', 'content':'Fabricated result from model memory.'})
    response = client.post('/api/ask', json={'question':'What hardware?', 'agent':True})
    assert response.json()['mode'] == 'insufficient'
    assert 'Fabricated' not in response.text


def test_budget_stops_repeated_calls(client, monkeypatch):
    requests = turns(monkeypatch, *[{'role':'assistant', 'tool_calls':[call('library_catalog', {}, f'c{i}')]} for i in range(4)])
    response = client.post('/api/ask', json={'question':'Find evidence', 'agent':True}).json()
    assert len(requests) == 4 and response['bounded'] is True
    assert response['tool_calls'] == 4 and response['mode'] == 'insufficient'
    assert any(t['status'] == 'skipped' for t in response['trace'])


def test_total_call_limit_enforced(client, monkeypatch):
    requests = turns(monkeypatch, *[{'role':'assistant', 'tool_calls':[call('library_catalog', {}, f'c{i}{j}') for j in range(3)]} for i in range(3)])
    response = client.post('/api/ask', json={'question':'Find evidence', 'agent':True}).json()
    assert len(requests) == 3 and response['tool_calls'] == 8
    assert response['bounded'] is True


def test_fabricated_citation_rejected_after_agent_search(client, monkeypatch):
    turns(monkeypatch, {'role':'assistant','tool_calls':[call('search_evidence', {'query':'Adam'})]}, {'role':'assistant','content':'done'})
    final_claim(monkeypatch, 'FAKE')
    response = client.post('/api/ask', json={'question':'Adam?', 'agent':True})
    assert response.status_code == 503 and '不存在' in response.json()['detail']


def test_inline_citation_cannot_bypass_structured_citation_check(client, monkeypatch):
    turns(monkeypatch, {'role':'assistant','tool_calls':[call('search_evidence', {'query':'Adam'})]}, {'role':'assistant','content':'done'})
    monkeypatch.setattr(llm, 'complete_stream', lambda *a, **kw: iter(['Learning rate is 0.001 [S99]. ||| S1\n']))
    response = client.post('/api/ask', json={'question':'Adam?', 'agent':True})
    assert response.status_code == 503 and '未声明' in response.json()['detail']


def test_private_planning_content_never_leaks_in_stream(client, monkeypatch):
    turns(monkeypatch, {'role':'assistant','reasoning_content':'private secret reasoning','content':'unchecked answer',
                       'tool_calls':[call('search_evidence', {'query':'Adam'})]}, {'role':'assistant','content':'unchecked final'})
    final_claim(monkeypatch)
    response = client.post('/api/ask/stream', json={'question':'Adam?', 'agent':True})
    assert 'event: result' in response.text
    assert 'private secret' not in response.text and 'unchecked' not in response.text


def test_external_discovery_separate_from_primary_evidence(client, monkeypatch):
    turns(monkeypatch, {'role':'assistant','tool_calls':[call('search_arxiv', {'query':'attention'})]}, {'role':'assistant','content':'done'})
    monkeypatch.setattr(research, 'arxiv_search', lambda *a: [{'id':'1706.03762v7','title':'Metadata only','summary':'Not full text','retrieval':{'cache_state':'stale'}}])
    response = client.post('/api/ask', json={'question':'Find related papers', 'agent':True,'allow_external':True}).json()
    assert response['mode'] == 'insufficient' and response['sources'] == []
    assert response['discoveries'][0]['retrieval']['cache_state'] == 'stale'


def test_discovery_failure_is_a_tool_result_not_fabricated_papers(client, monkeypatch):
    def fail(*a):
        raise research.DiscoveryError('arXiv 暂时不可用')
    monkeypatch.setattr(research, 'arxiv_search', fail)
    output, _, _, status = agent.Session(['alpha'], True).execute('search_arxiv', '{"query":"attention"}')
    assert status == 'failed' and 'error' in output and 'papers' not in output


def test_stream_preserves_actual_steps_on_generation_failure(client, monkeypatch):
    turns(monkeypatch, {'role':'assistant','tool_calls':[call('search_evidence', {'query':'Adam'})]}, {'role':'assistant','content':'done'})
    def fail(*a, **kw):
        raise llm.ModelError('Test generation failure')
    monkeypatch.setattr(llm, 'complete_stream', fail)
    response = client.post('/api/ask/stream', json={'question':'Adam?', 'agent':True})
    assert 'search_evidence' in response.text and 'event: error' in response.text
    assert 'event: result' not in response.text


def test_unknown_scope_never_silently_expands_to_all(client):
    response = client.post('/api/ask', json={'question':'Adam?', 'paper_ids':['unknown'], 'agent':True})
    assert response.status_code == 422


def test_agent_without_model_falls_back_to_honest_local_retrieval(client, monkeypatch):
    monkeypatch.setattr(llm, 'configured', lambda: False)
    response = client.post('/api/ask', json={'question':'Adam?', 'agent':True}).json()
    assert response['mode'] == 'evidence_only' and response['execution'] == 'local'


def test_planner_deadline_skips_calls_and_returns_no_invented_result(client, monkeypatch):
    ticks = iter([0, 170])
    monkeypatch.setattr(agent, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(llm, 'tool_turn', lambda *a, **kw: pytest.fail('no budget left'))
    events = list(agent.run('Adam?', ['alpha']))
    assert events[-1]['data']['bounded'] is True
    assert events[-1]['data']['mode'] == 'insufficient'


def test_native_tool_protocol_preserves_server_reasoning_only(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://api.xiaomimimo.com/v1')
    monkeypatch.setenv('LLM_API_KEY', 'dummy')
    monkeypatch.setenv('LLM_MODEL', 'mimo-v2.6-pro')
    original = httpx.Client
    def handler(request):
        payload = json.loads(request.content)
        assert payload['tool_choice'] == 'auto'
        assert payload['thinking'] == {'type':'disabled'}
        return httpx.Response(200, json={'choices':[{'finish_reason':'tool_calls','message':{'content':None,'reasoning_content':'private','tool_calls':[call('library_catalog', {})]}}]})
    monkeypatch.setattr(llm.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    value = llm.tool_turn([{'role':'user','content':'test'}], agent.tool_schemas())
    assert value['tool_calls'][0]['id'] == 'call1' and value['reasoning_content'] == 'private'


@pytest.mark.parametrize('calls', [[call('library_catalog', {})] * 2, [{'id':'x','type':'function','function':{'name':'x','arguments':{}}}]])
def test_native_tool_protocol_rejects_invalid_envelopes(monkeypatch, calls):
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.test/v1')
    monkeypatch.setenv('LLM_API_KEY', 'dummy')
    monkeypatch.setenv('LLM_MODEL', 'fixture')
    original = httpx.Client
    monkeypatch.setattr(llm.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'choices':[{'message':{'tool_calls':calls}}]})), **kw))
    with pytest.raises(llm.ModelError, match='格式异常'):
        llm.tool_turn([], agent.tool_schemas())
