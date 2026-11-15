"""检查规划、检索核读与综合阶段的交接，不调用付费模型。"""
import json

import pytest
from fastapi.testclient import TestClient

from backend import agent, db, llm
from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(llm, 'configured', lambda: True)
    for key in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL', 'EMBEDDING_MODEL'):
        monkeypatch.delenv(key, raising=False)
    with TestClient(app) as client:
        db.execute("INSERT INTO papers(id,title,status,page_count,created) VALUES('alpha','alpha fixture','ready',2,'2026-10-28')")
        db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
                   ('alpha-1', 'alpha', 1, 'Synthetic fixture: Adam optimizer learning rate 0.001.', '[10,20,100,200]', None))
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


def stub_answer(monkeypatch):
    monkeypatch.setattr(llm, 'complete_stream', lambda *a, **kw: iter(['Learning rate is 0.001. ||| S1\n']))


def step_data(events, tool):
    return next(e['data'] for e in events if e['event'] == 'step' and e['data'].get('tool') == tool)


def test_plan_hands_off_to_reader_with_role_labels(client, monkeypatch):
    monkeypatch.setattr(llm, 'complete', lambda *a, **kw: {'sub_queries': [{'query': 'Adam optimizer', 'paper_id': 'alpha'}]})
    requests = turns(monkeypatch,
        {'role': 'assistant', 'tool_calls': [call('search_evidence', {'query': 'Adam', 'paper_id': 'alpha'})]},
        {'role': 'assistant', 'content': 'finished'})
    stub_answer(monkeypatch)
    events = list(agent.run('learning rate?', ['alpha']))
    result = events[-1]['data']
    assert result['execution'] == 'agent' and result['sources'][0]['source_id'] == 'S1'
    roles = [e['data'].get('role') for e in events if e['event'] == 'step']
    assert '规划' in roles and '核读' in roles and '综合' in roles
    plan = step_data(events, 'planner')
    assert plan['status'] == 'done' and plan['arguments']['sub_queries'][0]['query'] == 'Adam optimizer'
    assert 'Adam optimizer' in requests[0]['messages'][1]['content']


@pytest.mark.parametrize('plan', [
    {'sub_queries': [{'query': 'Adam', 'paper_id': 'unknown'}]},
    {'sub_queries': [{'query': 'Adam'}] * 5},
    {'sub_queries': [{'query': 'a'}]},
    {'sub_queries': [{'query': 'Adam', 'extra': 1}]},
    {'sub_queries': 'not-a-list'},
    {'notes': 'no sub_queries'},
])
def test_invalid_plan_falls_back_without_blocking_reader(client, monkeypatch, plan):
    monkeypatch.setattr(llm, 'complete', lambda *a, **kw: plan)
    turns(monkeypatch,
        {'role': 'assistant', 'tool_calls': [call('search_evidence', {'query': 'Adam'})]},
        {'role': 'assistant', 'content': 'finished'})
    stub_answer(monkeypatch)
    events = list(agent.run('Adam?', ['alpha']))
    assert step_data(events, 'planner')['status'] == 'skipped'
    assert events[-1]['data']['execution'] == 'agent'
    assert events[-1]['data']['sources']


def test_planner_outage_never_blocks_reader(client, monkeypatch):
    def fail(*a, **kw):
        raise llm.ModelError('planner down')
    monkeypatch.setattr(llm, 'complete', fail)
    turns(monkeypatch,
        {'role': 'assistant', 'tool_calls': [call('search_evidence', {'query': 'Adam'})]},
        {'role': 'assistant', 'content': 'finished'})
    stub_answer(monkeypatch)
    events = list(agent.run('Adam?', ['alpha']))
    assert step_data(events, 'planner')['status'] == 'skipped'
    assert events[-1]['data']['mode'] == 'generated'


def test_plan_queries_validates_before_handoff():
    papers = [{'id': 'alpha', 'title': 'A'}]
    monkeyed = llm.complete
    try:
        llm.complete = lambda *a, **kw: {'sub_queries': [{'query': ' Adam optimizer ', 'paper_id': 'alpha'}, {'query': 'learning rate'}]}
        assert agent.plan_queries('q', papers) == [{'query': 'Adam optimizer', 'paper_id': 'alpha'}, {'query': 'learning rate', 'paper_id': None}]
        llm.complete = lambda *a, **kw: {'sub_queries': [{'query': 'Adam', 'paper_id': 'beta'}]}
        assert agent.plan_queries('q', papers) is None
        llm.complete = lambda *a, **kw: {'sub_queries': []}
        assert agent.plan_queries('q', papers) is None
    finally:
        llm.complete = monkeyed
