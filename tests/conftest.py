"""普通回归测试不得加载用户的大体积向量模型。"""
import pytest


@pytest.fixture(autouse=True)
def isolate_embedding_config(monkeypatch):
    monkeypatch.delenv('EMBEDDING_MODEL', raising=False)
    monkeypatch.setenv('EMBEDDING_PROVIDER', 'api')
    monkeypatch.setenv('EMBEDDING_AUTO_INDEX', '0')
    monkeypatch.delenv('RERANKER_MODEL', raising=False)
    monkeypatch.delenv('DEMO_PASSWORD',raising=False)
