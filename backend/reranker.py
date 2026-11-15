"""本地交叉编码器精排，仅返回已有且带原文锚点的证据。"""
import json
import os
import threading
import time
from pathlib import Path

import numpy as np

from . import db, llm
from .local_models import INFERENCE_LOCK

MODEL_ID = 'BAAI/bge-reranker-v2-m3'
_LOCK = threading.Lock()
_MODEL = _TOKENIZER = _MODEL_KEY = None
_RUNTIME = {'state': 'idle', 'device': '', 'error': '', 'last_candidates': 0, 'last_ms': None}


def configured():
    return bool(os.getenv('RERANKER_MODEL', '').strip())


def _integer(name, default, low, high):
    try:
        value = int(os.getenv(name, str(default)))
        if not low <= value <= high:
            raise ValueError()
        return value
    except ValueError as exc:
        raise llm.ModelError(f'{name} 必须为 {low}–{high} 的整数。') from exc


def candidate_count():
    return _integer('RERANKER_CANDIDATES', 40, 1, 80)


def model_path():
    path = Path(os.getenv('RERANKER_LOCAL_PATH', '.local/models/bge-reranker-v2-m3'))
    return (db.ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def identity():
    if os.getenv('RERANKER_MODEL', '').strip() != MODEL_ID:
        raise llm.ModelError('本地精排适配器仅支持 BAAI/bge-reranker-v2-m3。')
    try:
        manifest = json.loads((model_path() / 'researchdock-model.json').read_text(encoding='utf-8'))
        if (manifest['model'] != MODEL_ID or not isinstance(manifest['revision'], str)
                or len(manifest['revision']) != 40 or not (model_path() / 'model.safetensors').is_file()):
            raise ValueError()
        return manifest['revision']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise llm.ModelError('Reranker 本地文件未就绪，请先下载 bge-reranker-v2-m3。') from exc


def score(query, passages):
    """返回原始相关性分数，不代表事实正确概率；每个进程只加载一次模型。"""
    global _MODEL, _TOKENIZER, _MODEL_KEY
    if not configured():
        raise llm.ModelError('尚未配置 Reranker。')
    if not passages:
        return []
    if len(passages) > 80:
        raise llm.ModelError('单次精排候选最多 80 条。')
    batch = _integer('RERANKER_BATCH_SIZE', 4, 1, 16)
    max_length = _integer('RERANKER_MAX_LENGTH', 512, 32, 8192)
    key = (str(model_path()), identity(), os.getenv('RERANKER_DEVICE', 'auto'))
    started = time.monotonic()
    with _LOCK, INFERENCE_LOCK:
        try:
            import torch
            if _MODEL_KEY != key:
                _RUNTIME.update(state='loading', error='')
                from transformers import AutoModelForSequenceClassification, AutoTokenizer
                device = key[2]
                if device == 'auto':
                    device = 'cuda' if torch.cuda.is_available() else 'cpu'
                if device.startswith('cuda'):
                    torch.cuda.empty_cache()
                tokenizer = AutoTokenizer.from_pretrained(str(model_path()), local_files_only=True, trust_remote_code=False)
                model = AutoModelForSequenceClassification.from_pretrained(
                    str(model_path()), local_files_only=True, trust_remote_code=False,
                    use_safetensors=True, torch_dtype=torch.float16 if device.startswith('cuda') else torch.float32)
                if model.config.num_labels != 1:
                    raise ValueError('Unexpected relevance head')
                model.to(device).eval()
                _MODEL, _TOKENIZER, _MODEL_KEY = model, tokenizer, key
                _RUNTIME.update(device=device)
            values = []
            with torch.inference_mode():
                for offset in range(0, len(passages), batch):
                    pairs = [(query, text) for text in passages[offset:offset+batch]]
                    inputs = _TOKENIZER(pairs, padding=True, truncation=True, max_length=max_length, return_tensors='pt')
                    inputs = {name: value.to(_RUNTIME['device']) for name, value in inputs.items()}
                    logits = _MODEL(**inputs, return_dict=True).logits.reshape(-1).float().cpu().numpy()
                    values.extend(logits.tolist())
            scores = np.asarray(values, dtype=np.float64)
            if scores.shape != (len(passages),) or not np.isfinite(scores).all():
                raise ValueError('Invalid relevance scores')
            _RUNTIME.update(state='ready', error='', last_candidates=len(passages),
                            last_ms=round((time.monotonic()-started)*1000, 1))
            return scores.tolist()
        except Exception as exc:
            _RUNTIME.update(state='error', error='Reranker 加载或评分失败，请检查模型、依赖与显存。')
            raise llm.ModelError(_RUNTIME['error']) from exc


def rerank(query, candidates):
    values = score(query, [candidate['text'] for candidate in candidates])
    if len(values) != len(candidates) or not np.isfinite(values).all():
        raise llm.ModelError('Reranker 评分数量或数值异常。')
    order = sorted(range(len(candidates)), key=lambda i: -values[i])
    return [{**candidates[i], 'rerank_score': float(values[i])} for i in order]


def status():
    error, revision = _RUNTIME['error'], ''
    try:
        count = candidate_count()
        max_length = _integer('RERANKER_MAX_LENGTH', 512, 32, 8192)
        if configured():
            revision = identity()
    except llm.ModelError as exc:
        error, count, max_length = str(exc), 0, 0
    return {'configured': configured(), 'model': os.getenv('RERANKER_MODEL', ''), 'revision': revision,
            'candidates': count, 'max_length': max_length, 'runtime': dict(_RUNTIME), 'error': error}
