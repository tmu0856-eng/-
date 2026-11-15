"""服务端模型适配，未配置凭据时不生成虚构输出。"""
import json
import os
from urllib.parse import urlsplit
import httpx


class ModelError(Exception):
    pass


def configured():
    return all(os.getenv(k, '').strip() for k in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL'))


def endpoint(base, route):
    return base.rstrip('/') + '/' + route


def tool_turn(messages, tools, timeout=90):
    """执行一轮原生工具调用，推理字段仅保留在服务端。"""
    if not configured():
        raise ModelError('尚未配置模型，无法启动自主工具调用。')
    payload = {'model': os.environ['LLM_MODEL'], 'messages': messages,
               'tools': tools, 'tool_choice': 'auto'}
    if urlsplit(os.environ['LLM_BASE_URL']).hostname == 'api.xiaomimimo.com':
        payload['max_completion_tokens'] = 2048
        payload['thinking'] = {'type': 'disabled'}
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(15, timeout)), trust_env=False) as client:
            response = client.post(endpoint(os.environ['LLM_BASE_URL'], 'chat/completions'),
                                   headers={'Authorization': 'Bearer ' + os.environ['LLM_API_KEY']}, json=payload)
            response.raise_for_status()
            choice = response.json()['choices'][0]
        if choice.get('finish_reason') in ('length', 'content_filter', 'repetition_truncation'):
            raise ModelError('工具规划输出未正常完成，已停止执行；可简化问题后重试。')
        message = choice['message']
        if not isinstance(message, dict):
            raise ValueError('Invalid assistant message')
        calls = message.get('tool_calls') or []
        if not isinstance(calls, list) or len(calls) > 3:
            raise ModelError('单轮工具调用超出上限（最多 3 个），已停止执行。')
        seen = set()
        for call in calls:
            if (not isinstance(call, dict) or call.get('type') != 'function'
                    or not isinstance(call.get('id'), str) or not 1 <= len(call['id']) <= 200
                    or call['id'] in seen or not isinstance(call.get('function'), dict)
                    or not isinstance(call['function'].get('name'), str)
                    or not isinstance(call['function'].get('arguments'), str)
                    or len(call['function']['arguments']) > 6000):
                raise ModelError('模型工具调用格式异常，未执行。')
            seen.add(call['id'])
        # 部分服务要求后续轮次携带推理字段，因此仅在服务端保留；
        # 不向接口调用方、日志或浏览器输出该字段。
        result = {'role': 'assistant', 'content': message.get('content') or None}
        if calls:
            result['tool_calls'] = calls
        if isinstance(message.get('reasoning_content'), str):
            result['reasoning_content'] = message['reasoning_content']
        return result
    except httpx.HTTPStatusError as exc:
        raise ModelError(f'工具规划服务请求失败（HTTP {exc.response.status_code}），请检查模型工具调用支持与额度。') from exc
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise ModelError('工具规划连接或返回格式异常，请稍后重试。') from exc


def _token_budget():
    try:
        budget = int(os.getenv('LLM_MAX_COMPLETION_TOKENS', '4096'))
    except ValueError as exc:
        raise ModelError('LLM_MAX_COMPLETION_TOKENS 必须是整数。') from exc
    if not 256 <= budget <= 32768:
        raise ModelError('LLM_MAX_COMPLETION_TOKENS 请设置在 256–32768 之间。')
    return budget


