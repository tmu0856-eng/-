"""仅在隔离资料测试库中进行真实模型回归，会消耗额度。重新解析扫描件并保留文献编号，结果单独保存，不覆盖原始 32 项报告。"""
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/system-test-20261114-materials'
os.environ['RESEARCHDOCK_DATA'] = str(DATA)
sys.path.insert(0, str(ROOT))
from backend import db, documents

report_path = ROOT / 'docs/REPORT_FIXES_CHECK.json'
report = {'started_at': datetime.now(timezone.utc).isoformat(), 'checks': [],
          'scope': 'Focused fixes regression on isolated port 8766, not a repeat of the complete 32-check material run.'}


def save():
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def check(name, passed, **details):
    report['checks'].append({'name': name, 'passed': bool(passed), **details})
    save()
    print(json.dumps({'check': name, 'passed': bool(passed)}, ensure_ascii=True), flush=True)


def main():
    previous = json.loads((ROOT / 'docs/SYSTEM_TEST_REPORT.json').read_text(encoding='utf-8'))
    assert Path(previous['data_root']).resolve() == DATA.resolve()
    ids = {p['filename']: p['paper_id'] for p in previous['imports']}
    primary, scan = ids['1706.03762v7.pdf'], ids['1706.03762v1_img.pdf']
    assert not db.rows("SELECT id FROM jobs WHERE status IN ('queued','running')")
    jid = uuid.uuid4().hex
    with db.connect() as con:
        con.execute('DELETE FROM chunks WHERE paper_id=?', (scan,))
        con.execute('DELETE FROM pages WHERE paper_id=?', (scan,))
        con.execute("UPDATE papers SET status='processing' WHERE id=?", (scan,))
        con.execute('INSERT INTO jobs(id,paper_id,status,total,message,created) VALUES(?,?,?,?,?,?)',
                    (jid, scan, 'queued', 15, '扫描件回归解析', documents.now()))
    start = time.monotonic()
    documents.parse(scan, jid)
    p = db.one('SELECT * FROM papers WHERE id=?', (scan,))
    check('Scanned title and all 15 OCR pages', p['title'] == 'Attention Is All You Need' and p['status'] == 'ready'
          and db.one('SELECT COUNT(*) n FROM pages WHERE paper_id=?', (scan,))['n'] == 15,
          title=p['title'], status=p['status'], seconds=round(time.monotonic()-start, 2))
    with httpx.Client(base_url='http://127.0.0.1:8766', trust_env=False, timeout=220) as client:
        def ask(name, question, scope, verify, **options):
            start = time.monotonic()
            try:
                r = client.post('/api/ask', json={'question': question, 'paper_ids': scope, **options})
                value = r.json()
                check(name, r.status_code == 200 and verify(value), status=r.status_code,
                      seconds=round(time.monotonic()-start, 2), response=value)
            except httpx.HTTPError as exc:
                check(name, False, error=type(exc).__name__)
        ask('Native GPU and Adam remain correct', '训练用了什么 GPU 型号和数量？Adam 的 beta1、beta2 和 epsilon 是多少？请依据原文回答。', [primary],
            lambda v: 'P100' in v['answer'] and '0.98' in v['answer'] and bool(v['sources']))
        ask('Sixth author and institution', 'Attention Is All You Need 的第六位作者是谁？原文列出的所属机构是什么？请按作者顺序核对。', [primary],
            lambda v: 'Gomez' in v['answer'] and ('Toronto' in v['answer'] or '多伦多' in v['answer']) and bool(v['sources']))
        ask('Scanned GPU and Adam facts', '训练使用什么 GPU 型号？Adam 的 beta2 参数是多少？', [scan],
            lambda v: 'P100' in v['answer'] and '0.98' in v['answer'] and bool(v['sources']))
        ask('Unsupported hardware serial is not invented', '原文中训练使用的 GPU 的硬件序列号分别是多少？只报告原文明确给出的硬件序列号。', [primary],
            lambda v: any(w in v['answer'] for w in ('无法', '未提供', '不足', '未找到', '没有提供')))
        compare = [primary, ids['N19-1423.pdf']]
        ask('Cross-paper BERT and Transformer sources', '对比这两篇论文：BERT 的架构与原始 Transformer Encoder 有什么关系？BERT 保留什么、去掉什么？分别引用两篇原文。', compare,
            lambda v: {s['paper_id'] for s in v['sources']} == set(compare) and 'BERT' in v['answer'])
        ask('Agent tool execution and synthesis', '请查原文确认训练 GPU，并核读相关页面给出 Adam 参数。', [primary],
            lambda v: v.get('execution') == 'agent' and v.get('tool_calls', 0) > 0 and 'P100' in v['answer'] and bool(v['sources']), agent=True)
        response = client.post('/api/ask/stream', json={'question': 'Adam optimizer beta2 参数是多少？', 'paper_ids': [primary]})
        events = []
        for block in response.text.split('\n\n'):
            event = re.search(r'^event: (.+)$', block, re.M)
            data = re.search(r'^data: (.+)$', block, re.M)
            if event and data:
                events.append({'event': event[1], 'data': json.loads(data[1])})
        kinds = [e['event'] for e in events]
        final = next((e['data'] for e in events if e['event'] == 'result'), {})
        check('Real SSE token, claim and result', all(e in kinds for e in ('token', 'claim', 'result'))
              and 'error' not in kinds and '0.98' in final.get('answer', ''),
              event_counts={e: kinds.count(e) for e in set(kinds)}, events=events)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    report['summary'] = {'passed': sum(c['passed'] for c in report['checks']), 'total': len(report['checks'])}
    save()
    print(json.dumps(report['summary']), flush=True)


if __name__ == '__main__':
    main()
