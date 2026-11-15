import json
import sys
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import db, embeddings, llm, retrieval
from backend.app import app


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('EMBEDDING_MODEL', 'fixture-encoder')
    monkeypatch.setattr(embeddings, 'space_id', lambda: 'fixture-v1')
    with TestClient(app) as client:
        db.execute("INSERT INTO papers(id,title,status) VALUES('p1','Paper one','ready')")
        db.execute("INSERT INTO papers(id,title,status) VALUES('p2','Paper two','ready')")
        for ident, pid, text in [('c1', 'p1', 'Hardware consists of eight P100 GPUs.'), ('c2', 'p1', 'An unrelated gardening topic.'), ('c3', 'p2', 'Other hardware.')]:
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)', (ident, pid, 7, text, '[1,2,3,4]', None))
        yield client


def test_index_is_resumable_and_model_scoped(library, monkeypatch):
    calls = []
    monkeypatch.setattr(embeddings, 'encode', lambda texts: calls.append(texts) or [[1., 0.] for _ in texts])
    embeddings.index_pending()
    assert db.one('SELECT COUNT(*) n FROM chunk_vectors')['n'] == 3
    embeddings.index_pending()
    assert len(calls) == 1
    monkeypatch.setattr(embeddings, 'space_id', lambda: 'fixture-v2')
    embeddings.index_pending()
    assert len(calls) == 2
    assert {v['space'] for v in db.rows('SELECT space FROM chunk_vectors')} == {'fixture-v2'}


def test_paper_delete_removes_vectors(library, monkeypatch):
    monkeypatch.setattr(embeddings, 'encode', lambda texts: [[1., 0.] for _ in texts])
    embeddings.index_pending()
    db.execute("DELETE FROM papers WHERE id='p1'")
    assert db.one('SELECT COUNT(*) n FROM chunk_vectors')['n'] == 1


def test_encode_deleted_text_is_not_stored(library, monkeypatch):
    def encode(texts):
        db.execute('DELETE FROM chunks')
        return [[1., 0.] for _ in texts]
    monkeypatch.setattr(embeddings, 'encode', encode)
    embeddings.index_pending()
    assert db.one('SELECT COUNT(*) n FROM chunk_vectors')['n'] == 0


def add_vectors(space='fixture-v1'):
    for key, vector in [('c1', [1., 0.]), ('c2', [0., 1.]), ('c3', [1., 0.])]:
        db.execute('INSERT INTO chunk_vectors VALUES(?,?,?,?)', (key, space, 2, np.asarray(vector, dtype='<f4').tobytes()))


def test_semantic_only_match_and_scope(library, monkeypatch):
    add_vectors()
    monkeypatch.setattr(llm, 'embed', lambda texts: [[1., 0.]])
    values, info = retrieval.retrieve('该实验用了哪种运算装备', ['p1'])
    assert [v['chunk_id'] for v in values] == ['c1']
    assert 'fixture-encoder' in info['mode']
    assert info['indexed_chunks'] == 2


def test_same_dimension_other_model_vectors_are_not_used(library, monkeypatch):
    add_vectors('different-model')
    monkeypatch.setattr(llm, 'embed', lambda texts: pytest.fail('Should not encode against mismatched model'))
    values, info = retrieval.retrieve('该实验用了哪种运算装备', ['p1'])
    assert not values
    assert 'fixture-encoder' not in info['mode']


def test_embedding_failure_falls_back_to_lexical(library, monkeypatch):
    add_vectors()
    def fail(texts):
        raise llm.ModelError('fixture unavailable')
    monkeypatch.setattr(llm, 'embed', fail)
    values, info = retrieval.retrieve('P100', ['p1'])
    assert values[0]['chunk_id'] == 'c1'
    assert '退回词频' in info['warning']


@pytest.mark.parametrize('values', [[[float('nan'), 1]], [[0., 0.]], [[1., 0.], [1., 0.]], [[1., 0.], [1.]]])
def test_invalid_embeddings_rejected(monkeypatch, values):
    monkeypatch.setenv('EMBEDDING_MODEL', 'fixture')
    monkeypatch.setattr(llm, 'remote_embed', lambda texts: values)
    with pytest.raises(llm.ModelError):
        embeddings.encode(['one text'])


def test_local_space_includes_revision_and_length(tmp_path, monkeypatch):
    monkeypatch.setenv('EMBEDDING_PROVIDER', 'local')
    monkeypatch.setenv('EMBEDDING_MODEL', 'BAAI/bge-m3')
    monkeypatch.setenv('EMBEDDING_LOCAL_PATH', str(tmp_path))
    (tmp_path / 'researchdock-model.json').write_text(json.dumps({'model': 'BAAI/bge-m3', 'revision': 'a'*40}))
    (tmp_path / 'pytorch_model.bin').touch()
    a = embeddings.space_id()
    monkeypatch.setenv('EMBEDDING_MAX_LENGTH', '1024')
    assert a != embeddings.space_id()
    assert 'a'*40 in a


