"""对空且隔离的 8766 服务执行真实资料包验收。使用 --data-root 指定测试目录，--with-model 启用收费模型检查；不操作主文献库。"""
import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', default='http://127.0.0.1:8766')
parser.add_argument('--materials', type=Path, default=ROOT / 'data/contest-materials/科研小助手/pdf')
parser.add_argument('--data-root', type=Path, required=True)
parser.add_argument('--with-model', action='store_true')
parser.add_argument('--report',type=Path,default=ROOT/'docs/SYSTEM_TEST_REPORT.json')
args = parser.parse_args()
if args.base_url not in ('http://127.0.0.1:8766', 'http://localhost:8766'):
    parser.error('This script only targets the isolated local test port 8766.')
data_root = args.data_root.resolve()
if not data_root.is_relative_to(ROOT / 'data') or data_root == ROOT / 'data':
    parser.error('--data-root must be a dedicated child of project data/.')

report_path = args.report.resolve()
if not report_path.is_relative_to(ROOT/'docs'):
    parser.error('--report must be under project docs/.')
report = {'started_at': datetime.now(timezone.utc).isoformat(), 'state': 'running',
          'scope': 'User-provided material pack; real HTTP APIs and isolated storage. Text extraction is not proof of complete layout/formula accuracy.',
          'data_root': str(data_root), 'base_url': args.base_url,
          'with_paid_model_checks': args.with_model, 'imports': [], 'checks': [], 'limitations': [
              'Automated model checks verify selected sample facts and source coverage, not every sentence or full translation quality.',
              'No deployment, public access, or formal submission is performed.',
              'Formula LaTeX recovery, full author-affiliation extraction and precise paragraph alignment remain separate capability gaps.']}
ids = {}
client = httpx.Client(base_url=args.base_url, trust_env=False, timeout=220)


def save():
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def check(name, passed, **details):
    item = {'name': name, 'passed': bool(passed), **details}
    report['checks'].append(item)
    save()
    print(json.dumps({'check': name, 'passed': bool(passed), **{k: v for k, v in details.items() if k in ('status', 'error', 'count')}}, ensure_ascii=True), flush=True)
    return item


def request(method, path, **kw):
    return client.request(method, path, **kw)


def wait_paper(pid, seconds=1200):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        response = client.get('/api/papers/' + pid)
        response.raise_for_status()
        value = response.json()
        if value['status'] != 'processing':
            return value
        time.sleep(.3)
    raise TimeoutError('PDF parse deadline exceeded')


def sql(query, params=()):
    uri = (data_root / 'researchdock.db').as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(query, params)]


def import_file(path, keep=False):
    started = time.monotonic()
    raw = path.read_bytes()
    with fitz.open(stream=raw, filetype='pdf') as doc:
        expected_pages = len(doc)
    response = client.post('/api/papers', files={'file': (path.name, raw, 'application/pdf')}, data={'keep': str(keep).lower()})
    duplicate = None
    if response.status_code == 409:
        duplicate = [{'title': d['paper']['title'], 'reason': d['reason'], 'score': d['score']} for d in response.json().get('duplicates', [])]
        response = client.post('/api/papers', files={'file': (path.name, raw, 'application/pdf')}, data={'keep': 'true'})
    response.raise_for_status()
    pid = response.json()['paper_id']
    ids[path.name] = pid
    paper = wait_paper(pid)
    stats = sql('SELECT COUNT(*) pages,MAX(page) last_page,SUM(LENGTH(text)) characters FROM pages WHERE paper_id=?', (pid,))[0]
    meta = paper['metadata']
    sources = meta.get('page_sources', {})
    counts = {s: list(sources.values()).count(s) for s in sorted(set(sources.values()))}
    item = {'filename': path.name, 'paper_id': pid, 'expected_pages': expected_pages,
            'status': paper['status'], 'title': paper['title'], 'authors_present': bool(paper['authors'].strip()),
            **stats, 'seconds': round(time.monotonic() - started, 2), 'duplicate_warning': duplicate,
            'toc_entries': len(meta.get('toc', [])), 'tables': len(meta.get('tables', [])),
            'image_regions': len(meta.get('images', [])), 'page_sources': counts,
            'review_pages': meta.get('unreadable_pages', []), 'ocr_engines': meta.get('ocr_engines', []),
            'formula_mode': meta.get('formula_mode'),
            'passed_persistence': paper['status'] in ('ready', 'partial') and stats['pages'] == expected_pages and stats['last_page'] == expected_pages}
    report['imports'].append(item)
    save()
    print(json.dumps({'file': path.name, 'status': paper['status'], 'pages': stats['pages'], 'expected': expected_pages,
                      'seconds': item['seconds'], 'ocr_engines': item['ocr_engines']}, ensure_ascii=True), flush=True)
    return paper


