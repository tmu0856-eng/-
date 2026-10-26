"""Server-side model adapter. Missing credentials never produce fabricated output."""
import json
import os
import httpx


class ModelError(Exception):
    pass


def configured():
    return all(os.getenv(k, '').strip() for k in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL'))


def endpoint(base, route):
    return base.rstrip('/') + '/' + route


def complete(system, user, json_output=False):
    if not configured():
        raise ModelError('尚未配置模型。请在 .env 设置 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL，然后重启服务。')
    payload = {
        'model': os.environ['LLM_MODEL'], 'temperature': 0.15,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
    }
    if json_output:
        system += '\n只输出一个 JSON 对象，不要输出 Markdown 代码围栏。'
        payload['messages'][0]['content'] = system
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=15), trust_env=False) as client:
            response = client.post(endpoint(os.environ['LLM_BASE_URL'], 'chat/completions'),
                                   headers={'Authorization': 'Bearer ' + os.environ['LLM_API_KEY']}, json=payload)
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content']
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
    except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
        # Do not return provider response bodies: they may echo private requests.
        raise ModelError('模型连接或返回格式异常，请检查配置后重试。') from exc


def embed(texts):
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
            result = [item['embedding'] for item in data]
            if len(result) != len(texts):
                raise ValueError('Embedding count mismatch')
            return result
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise ModelError('Embedding 服务调用失败；未将词频向量当作语义向量。请检查配置。') from exc
