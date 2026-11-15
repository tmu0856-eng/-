"""在现有文献库执行两次收费模型检查，不修改论文。"""
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT=Path(__file__).resolve().parents[1]
report={'date':datetime.now(timezone.utc).isoformat(),'scope':'Real main-library question answering and Agent SSE; two paid MiMo calls, no paper edits.'}
with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=220) as client:
    papers=client.get('/api/papers').raise_for_status().json()
    primary=next(p for p in papers if p['filename']=='1706.03762v7.pdf')
    question='原文中训练使用的 GPU 型号和数量是什么？Adam 的 beta2 参数是多少？请引用原文。'
    started=time.monotonic()
    response=client.post('/api/ask',json={'question':question,'paper_ids':[primary['id']]})
    value=response.json()
    report['direct']={'status':response.status_code,'total_ms':round((time.monotonic()-started)*1000,1),'response':value}
    report['direct']['passed']=(response.status_code==200 and 'P100' in value.get('answer','') and '0.98' in value.get('answer','')
        and bool(value.get('sources')) and all(s['paper_id']==primary['id'] for s in value['sources'])
        and any(t.get('tool')=='rerank_evidence' for t in value.get('trace',[])))
    print(json.dumps({'direct_passed':report['direct']['passed']}),flush=True)
    response=client.post('/api/ask/stream',json={'question':'请先检索原文再核读相关页，确认训练 GPU 型号与数量，以及 Adam 的 beta2 参数。','paper_ids':[primary['id']],'agent':True})
    events=[]
    for block in response.text.split('\n\n'):
        kind=re.search(r'^event: (.+)$',block,re.M)
        data=re.search(r'^data: (.+)$',block,re.M)
        if kind and data:events.append({'event':kind[1],'data':json.loads(data[1])})
    result=next((e['data'] for e in events if e['event']=='result'),{})
    names=[e['event'] for e in events]
    report['agent_sse']={'status':response.status_code,'events':events,'event_counts':{e:names.count(e) for e in set(names)}}
    report['agent_sse']['passed']=('error' not in names and all(e in names for e in ('token','claim','result'))
        and result.get('execution')=='agent' and result.get('tool_calls',0)>0
        and 'P100' in result.get('answer','') and '0.98' in result.get('answer','')
        and bool(result.get('sources')) and all(s['paper_id']==primary['id'] for s in result['sources'])
        and '精排保留' in json.dumps(events,ensure_ascii=False))
    report['status']=client.get('/api/status').raise_for_status().json()
report['passed']=report['direct']['passed'] and report['agent_sse']['passed']
(ROOT/'docs/RERANKER_LIVE_CHECK.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':report['passed'],'sse_passed':report['agent_sse']['passed'],'reranker':report['status']['reranker']}),flush=True)
if not report['passed']:raise SystemExit(1)
