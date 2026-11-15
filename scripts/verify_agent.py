"""使用已导入的公开 arXiv 样本进行可选真实模型检查，会消耗接口额度。"""
import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

import httpx

root = Path(__file__).resolve().parents[1]
with httpx.Client(base_url='http://127.0.0.1:8765', trust_env=False, timeout=200) as client:
    papers = client.get('/api/papers').raise_for_status().json()
    sample = next(p for p in papers if p.get('arxiv_id') == '1706.03762' and p.get('version') == 7)
    started = monotonic()
    response = client.post('/api/ask', json={'question': 'What GPU hardware and Adam optimizer beta and epsilon parameters were used in training? Answer in Chinese with sources.',
                                           'paper_ids': [sample['id']], 'agent': True})
    response.raise_for_status()
    value = response.json()
    assert value['execution'] == 'agent' and value['tool_calls'] > 0
    assert value['mode'] == 'generated' and value['sources']
    assert all(s['paper_id'] == sample['id'] and s['bbox'] for s in value['sources'])
    assert 'P100' in value['answer'] and '0.98' in value['answer']
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'model': 'mimo-v2.6-pro',
              'sample': 'arXiv:1706.03762v7', 'duration_seconds': round(monotonic() - started, 2),
              'execution': value['execution'], 'tool_calls': value['tool_calls'], 'bounded': value['bounded'],
              'trace': value['trace'], 'answer': value['answer'],
              'source_pages': [s['page'] for s in value['sources']], 'passed': True,
              'limit': 'One real tool-calling smoke check; citation IDs and sample facts checked, not a comprehensive agent or entailment evaluation.'}
    target = root / 'docs' / 'AGENT_LIVE_CHECK.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))
