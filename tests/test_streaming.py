"""使用模拟 HTTP 响应检查流式输出与声明级校验，不调用付费模型。"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import db, llm, retrieval
from backend.app import app


EVIDENCE = [{'source_id': 'S1', 'chunk_id': 'c1', 'paper_id': 'p1', 'title': 'Fixture Paper',
             'page': 3, 'text': 'Adam optimizer learning rate 0.001.', 'bbox': [1, 2, 3, 4], 'score': .5}]


def run_stream(chunks, evidence=None):
    monkeyed = llm.complete_stream
    llm.complete_stream = lambda *a, **kw: iter(chunks)
    try:
        return list(retrieval.generate_answer_stream('question', evidence or EVIDENCE, []))
    finally:
        llm.complete_stream = monkeyed


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(llm, 'configured', lambda: True)
    monkeypatch.delenv('EMBEDDING_MODEL', raising=False)
    with TestClient(app) as c:
        db.execute("INSERT INTO papers(id,title,status,page_count,created) VALUES('alpha','alpha fixture','ready',2,'2026-10-28')")
        db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
                   ('alpha-1', 'alpha', 1, 'Synthetic fixture: Adam optimizer learning rate 0.001.', '[10,20,100,200]', None))
        yield c


def test_claim_lines_validate_before_result():
    result = run_stream(['Learning rate is 0.001. ||| ', 'S1', '\nAdam is the optimizer [S1]. ||| S1\n'])
    kinds = [e['event'] for e in result]
    assert kinds == ['token', 'token', 'claim', 'claim', 'result']
    assert result[0]['data']['text'].startswith('Learning rate')
    assert result[2]['data'] == {'text': 'Learning rate is 0.001.', 'source_ids': ['S1']}
    final = result[-1]['data']
    assert final['mode'] == 'generated'
    assert final['answer'] == 'Learning rate is 0.001. [S1]\n\nAdam is the optimizer [S1]. [S1]'
    assert final['sources'] == EVIDENCE
    assert final['trace'][-1]['tool'] == 'validate_citations'


def test_unsourced_insufficiency_line_is_allowed():
    result = run_stream(['证据不足，无法确定。 ||| -\n'])
    final = result[-1]['data']
    assert final['answer'] == '证据不足，无法确定。'
    assert final['sources'] == []


def test_json_claims_stream_across_arbitrary_boundaries():
    raw = json.dumps({'claims': [{'text': '学习率为 0.001。', 'source_ids': ['S1']},
                                 {'text': '现有证据不足以确定序列号。', 'source_ids': []}]}, ensure_ascii=False)
    events = run_stream([raw[i:i+4] for i in range(0, len(raw), 4)])
    assert any(e['event'] == 'token' and '学习率' in e['data']['text'] for e in events)
    assert len([e for e in events if e['event'] == 'claim']) == 2
    assert events[-1]['data']['sources'] == EVIDENCE
    assert '0.001。 [S1]' in events[-1]['data']['answer']


@pytest.mark.parametrize('raw,match', [
    ('{"claims":[{"text":"fake","source_ids":["S99"]}]}', '不存在'),
    ('{"claims":[{"text":"fake","source_ids":[]}]}', '没有引用'),
    ('{"claims":[{"text":"fact","source_ids":"S1"}]}', '格式'),
    ('{"claims":[{"text":"fact","source_ids":["S1"]}', '不完整'),
    ('{"claims":[{"text":"fact","source_ids":["S1"]}]} extra', '不完整'),
    ('{"claims":[]}', '缺少有效结论'),
])
def test_json_output_keeps_citation_and_completion_guards(raw, match):
    with pytest.raises(llm.ModelError, match=match):
        run_stream([raw])


def test_line_compatibility_does_not_require_spaces_around_separator():
    assert run_stream(['fact|||S1'])[-1]['data']['sources'] == EVIDENCE


@pytest.mark.parametrize('chunks,match', [
    (['unsupported ||| FAKE\n'], '不存在'),
    (['made up fact ||| -\n'], '没有引用'),
    (['claim [S99]. ||| S1\n'], '未声明'),
    (['no separator here\n'], '引用标注'),
    (['\n \n'], '缺少有效结论'),
])
def test_violation_stops_display(chunks, match):
    with pytest.raises(llm.ModelError, match=match):
        run_stream(chunks)


def test_claim_count_is_capped():
    with pytest.raises(llm.ModelError, match='超出限制'):
        run_stream([f'c{i} ||| S1\n' for i in range(13)])


def test_stream_endpoint_emits_token_claim_and_result(client, monkeypatch):
    monkeypatch.setattr(llm, 'complete_stream', lambda *a, **kw: iter(['Learning rate is 0.001. ||| ', 'S1\n']))
    text = client.post('/api/ask/stream', json={'question': 'Adam optimizer'}).text
    assert 'event: step' in text
    assert 'event: token' in text
    assert 'event: claim' in text
    assert 'event: result' in text
    assert 'Learning rate is 0.001. [S1]' in text


def test_author_retrieval_keeps_header_order_and_affiliations(client):
    blocks = [{'bbox': [350, 200.1, 470, 230], 'text': 'Third Author\nUniversity C'},
              {'bbox': [180, 200, 310, 230], 'text': 'Second Author\nUniversity B'},
              {'bbox': [50, 100, 170, 130], 'text': 'First Author\nUniversity A'}]
    db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?)', ('alpha', 1, 'author', json.dumps(blocks), 600, 800))
    db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
               ('alpha-author', 'alpha', 1, 'author affiliations on first page', '[50,100,470,230]', None))
    evidence, _ = retrieval.retrieve('作者机构 affiliation', ['alpha'])
    first = evidence[0]
    assert first['page'] == 1
    assert first['text'].index('First Author') < first['text'].index('Second Author') < first['text'].index('Third Author')
    assert 'University B' in first['text']


def test_stream_endpoint_reports_violation_as_error(client, monkeypatch):
    monkeypatch.setattr(llm, 'complete_stream', lambda *a, **kw: iter(['unsupported ||| FAKE\n']))
    text = client.post('/api/ask/stream', json={'question': 'Adam optimizer'}).text
    assert 'event: error' in text and 'event: result' not in text


@pytest.fixture
def mimo_env(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://api.xiaomimimo.com/v1')
    monkeypatch.setenv('LLM_API_KEY', 'dummy-test-only-key')
    monkeypatch.setenv('LLM_MODEL', 'mimo-v2.6-pro')
    monkeypatch.delenv('LLM_MAX_COMPLETION_TOKENS', raising=False)


def mock_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(llm.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_complete_stream_yields_only_content_deltas(monkeypatch, mimo_env):
    def handler(request):
        payload = json.loads(request.content)
        assert payload['stream'] is True
        assert payload['max_completion_tokens'] == 4096
        assert 'response_format' not in payload
        body = ('data: {"choices":[{"delta":{"reasoning_content":"private thoughts","content":""}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                'data: [DONE]\n\n')
        return httpx.Response(200, content=body.encode(), headers={'content-type': 'text/event-stream'})
    mock_transport(monkeypatch, handler)
    assert list(llm.complete_stream('system', 'user')) == ['Hel', 'lo']


def test_complete_stream_rejects_truncation(monkeypatch, mimo_env):
    def handler(request):
        body = 'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=body.encode(), headers={'content-type': 'text/event-stream'})
    mock_transport(monkeypatch, handler)
    with pytest.raises(llm.ModelError, match='截断'):
        list(llm.complete_stream('system', 'user'))


def test_complete_stream_json_mode(monkeypatch, mimo_env):
    def handler(request):
        assert json.loads(request.content)['response_format'] == {'type': 'json_object'}
        return httpx.Response(200, content=b'data: [DONE]\n\n')
    mock_transport(monkeypatch, handler)
    assert list(llm.complete_stream('return JSON', 'user', json_output=True)) == []


def test_complete_stream_error_does_not_echo_credentials(monkeypatch, mimo_env):
    mock_transport(monkeypatch, lambda request: httpx.Response(401, json={'error': {'message': 'dummy-test-only-key secret'}}))
    with pytest.raises(llm.ModelError) as error:
        list(llm.complete_stream('system', 'private payload'))
    assert '401' in str(error.value)
    assert 'dummy-test-only-key' not in str(error.value)
    assert 'private payload' not in str(error.value)