def ask(name, question, selected, predicate, **extra):
    started = time.monotonic()
    try:
        response = client.post('/api/ask', json={'question': question, 'paper_ids': selected, **extra})
        if response.status_code != 200:
            return check(name, False, status=response.status_code, error=response.json().get('detail', 'request failed'))
        value = response.json()
        compact = {'answer': value['answer'], 'mode': value['mode'], 'execution': value.get('execution'),
                   'source_pages': [{'paper_id': s['paper_id'], 'page': s['page'], 'source_id': s['source_id']} for s in value['sources']],
                   'tool_calls': value.get('tool_calls'), 'trace': value.get('trace', [])}
        return check(name, predicate(value), question=question, seconds=round(time.monotonic() - started, 2), result=compact)
    except httpx.HTTPError as exc:
        return check(name, False, error=type(exc).__name__)


try:
    initial = client.get('/api/status').raise_for_status().json()
    if initial['papers'] != 0 or sql('SELECT COUNT(*) n FROM papers')[0]['n'] != 0:
        raise RuntimeError('Test library must start empty; no existing data will be deleted.')
    report['initial_status'] = {k: initial.get(k) for k in ('version', 'model', 'llm_configured', 'embedding_configured', 'papers')}
    save()
    check('HTTP home and status', client.get('/').status_code == 200)

    v7 = import_file(args.materials / '1706.03762v7.pdf')
    duplicate = client.post('/api/papers', files={'file': ('renamed-old-version.pdf', (args.materials / '1706.03762v1.pdf').read_bytes(), 'application/pdf')})
    detail = duplicate.json()
    check('Renamed v1 detected against v7 with newer-version advice', duplicate.status_code == 409 and any('库中版本较新' in d['reason'] for d in detail.get('duplicates', [])),
          status=duplicate.status_code, reasons=[d['reason'] for d in detail.get('duplicates', [])])
    import_file(args.materials / '1706.03762v1.pdf', keep=True)
    scan = import_file(args.materials / '1706.03762v1_img.pdf')
    check('Scanned PDF all 15 pages OCR persisted', scan['status'] == 'ready' and len(scan['metadata'].get('page_sources', {})) == 15 and all(v == 'ocr' for v in scan['metadata']['page_sources'].values()),
          engines=scan['metadata'].get('ocr_engines'), page_sources=scan['metadata'].get('page_sources'))
    check('Scanned PDF has recognized paper title', scan['title'] != '未识别标题', actual_title=scan['title'])

    excluded = {'1706.03762v7.pdf', '1706.03762v1.pdf', '1706.03762v1_img.pdf'}
    for path in sorted(args.materials.glob('*.pdf')):
        if path.name not in excluded:
            import_file(path)
    items = report['imports']
    check('All material PDFs fully persisted', len(items) == 39 and all(p['passed_persistence'] for p in items), count=len(items),
          total_pages=sum(p['pages'] for p in items), failed=[p['filename'] for p in items if not p['passed_persistence']])
    report['metadata_quality'] = {'missing_authors': [p['filename'] for p in items if not p['authors_present']],
                                  'unrecognized_titles': [p['filename'] for p in items if p['title'] == '未识别标题'],
                                  'partial_papers': [p['filename'] for p in items if p['status'] == 'partial'],
                                  'unstructured_formula_mode': sorted(set(p['formula_mode'] or '' for p in items))}

    for filename, page in [('1706.03762v7.pdf', 7), ('1706.03762v1_img.pdf', 7), ('UnderstandingDeepLearning_02_09_26_C.pdf', 541)]:
        pid = ids[filename]
        info = client.get(f'/api/papers/{pid}/pages/{page}')
        image = client.get(f'/api/papers/{pid}/pages/{page}/image')
        check('Read page and PNG: ' + filename, info.status_code == 200 and image.status_code == 200 and image.content.startswith(b'\x89PNG'), page=page)
    refs = client.get('/api/papers/' + v7['id'] + '/references').raise_for_status().json()
    check('Layer Normalization reference is extracted', any('layer normalization' in x['text'].lower() for x in refs),
          count=len(refs), matching=[x for x in refs if 'layer normalization' in x['text'].lower()])
    diff = client.post('/api/diff', json={'left': ids['1706.03762v1.pdf'], 'right': ids['1706.03762v7.pdf']}).raise_for_status().json()
    check('v1-v7 body differences', diff['change_count'] > 0 and diff['similarity'] < 1, result=diff)

    graph = client.get('/api/graph').raise_for_status().json()
    evidence_ok = True
    for edge in graph['edges']:
        page = sql('SELECT text FROM pages WHERE paper_id=? AND page=?', (edge['source'], edge['page']))
        if not page or not edge['evidence'].strip():
            evidence_ok = False
            break
    check('Material citation graph has anchored edges', len(graph['nodes']) == 39 and len(graph['edges']) > 0 and evidence_ok,
          nodes=len(graph['nodes']), edges=len(graph['edges']), nodes_with_incoming=sum(n['in_degree'] > 0 for n in graph['nodes']))
    report['graph'] = {'nodes': len(graph['nodes']), 'edges': len(graph['edges']),
                       'papers_without_references': [n['title'] for n in graph['nodes'] if not n['references']],
                       'note': 'Checks anchor existence only, not all reference-match precision/recall.'}

    raw = b'model,accuracy,recall,f1\na,0.70,0.60,0.65\nb,0.80,0.75,0.77\n'
    for kind in ('line', 'bar', 'scatter', 'radar', 'radar3d'):
        chart_csv = b'x,accuracy,recall,f1\n1,0.70,0.60,0.65\n2,0.80,0.75,0.77\n' if kind == 'scatter' else raw
        response = client.post('/api/charts', files={'file': ('benchmark.csv', chart_csv, 'text/csv')}, data={'kind': kind})
        ok = response.status_code == 200
        if ok:
            value = response.json()
            ok = client.get(value['image']).content.startswith(b'\x89PNG') and '<svg' in client.get(value['svg']).text and client.get(value['script']).status_code == 200
        check('CSV plot and exports: ' + kind, ok, status=response.status_code)
    response = client.post('/api/charts', files={'file': ('invalid.csv', b'x,y\na,NaN\nb,Infinity\n', 'text/csv')})
    check('Non-finite CSV rejected', response.status_code == 422, status=response.status_code)

    # 增删改操作仅作用于本测试单独创建的副本。
    copy_response = client.post('/api/papers', files={'file': ('crud-test-copy.pdf', (args.materials / '1706.03762v7.pdf').read_bytes(), 'application/pdf')}, data={'keep': 'true'}).raise_for_status().json()
    copy_id = copy_response['paper_id']
    wait_paper(copy_id)
    edited = client.patch('/api/papers/' + copy_id, json={'title': 'Disposable CRUD test copy', 'authors': 'Test only'})
    deleted = client.delete('/api/papers/' + copy_id)
    check('Metadata edit and deletion of test-created copy', edited.status_code == 200 and edited.json()['title'] == 'Disposable CRUD test copy' and deleted.status_code == 200 and client.get('/api/papers/' + copy_id).status_code == 404)

    try:
        found = client.get('/api/discover', params={'query': '1706.03762', 'count': 3}, timeout=50)
        check('Real arXiv discovery', found.status_code == 200 and bool(found.json()), status=found.status_code,
              results=[{'id': p['id'], 'retrieval': p.get('retrieval')} for p in found.json()] if found.status_code == 200 else [])
    except httpx.HTTPError as exc:
        check('Real arXiv discovery', False, error=type(exc).__name__)
    watch = client.post('/api/watches', json={'query': '1706.03762', 'hours': 24}).raise_for_status().json()
    watch_id = watch['id']
    listed = client.get('/api/watches').raise_for_status().json()
    removed = client.delete('/api/watches/' + watch_id)
    check('Tracking topic configuration', any(w['id'] == watch_id for w in listed) and removed.status_code == 200)

    if args.with_model and initial['llm_configured']:
        primary = ids['1706.03762v7.pdf']
        ask('Single-paper GPU and Adam facts', '这篇论文训练用了什么 GPU 型号和数量？Adam 的 beta1、beta2 和 epsilon 是多少？请依据原文回答。', [primary],
            lambda v: v['mode'] == 'generated' and 'P100' in v['answer'] and '0.98' in v['answer'] and bool(v['sources']) and all(s['paper_id'] == primary for s in v['sources']))
        ask('Sixth author and institution', 'Attention Is All You Need 的第六位作者是谁？原文列出的所属机构是什么？请按作者顺序核对。', [primary],
            lambda v: 'Gomez' in v['answer'] and ('Toronto' in v['answer'] or '多伦多' in v['answer']) and bool(v['sources']))
        ask('Scanned paper GPU and Adam facts', '训练使用什么 GPU 型号？Adam 的 beta2 参数是多少？', [ids['1706.03762v1_img.pdf']],
            lambda v: 'P100' in v['answer'] and '0.98' in v['answer'] and bool(v['sources']))
        ask('Unsupported hardware serial number is not invented', '原文中训练使用的 GPU 的硬件序列号分别是多少？只报告原文明确给出的硬件序列号。', [primary],
            lambda v: any(word in v['answer'] for word in ('无法', '未提供', '不足', '未找到', '没有提供')))
        if 'N19-1423.pdf' in ids:
            compare_ids = [primary, ids['N19-1423.pdf']]
            ask('Cross-paper BERT vs Transformer evidence coverage', '对比这两篇论文：BERT 的架构与原始 Transformer Encoder 有什么关系？BERT 保留什么、去掉什么？分别引用两篇原文。', compare_ids,
                lambda v: {s['paper_id'] for s in v['sources']} == set(compare_ids) and 'BERT' in v['answer'])
        ask('Native Agent tool execution and synthesis', '请查原文确认训练 GPU，并核读相关页面给出 Adam 参数。', [primary],
            lambda v: v.get('execution') == 'agent' and (v.get('tool_calls') or 0) > 0 and 'P100' in v['answer'] and bool(v['sources']), agent=True)
        try:
            response = client.post('/api/ask/stream', json={'question': 'Adam optimizer beta2 参数是多少？', 'paper_ids': [primary]})
            events = []
            for block in response.text.split('\n\n'):
                event = re.search(r'^event: (.+)$', block, re.M)
                if event:
                    events.append(event.group(1))
            check('Real model SSE token/claim/final sequence', response.status_code == 200 and all(e in events for e in ('token', 'claim', 'result')) and 'error' not in events,
                  event_counts={e: events.count(e) for e in set(events)})
        except httpx.HTTPError as exc:
            check('Real model SSE token/claim/final sequence', False, error=type(exc).__name__)
        for target, text in [('中文', 'We used the Adam optimizer with beta1 = 0.9 and beta2 = 0.98.'), ('英文', '本研究使用 Adam 优化器，beta1 为 0.9，beta2 为 0.98。')]:
            response = client.post('/api/translate', json={'text': text, 'target': target})
            value = response.json()
            content = value.get('content', '')
            check('Academic translation to ' + target, response.status_code == 200 and '0.9' in content and '0.98' in content,
                  status=response.status_code, output=content or value.get('detail'))
        response = client.post('/api/papers/' + primary + '/translate', json={'page': 7})
        content = response.json().get('content', '')
        exported = client.get('/api/papers/' + primary + '/translations.md')
        check('Real page translation and Markdown export', response.status_code == 200 and 'P100' in content and exported.status_code == 200,
              status=response.status_code, excerpt=content[:700])
        for kind in ('outline', 'mermaid'):
            response = client.post('/api/writing', json={'kind': kind, 'idea': '基于所选论文整理一个简短的 Transformer 阅读分析工具方案，明确区分已知论文方法和待验证建议。', 'paper_ids': [primary]})
            value = response.json()
            content = value.get('content', '')
            valid = response.status_code == 200 and bool(content)
            valid = valid and (all(w in content.lower() for w in ('abstract', 'introduction', 'references')) if kind == 'outline' else bool(re.search(r'flowchart|graph\s+(?:TD|LR)', content)))
            check('Real writing: ' + kind, valid, status=response.status_code, output=content or value.get('detail'))
    else:
        report['limitations'].append('Paid-model checks not run; model API quality is unverified in this run.')
    report['summary'] = {'checks_passed': sum(x['passed'] for x in report['checks']),
                         'checks_total': len(report['checks']),
                         'failed_checks': [x['name'] for x in report['checks'] if not x['passed']],
                         'imported_pdfs': len(items), 'imported_pages': sum(x['pages'] for x in items),
                         'partial_papers': report['metadata_quality']['partial_papers']}
    report['state'] = 'completed'
except Exception as exc:
    report['state'] = 'interrupted'
    report['fatal_error'] = type(exc).__name__ + ': ' + str(exc)[:250]
    raise
finally:
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    save()
    client.close()
    print(json.dumps({'state': report['state'], 'report': str(report_path), 'summary': report.get('summary')}, ensure_ascii=True), flush=True)
