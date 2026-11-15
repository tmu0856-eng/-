"""使用已配置接口检查向量服务、多文推理、参考文献与翻译，结果写入 docs/LLM_LIVE_CHECK.json。执行前请先重新解析文献。"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import app as backend_app
from backend import db, documents, llm, research, retrieval

REPORT = Path(__file__).resolve().parent.parent / 'docs' / 'LLM_LIVE_CHECK.json'
CORE_IDS = ['1706.03762', '1810.04805', '1907.11692', '2005.14165', '1910.10683', '1412.6980',
            '1512.03385', '1607.06450', '1802.05365', '1801.06146', '1901.02860', '1906.08237',
            '1909.11942', '2003.10555', '2003.08239', '2103.00020', '1707.06347', '1409.0575',
            '1606.05250', '1804.07461']


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** .5
    nb = sum(y * y for y in b) ** .5
    return dot / (na * nb) if na and nb else 0.0


def embedding_probe():
    base = os.getenv('LLM_BASE_URL', '')
    key = os.getenv('LLM_API_KEY', '')
    out = {'model_env': os.getenv('EMBEDDING_MODEL', ''), 'candidates': [], 'attempts': []}
    if not base or not key:
        out['verdict'] = 'skipped'
        out['reason'] = '未配置 LLM_BASE_URL/LLM_API_KEY'
        return out
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            response = client.get(base.rstrip('/') + '/models', headers={'Authorization': 'Bearer ' + key})
            out['models_http_status'] = response.status_code
            ids = [m.get('id', '') for m in response.json().get('data', [])] if response.status_code == 200 else []
    except Exception as exc:
        out['verdict'] = 'unavailable'
        out['reason'] = f'模型列表获取失败：{type(exc).__name__}'
        return out
    out['listed_models'] = ids[:50]
    candidates = [m for m in ids if re.search(r'embed|bge|e5|m3e|gte|text-embedding', m, re.I)]
    out['candidates'] = candidates
    tried = list(candidates) or ([out['model_env']] if out['model_env'] else [])
    if not tried:
        out['verdict'] = 'unavailable'
        out['reason'] = '服务商模型列表中没有 embedding 模型；语义检索保持 BM25+TF-IDF，属服务能力限制'
        return out
    pairs = (['Adam optimizer learning rate schedule', 'learning rate schedule of the Adam optimizer'],
             ['Adam optimizer learning rate schedule', 'zebras migrate across the savanna'])
    for name in tried[:3]:
        os.environ['EMBEDDING_MODEL'] = name
        attempt = {'model': name}
        try:
            vectors = llm.embed(list(pairs[0]) + [pairs[1][1]])
            if not vectors:
                raise llm.ModelError('embed 返回空')
            attempt['dims'] = len(vectors[0])
            attempt['cosine_similar'] = round(cosine(vectors[0], vectors[1]), 4)
            attempt['cosine_dissimilar'] = round(cosine(vectors[0], vectors[2]), 4)
            attempt['ok'] = attempt['cosine_similar'] > attempt['cosine_dissimilar'] > -1
            out['attempts'].append(attempt)
            if attempt['ok']:
                out['verdict'] = 'passed'
                out['reason'] = f"语义向量可用（{name}，{attempt['dims']} 维，相似对 {attempt['cosine_similar']} > 不似对 {attempt['cosine_dissimilar']}）"
                return out
        except Exception as exc:
            attempt['error'] = type(exc).__name__ + ': ' + str(exc)[:150]
            out['attempts'].append(attempt)
    out['verdict'] = 'unavailable'
    out['reason'] = '尝试的 embedding 模型均不可用；语义检索保持 BM25+TF-IDF'
    return out


def multi_doc_reasoning():
    rows = db.rows("SELECT id,arxiv_id,title FROM papers WHERE status IN ('ready','partial')")
    by_id = {r['arxiv_id'].split('v')[0]: r for r in rows if r['arxiv_id']}
    picked = [by_id[a] for a in ('1706.03762', '1810.04805') if a in by_id]
    out = {'papers': [{'paper_id': p['id'], 'arxiv_id': p['arxiv_id'], 'title': p['title']} for p in picked]}
    if len(picked) < 2:
        out['verdict'] = 'skipped'
        out['reason'] = '需要至少两篇已解析论文'
        return out
    if not llm.configured():
        out['verdict'] = 'skipped'
        out['reason'] = '模型未配置'
        return out
    query = '对比这两篇论文：各自提出的核心方法是什么？分别用什么任务或数据集验证？'
    ids = [p['id'] for p in picked]
    evidence, info = retrieval.retrieve(query, ids, limit=8)
    out['evidence_chunks'] = len(evidence)
    out['evidence_papers'] = sorted({e['paper_id'] for e in evidence})
    trace = [{'tool': 'retrieve_evidence', 'message': f'{info["mode"]}，返回 {len(evidence)} 条证据'}]
    try:
        result = retrieval.generate_answer(query, evidence, trace)
    except llm.ModelError as exc:
        out['verdict'] = 'failed'
        out['reason'] = f'生成/校验失败：{exc}'
        return out
    cited = sorted({s['paper_id'] for s in result.get('sources', [])})
    out['mode'] = result.get('mode')
    out['claims'] = len(result.get('sources', []))
    out['cited_papers'] = cited
    out['answer_excerpt'] = (result.get('answer') or '')[:600]
    out['verdict'] = 'passed' if set(ids) <= set(cited) else 'partial'
    out['reason'] = ('回答覆盖两篇论文的可核对引用' if out['verdict'] == 'passed'
                     else '回答未同时引用两篇论文，需人工核对检索范围与问题表述')
    return out


def references_check():
    graph = research.citation_graph()
    wanted = {p['arxiv_id'].split('v')[0]: p for p in graph['nodes'] if p.get('arxiv_id')}
    per_paper, zero = [], []
    for key in CORE_IDS:
        node = wanted.get(key)
        if not node:
            continue
        count = len(node['references'])
        outgoing = [e for e in graph['edges'] if e['source'] == node['id']]
        entry = {'arxiv_id': key, 'title': node['title'][:70], 'references_parsed': count,
                 'matched_targets': len(outgoing), 'in_degree': node['in_degree'], 'out_degree': node['out_degree']}
        if not count:
            zero.append(key)
        if outgoing:
            entry['sample_evidence'] = outgoing[0]['evidence'][:160]
        per_paper.append(entry)
    return {'per_paper': per_paper,
            'zero_reference_papers': zero,
            'edges_total_in_library': len(graph['edges']),
            'verdict': 'passed' if not zero and graph['edges'] else 'partial',
            'reason': ('全部核心论文抽取到参考文献且存在可核对连边' if not zero and graph['edges']
                       else f'{len(zero)} 篇未抽取到参考文献或无连边；未匹配不等于没有引用，需人工核对'),
            'method': '与引用图谱一致：References 段条目抽取 + 规范化标题匹配，边保留原文证据'}


def translation_quality():
    out = {'units': []}
    if not llm.configured():
        out['verdict'] = 'skipped'
        out['reason'] = '模型未配置'
        return out
    rows = db.rows("SELECT id,arxiv_id,title FROM papers WHERE status IN ('ready','partial')")
    by_id = {r['arxiv_id'].split('v')[0]: r for r in rows if r['arxiv_id']}
    targets = [by_id[a] for a in ('1706.03762', '1810.04805', '1412.6980') if a in by_id]
    digit = re.compile(r'\d+(?:\.\d+)?')

    def judge(unit, source, translated):
        numbers = set(digit.findall(source))
        missing = sorted(n for n in numbers if n not in translated)[:8]
        unit['numbers_in_source'] = len(numbers)
        unit['numbers_missing_in_output'] = missing
        unit['length_ratio'] = round(len(translated) / max(1, len(source)), 2)
        unit['ok'] = not missing and .35 <= unit['length_ratio'] <= 3.0
        return unit['ok']

    for p in targets:
        db.execute('DELETE FROM translations WHERE paper_id=?', (p['id'],))
        try:
            source = db.one('SELECT text FROM pages WHERE paper_id=? AND page=1', (p['id'],))['text'][:4000]
            translated = backend_app.translate_page(p['id'], 1)
            unit = {'kind': 'page', 'arxiv_id': p['arxiv_id'], 'page': 1, 'output_excerpt': translated[:300]}
            judge(unit, source, translated)
        except Exception as exc:
            unit = {'kind': 'page', 'arxiv_id': p.get('arxiv_id'), 'error': type(exc).__name__ + ': ' + str(exc)[:150], 'ok': False}
        out['units'].append(unit)
    text = ('We fine-tune the Transformer model on the WMT 2014 English-German dataset '
            'using Adam with β1 = 0.9, β2 = 0.98 and ε = 10−9, and report BLEU scores on newstest2013 [1].')
    try:
        translated = backend_app.translate_text(backend_app.TextTranslation(text=text, target='中文'))['content']
        unit = {'kind': 'text', 'target': '中文', 'output_excerpt': translated[:300]}
        judge(unit, text, translated)
    except Exception as exc:
        unit = {'kind': 'text', 'error': type(exc).__name__ + ': ' + str(exc)[:150], 'ok': False}
    out['units'].append(unit)
    passed = sum(1 for u in out['units'] if u.get('ok'))
    out['passed_units'] = passed
    out['total_units'] = len(out['units'])
    out['verdict'] = 'passed' if passed == len(out['units']) and out['units'] else 'partial'
    out['reason'] = ('数字与公式保留、篇幅比例正常' if out['verdict'] == 'passed'
                     else '存在数字丢失或篇幅比例异常的翻译单元，需人工核对')
    return out


def main():
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'model': os.getenv('LLM_MODEL', ''),
        'scope': 'Embedding 语义检索可用性、多文推理、References 核验、学术翻译质量（真实调用）。',
        'embedding': embedding_probe(),
        'multi_doc_reasoning': multi_doc_reasoning(),
        'references': references_check(),
        'translation': translation_quality(),
    }
    report['all_passed'] = all(report[k]['verdict'] in ('passed',) for k in ('embedding', 'multi_doc_reasoning', 'references', 'translation'))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for k in ('embedding', 'multi_doc_reasoning', 'references', 'translation'):
        print(k, '->', report[k]['verdict'], '|', report[k].get('reason', '')[:100])
    print('report:', REPORT)


if __name__ == '__main__':
    main()
