"""创建隔离的本地评审配置，不打印或公开密钥。"""
import os
import secrets
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / '.env.demo'
    access = ROOT / '.local/reviewer-access.txt'
    if target.exists():
        raise SystemExit('.env.demo already exists; keep the existing reviewer credentials. Nothing overwritten.')
    source = dotenv_values(ROOT / '.env')
    values = dotenv_values(ROOT / 'deploy/demo.env.example')
    for name in ('LLM_BASE_URL','LLM_API_KEY','LLM_MODEL','LLM_MAX_COMPLETION_TOKENS'):
        if source.get(name):
            values[name] = source[name]
    # Windows 默认使用现有 CUDA 模型，Compose 配置会改为 CPU。
    values.update({'APP_HOST':'127.0.0.1', 'APP_PORT':'8767',
                   'RESEARCHDOCK_DATA':str(ROOT / 'data/demo-public'),
                   'EMBEDDING_DEVICE':'auto','RERANKER_DEVICE':'auto',
                   'DEMO_PASSWORD':secrets.token_urlsafe(18),
                   'DEMO_SESSION_SECRET':secrets.token_urlsafe(36)})
    # JSON 双引号与 python-dotenv 兼容，不进行命令行求值。
    import json
    text = '# Private reviewer configuration. Never commit.\n' + ''.join(
        f'{name}={json.dumps(str(value or ""),ensure_ascii=False)}\n' for name,value in values.items())
    target.write_text(text,encoding='utf-8')
    access.parent.mkdir(exist_ok=True)
    access.write_text('Local reviewer access (private)\nURL: http://127.0.0.1:8767\n'
                      f'Username: {values["DEMO_USERNAME"]}\nPassword: {values["DEMO_PASSWORD"]}\n'
                      'Before public HTTPS use set DEMO_COOKIE_SECURE=1 in .env.demo and restart.\n',encoding='utf-8')
    print('Created private .env.demo and .local/reviewer-access.txt; no credentials printed.')
    print('Start isolated demo: python scripts/run_demo.py')


if __name__=='__main__':
    main()
