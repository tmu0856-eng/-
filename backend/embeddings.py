"""本地 BGE-M3 稠密编码，按模型空间保存可续建的分块索引。"""
import hashlib
import json
import os
import threading
from pathlib import Path

import numpy as np

from . import db, llm
from .local_models import INFERENCE_LOCK

_ENCODE_LOCK = threading.Lock()
_STATE_LOCK = threading.RLock()
_MODEL = None
_MODEL_KEY = None
_RUNTIME = {'state': 'idle', 'device': '', 'error': ''}
_THREAD = None
_REQUESTED = False
_STOP = threading.Event()
_INDEX_ERROR = ''


def configured():
    return bool(os.getenv('EMBEDDING_MODEL', '').strip())


def provider():
    return os.getenv('EMBEDDING_PROVIDER', 'api').strip().lower()


def model_path():
    path = Path(os.getenv('EMBEDDING_LOCAL_PATH', '.local/models/bge-m3'))
    return (db.ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def max_length():
    try:
        value = int(os.getenv('EMBEDDING_MAX_LENGTH', '512'))
        if not 32 <= value <= 8192:
            raise ValueError()
        return value
    except ValueError as exc:
        raise llm.ModelError('EMBEDDING_MAX_LENGTH 必须为 32–8192 的整数。') from exc


def batch_size():
    try:
        value = int(os.getenv('EMBEDDING_BATCH_SIZE', '8'))
        if not 1 <= value <= 64:
            raise ValueError()
        return value
    except ValueError as exc:
        raise llm.ModelError('EMBEDDING_BATCH_SIZE 必须为 1–64 的整数。') from exc


def space_id():
    if not configured():
        return ''
    model = os.environ['EMBEDDING_MODEL'].strip()
    if provider() == 'local':
        if model != 'BAAI/bge-m3':
            raise llm.ModelError('当前本地适配器仅支持 BAAI/bge-m3。')
        try:
            manifest = json.loads((model_path() / 'researchdock-model.json').read_text(encoding='utf-8'))
            revision = manifest['revision']
            if manifest['model'] != model or not isinstance(revision, str) or len(revision) != 40:
                raise ValueError()
            if not any((model_path() / name).is_file() for name in ('model.safetensors', 'pytorch_model.bin')):
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise llm.ModelError('BGE-M3 本地模型未就绪，请先运行 scripts/download_bge_m3.py。') from exc
        return f'{model}@{revision}:cls:l2:max{max_length()}'
    if provider() != 'api':
        raise llm.ModelError('EMBEDDING_PROVIDER 仅支持 local 或 api。')
    address = os.getenv('EMBEDDING_BASE_URL') or os.getenv('LLM_BASE_URL', '')
    return 'api:' + hashlib.sha256((address + '\n' + model).encode()).hexdigest()


def _local_encode(texts):
    global _MODEL, _MODEL_KEY
    key = (str(model_path()), space_id(), os.getenv('EMBEDDING_DEVICE', 'auto'))
    with _ENCODE_LOCK, INFERENCE_LOCK:
        try:
            import torch
            if _MODEL_KEY != key:
                _RUNTIME.update(state='loading', error='')
                os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
                from sentence_transformers import SentenceTransformer
                device = key[2]
                if device == 'auto':
                    device = 'cuda' if torch.cuda.is_available() else 'cpu'
                torch.set_num_threads(4)
                model = SentenceTransformer(str(model_path()), device=device, local_files_only=True,
                                            trust_remote_code=False,
                                            model_kwargs={'torch_dtype': torch.float16 if device.startswith('cuda') else torch.float32})
                model.max_seq_length = max_length()
                if model.get_sentence_embedding_dimension() != 1024:
                    raise ValueError('Unexpected BGE-M3 dimension')
                _MODEL, _MODEL_KEY = model, key
                _RUNTIME.update(state='ready', device=device, error='')
            with torch.inference_mode():
                values = _MODEL.encode(texts, batch_size=batch_size(), normalize_embeddings=True,
                                       convert_to_numpy=True, show_progress_bar=False)
            _RUNTIME.update(state='ready', error='')
            return values
        except Exception as exc:
            _RUNTIME.update(state='error', error='BGE-M3 本地加载或编码失败，请检查模型文件、依赖与显存。')
            raise llm.ModelError(_RUNTIME['error']) from exc


def encode(texts):
    if not configured():
        return None
    if not texts:
        return []
    values = _local_encode(texts) if provider() == 'local' else llm.remote_embed(texts)
    if values is None:
        raise llm.ModelError('Embedding 接口缺少地址或密钥。')
    try:
        matrix = np.asarray(values, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(texts) or not matrix.shape[1] or not np.isfinite(matrix).all():
            raise ValueError()
        if provider() == 'local' and matrix.shape[1] != 1024:
            raise ValueError()
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        if np.any(norms < 1e-12):
            raise ValueError()
        return (matrix / norms).tolist()
    except (ValueError, TypeError) as exc:
        raise llm.ModelError('Embedding 返回数量、维度或数值异常，未写入索引。') from exc


def status():
    total = db.one("SELECT COUNT(*) n FROM chunks c JOIN papers p ON p.id=c.paper_id WHERE p.status IN ('ready','partial')")['n']
    error, space, indexed = _INDEX_ERROR or _RUNTIME.get('error', ''), '', 0
    if configured():
        try:
            space = space_id()
            indexed = db.one("SELECT COUNT(*) n FROM chunk_vectors v JOIN chunks c ON c.id=v.chunk_id JOIN papers p ON p.id=c.paper_id WHERE v.space=? AND p.status IN ('ready','partial')", (space,))['n']
        except llm.ModelError as exc:
            error = str(exc)
    return {'configured': configured(), 'provider': provider(), 'model': os.getenv('EMBEDDING_MODEL', ''),
            'dimensions': 1024 if provider() == 'local' else None, 'space': space,
            'indexed_chunks': indexed, 'total_chunks': total, 'pending_chunks': total-indexed,
            'indexing': _THREAD is not None, 'runtime': dict(_RUNTIME), 'error': error}


def index_pending(stop=None):
    """每批独立提交，重启后补建缺失或失效的向量。"""
    space = space_id()
    if not space:
        return
    while not (stop and stop.is_set()):
        chunks = db.rows("""SELECT c.id,c.text FROM chunks c JOIN papers p ON p.id=c.paper_id
            LEFT JOIN chunk_vectors v ON v.chunk_id=c.id AND v.space=?
            WHERE p.status IN ('ready','partial') AND v.chunk_id IS NULL ORDER BY c.paper_id,c.page,c.id LIMIT ?""",
                         (space, batch_size()))
        if not chunks:
            return
        values = encode([c['text'] for c in chunks])
        if space != space_id():
            raise llm.ModelError('向量模型配置已变化，停止本次索引，请重新补建。')
        if stop and stop.is_set():
            return
        with db.connect() as con:
            for chunk, value in zip(chunks, values):
                vector = np.asarray(value, dtype='<f4')
                # 删除或重新解析文本时，不保留失效向量。
                con.execute('''INSERT INTO chunk_vectors(chunk_id,space,dimensions,vector)
                    SELECT id,?,?,? FROM chunks WHERE id=? AND text=?
                    ON CONFLICT(chunk_id) DO UPDATE SET space=excluded.space,dimensions=excluded.dimensions,vector=excluded.vector''',
                            (space, len(value), vector.tobytes(), chunk['id'], chunk['text']))


def _worker():
    global _THREAD, _REQUESTED, _INDEX_ERROR
    try:
        while not _STOP.is_set():
            with _STATE_LOCK:
                _REQUESTED = False
            index_pending(_STOP)
            with _STATE_LOCK:
                if not _REQUESTED:
                    _THREAD = None
                    return
    except Exception as exc:
        with _STATE_LOCK:
            _INDEX_ERROR = str(exc) if isinstance(exc, llm.ModelError) else '向量索引失败，可重试补建；原文与词频检索仍可用。'
    finally:
        with _STATE_LOCK:
            if _THREAD is threading.current_thread():
                _THREAD = None


def start_indexing():
    global _THREAD, _REQUESTED, _INDEX_ERROR
    if not configured():
        return False
    with _STATE_LOCK:
        _REQUESTED = True
        if _THREAD is not None:
            return False
        _STOP.clear()
        _INDEX_ERROR = ''
        _THREAD = threading.Thread(target=_worker, daemon=True, name='embedding-index')
        _THREAD.start()
    return True


def stop_indexing():
    _STOP.set()
    with _STATE_LOCK:
        thread = _THREAD
    if thread:
        thread.join(timeout=10)
