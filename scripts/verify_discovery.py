"""对运行中的本地服务执行可选真实文献检索检查。"""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

root=Path(__file__).resolve().parents[1]
with httpx.Client(base_url='http://127.0.0.1:8765',timeout=70,trust_env=False) as client:
    first=client.get('/api/discover',params={'query':'attention','count':12})
    first.raise_for_status()
    items=first.json()
    assert items and all(p['url'].startswith('https://arxiv.org/abs/') for p in items)
    second=client.get('/api/discover',params={'query':'attention','count':12})
    second.raise_for_status()
    repeated=second.json()
    assert repeated[0]['retrieval']['cache_state']=='cached'
    assert items[0]['retrieval']['fetched_at']==repeated[0]['retrieval']['fetched_at']
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'query':'attention',
            'http_status':first.status_code,'paper_count':len(items),
            'first_retrieval':items[0]['retrieval'],'repeat_retrieval':repeated[0]['retrieval'],
            'example_ids':[p['id'] for p in items[:3]],'passed':True,
            'limit':'A successful live check does not guarantee future upstream availability; retry and offline-cache cases are covered by mocked tests.'}
    target=root/'docs'/'ARXIV_LIVE_CHECK.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))
