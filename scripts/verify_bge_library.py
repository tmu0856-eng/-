"""等待现有文献库完成索引，执行一次真实 MiMo 混合检索问答检查。使用 8765 服务，不改动论文，会消耗接口额度。"""
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / 'docs/BGE_M3_LIBRARY_CHECK.json'
report = {'started_at':datetime.now(timezone.utc).isoformat(), 'scope':'Existing main library, local BGE-M3, one paid MiMo answer. Not a comprehensive retrieval benchmark.'}
with httpx.Client(base_url='http://127.0.0.1:8765', trust_env=False, timeout=220) as client:
    deadline = time.monotonic()+1800
    while time.monotonic()<deadline:
        status = client.get('/api/embeddings/status').raise_for_status().json()
        report['embedding'] = status
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'indexed':status['indexed_chunks'],'total':status['total_chunks'],'indexing':status['indexing'],'runtime':status['runtime']['state']}),flush=True)
        if status['error']:
            raise RuntimeError(status['error'])
        if not status['pending_chunks'] and not status['indexing']:
            break
        time.sleep(15)
    else:
        raise TimeoutError('Index did not finish within 30 minutes')
    assert status['model']=='BAAI/bge-m3' and status['indexed_chunks']>0
    papers = client.get('/api/papers').raise_for_status().json()
    primary = next(p for p in papers if p['filename']=='1706.03762v7.pdf')
    question = '这篇论文训练使用的 GPU 型号与数量是什么？Adam 的 beta1、beta2 和 epsilon 分别是多少？请引用原文。'
    response = client.post('/api/ask',json={'question':question,'paper_ids':[primary['id']]})
    value = response.json()
    report['rag'] = {'status':response.status_code,'question':question,'response':value}
    uri = (ROOT / 'data/researchdock.db').as_uri()+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as con:
        report['vector_dimensions'] = con.execute('SELECT dimensions,COUNT(*) FROM chunk_vectors WHERE space=? GROUP BY dimensions',(status['space'],)).fetchall()
        vectors = con.execute('SELECT vector FROM chunk_vectors WHERE space=? LIMIT 5',(status['space'],)).fetchall()
        report['sample_norms'] = [float(np.linalg.norm(np.frombuffer(v[0],dtype='<f4'))) for v in vectors]
    report['papers'] = len(papers)
    report['passed'] = (response.status_code==200 and value.get('mode')=='generated'
        and 'P100' in value.get('answer','') and '0.98' in value.get('answer','')
        and bool(value.get('sources')) and all(s['paper_id']==primary['id'] for s in value['sources'])
        and any('BAAI/bge-m3' in t.get('message','') for t in value.get('trace',[]))
        and all(d==1024 for d,_ in report['vector_dimensions']))
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'papers':len(papers),'indexed':status['indexed_chunks']}),flush=True)
    if not report['passed']:
        raise SystemExit(1)
