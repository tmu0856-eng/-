"""模型适配测试仅使用虚拟凭据与模拟 HTTP 响应。"""
import json

import httpx
import pytest

from backend import llm


@pytest.fixture(autouse=True)
def model_env(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://api.xiaomimimo.com/v1')
    monkeypatch.setenv('LLM_API_KEY', 'dummy-test-only-key')
    monkeypatch.setenv('LLM_MODEL', 'mimo-v2.6-flash')
    monkeypatch.delenv('LLM_MAX_COMPLETION_TOKENS', raising=False)


def mock_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(llm.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.parametrize('model', ['mimo-v2.6-flash', 'mimo-v2.6-pro'])
def test_mimo_uses_json_mode_and_preserves_exact_model(monkeypatch, model):
    monkeypatch.setenv('LLM_MODEL', model)
    def handler(request):
        value = json.loads(request.content)
        assert str(request.url) == 'https://api.xiaomimimo.com/v1/chat/completions'
        assert value['model'] == model
        assert value['max_completion_tokens'] == 4096
        assert value['response_format'] == {'type': 'json_object'}
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]})
    mock_transport(monkeypatch, handler)
    assert llm.complete('Return JSON', 'test', True) == {'ok': True}


def test_provider_error_does_not_echo_response_or_credentials(monkeypatch):
    mock_transport(monkeypatch, lambda request: httpx.Response(401, json={'error': {'message':'dummy-test-only-key private payload'}}))
    with pytest.raises(llm.ModelError) as error:
        llm.complete('test', 'private payload')
    assert '401' in str(error.value)
    assert 'dummy-test-only-key' not in str(error.value)
    assert 'private payload' not in str(error.value)


def test_truncated_output_is_rejected(monkeypatch):
    mock_transport(monkeypatch, lambda request: httpx.Response(200, json={'choices':[{'finish_reason':'length','message':{'content':'{"partial":'}}]}))
    with pytest.raises(llm.ModelError, match='截断'):
        llm.complete('test', 'test', True)


def test_non_mimo_provider_does_not_receive_mimo_fields(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.test/v1')
    def handler(request):
        value = json.loads(request.content)
        assert 'max_completion_tokens' not in value
        assert 'response_format' not in value
        return httpx.Response(200, json={'choices':[{'message':{'content':'ok'}}]})
    mock_transport(monkeypatch, handler)
    assert llm.complete('test', 'test') == 'ok'


def test_invalid_budget_does_not_call_provider(monkeypatch):
    monkeypatch.setenv('LLM_MAX_COMPLETION_TOKENS', 'not-a-number')
    with pytest.raises(llm.ModelError, match='整数'):
        llm.complete('test', 'test')