def complete(system, user, json_output=False, *, timeout=120):
    if not configured():
        raise ModelError('尚未配置模型。请在 .env 设置 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL，然后重启服务。')
    payload = {
        'model': os.environ['LLM_MODEL'], 'temperature': 0.15,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
    }
    # MiMo 官方接口支持 JSON 模式；专用字段仅发送至已核验的主机，
    # 其他兼容服务仍使用通用请求格式。
    if urlsplit(os.environ['LLM_BASE_URL']).hostname == 'api.xiaomimimo.com':
        payload['max_completion_tokens'] = _token_budget()
        if json_output:
            payload['response_format'] = {'type': 'json_object'}
    if json_output:
        system += '\n只输出一个 JSON 对象，不要输出 Markdown 代码围栏。'
        payload['messages'][0]['content'] = system
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(15, timeout)), trust_env=False) as client:
            response = client.post(endpoint(os.environ['LLM_BASE_URL'], 'chat/completions'),
                                   headers={'Authorization': 'Bearer ' + os.environ['LLM_API_KEY']}, json=payload)
            response.raise_for_status()
            choice = response.json()['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise ModelError('模型输出因 Token 额度被截断，未展示不完整回答；可缩短输入或调整 LLM_MAX_COMPLETION_TOKENS。')
            content = choice['message']['content']
        if not isinstance(content, str) or not content.strip():
            raise ModelError('模型返回了空内容。')
        if json_output:
            clean = content.strip()
            if clean.startswith('```'):
                clean = clean.split('\n', 1)[1].rsplit('```', 1)[0]
            value = json.loads(clean)
            if not isinstance(value, dict):
                raise ModelError('模型返回格式不是 JSON 对象。')
            return value
        return content
    except httpx.HTTPStatusError as exc:
        raise ModelError(f'模型服务请求失败（HTTP {exc.response.status_code}），请检查模型名、接口地址与额度。') from exc
    except (httpx.HTTPError, KeyError, ValueError, IndexError, TypeError) as exc:
        # 不返回服务商原始错误响应，避免其中回显私人请求内容。
        raise ModelError('模型连接或返回格式异常，请检查配置后重试。') from exc


def complete_stream(system, user, *, timeout=120, json_output=False):
    """按 SSE 到达顺序输出文本增量，不输出服务商推理字段。"""
    if not configured():
        raise ModelError('尚未配置模型。请在 .env 设置 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL，然后重启服务。')
    payload = {
        'model': os.environ['LLM_MODEL'], 'temperature': 0.15, 'stream': True,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
    }
    if json_output:
        payload['response_format'] = {'type': 'json_object'}
    if urlsplit(os.environ['LLM_BASE_URL']).hostname == 'api.xiaomimimo.com':
        payload['max_completion_tokens'] = _token_budget()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(15, timeout)), trust_env=False) as client:
            with client.stream('POST', endpoint(os.environ['LLM_BASE_URL'], 'chat/completions'),
                               headers={'Authorization': 'Bearer ' + os.environ['LLM_API_KEY']}, json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        break
                    chunk = json.loads(data)
                    choice = (chunk.get('choices') or [{}])[0]
                    content = (choice.get('delta') or {}).get('content')
                    if isinstance(content, str) and content:
                        yield content
                    if choice.get('finish_reason') == 'length':
                        raise ModelError('模型输出因 Token 额度被截断，未展示不完整回答；可缩短输入或调整 LLM_MAX_COMPLETION_TOKENS。')
    except ModelError:
        raise
    except httpx.HTTPStatusError as exc:
        raise ModelError(f'模型服务请求失败（HTTP {exc.response.status_code}），请检查模型名、接口地址与额度。') from exc
    except (httpx.HTTPError, KeyError, ValueError, IndexError, TypeError) as exc:
        raise ModelError('模型连接或返回格式异常，请检查配置后重试。') from exc


def embed(texts):
    from . import embeddings
    return embeddings.encode(texts)


def remote_embed(texts):
    model = os.getenv('EMBEDDING_MODEL', '').strip()
    base = os.getenv('EMBEDDING_BASE_URL') or os.getenv('LLM_BASE_URL', '')
    key = os.getenv('EMBEDDING_API_KEY') or os.getenv('LLM_API_KEY', '')
    if not model or not base or not key:
        return None
    try:
        with httpx.Client(timeout=90, trust_env=False) as client:
            response = client.post(endpoint(base, 'embeddings'), headers={'Authorization': 'Bearer ' + key},
                                   json={'model': model, 'input': texts})
            response.raise_for_status()
            data = sorted(response.json()['data'], key=lambda x: x['index'])
            if [item['index'] for item in data] != list(range(len(texts))):
                raise ValueError('Embedding indices mismatch')
            result = [item['embedding'] for item in data]
            if len(result) != len(texts):
                raise ValueError('Embedding count mismatch')
            return result
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise ModelError('Embedding 服务调用失败；未将词频向量当作语义向量。请检查配置。') from exc