def test_missing_local_files_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv('EMBEDDING_PROVIDER', 'local')
    monkeypatch.setenv('EMBEDDING_MODEL', 'BAAI/bge-m3')
    monkeypatch.setenv('EMBEDDING_LOCAL_PATH', str(tmp_path))
    with pytest.raises(llm.ModelError, match='download_bge_m3'):
        embeddings.space_id()


def test_status_reports_current_model_coverage(library, monkeypatch):
    add_vectors()
    response = library.get('/api/embeddings/status')
    assert response.status_code == 200
    assert response.json()['indexed_chunks'] == 3
    assert response.json()['pending_chunks'] == 0


def test_reindex_requires_configuration(library, monkeypatch):
    monkeypatch.delenv('EMBEDDING_MODEL')
    assert library.post('/api/embeddings/reindex').status_code == 422


def test_local_encoder_reuses_loaded_model_and_normalizes(tmp_path, monkeypatch):
    monkeypatch.setenv('EMBEDDING_PROVIDER', 'local')
    monkeypatch.setenv('EMBEDDING_MODEL', 'BAAI/bge-m3')
    monkeypatch.setenv('EMBEDDING_LOCAL_PATH', str(tmp_path))
    monkeypatch.setattr(embeddings, '_MODEL', None)
    monkeypatch.setattr(embeddings, '_MODEL_KEY', None)
    (tmp_path / 'researchdock-model.json').write_text(json.dumps({'model': 'BAAI/bge-m3', 'revision': 'a'*40}))
    (tmp_path / 'pytorch_model.bin').touch()
    loads = []
    class FakeModel:
        def __init__(self, *args, **kwargs):
            loads.append(kwargs)
        def get_sentence_embedding_dimension(self):
            return 1024
        def encode(self, texts, **kwargs):
            return np.ones((len(texts), 1024), dtype=np.float32)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True),
                        float16='float16', float32='float32', set_num_threads=lambda n: None, inference_mode=nullcontext))
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=FakeModel))
    for _ in range(2):
        values = embeddings.encode(['fixture'])
        assert np.linalg.norm(values[0]) == pytest.approx(1.0)
    assert len(loads) == 1
    assert loads[0]['trust_remote_code'] is False
    assert loads[0]['local_files_only'] is True
    assert loads[0]['device'] == 'cuda'
    assert loads[0]['model_kwargs']['torch_dtype'] == 'float16'


def test_index_configuration_change_never_commits_mixed_vectors(library, monkeypatch):
    current = ['fixture-v1']
    monkeypatch.setattr(embeddings, 'space_id', lambda: current[0])
    def encode(texts):
        current[0] = 'fixture-v2'
        return [[1., 0.] for _ in texts]
    monkeypatch.setattr(embeddings, 'encode', encode)
    with pytest.raises(llm.ModelError, match='配置已变化'):
        embeddings.index_pending()
    assert db.one('SELECT COUNT(*) n FROM chunk_vectors')['n'] == 0


def test_shutdown_does_not_commit_inflight_batch(library, monkeypatch):
    stop = threading.Event()
    def encode(texts):
        stop.set()
        return [[1., 0.] for _ in texts]
    monkeypatch.setattr(embeddings, 'encode', encode)
    embeddings.index_pending(stop)
    assert db.one('SELECT COUNT(*) n FROM chunk_vectors')['n'] == 0


def test_repeated_background_requests_are_coalesced(library, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []
    def index(stop):
        calls.append(True)
        started.set()
        assert release.wait(2)
    monkeypatch.setattr(embeddings, 'index_pending', index)
    assert embeddings.start_indexing() is True
    assert started.wait(2)
    thread = embeddings._THREAD
    try:
        assert embeddings.start_indexing() is False
    finally:
        release.set()
        thread.join(3)
    assert len(calls) == 2
    assert embeddings._THREAD is None


def test_background_error_is_visible_and_leaves_text_intact(library, monkeypatch):
    def fail(stop):
        raise llm.ModelError('Fixture encoder unavailable')
    monkeypatch.setattr(embeddings, 'index_pending', fail)
    embeddings.start_indexing()
    thread = embeddings._THREAD
    if thread:
        thread.join(3)
    assert 'unavailable' in library.get('/api/embeddings/status').json()['error']
    assert db.one('SELECT COUNT(*) n FROM chunks')['n'] == 3
