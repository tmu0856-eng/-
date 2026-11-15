"""可选真实论文检查，需在被忽略的 data/samples/ 中准备 v1/v7 样本。"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import documents


def main():
    with httpx.Client(base_url='http://127.0.0.1:8765', timeout=90, trust_env=False) as client:
        papers = client.get('/api/papers').json()
        v7 = next(p for p in papers if p['arxiv_id'] == '1706.03762' and p['version'] == 7)
        raw = (ROOT / 'data/samples/1706.03762v1.pdf').read_bytes()
        response = client.post('/api/papers', files={'file': ('random-name-for-content-check.pdf', raw, 'application/pdf')})
        assert response.status_code == 409, response.text
        duplicates = response.json()['duplicates']
        match = next(d for d in duplicates if d['paper']['id'] == v7['id'])
        assert '建议保留' in match['reason']
        result = client.post('/api/ask', json={'question': 'Adam optimizer beta training GPU', 'paper_ids': [v7['id']]}).json()
        assert any('P100' in s['text'] for s in result['sources'])
        assert any('Adam' in s['text'] and '0.98' in s['text'] for s in result['sources'])
        refs = client.get(f'/api/papers/{v7["id"]}/references').json()
        assert refs and all(r['page'] > 0 for r in refs)
        report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'source': 'https://arxiv.org/abs/1706.03762',
                  'title': v7['title'], 'v7_pages': v7['page_count'], 'renamed_v1_duplicate_status': response.status_code,
                  'v1_v7_content_score': match['score'], 'version_advice': match['reason'],
                  'retrieval_mode': result['mode'], 'parameter_evidence_pages': sorted({s['page'] for s in result['sources'] if 'Adam' in s['text'] or 'P100' in s['text']}),
                  'references_detected': len(refs),
                  'not_verified': ['official scanned PDF', 'official 541-page textbook', 'live model generation', 'public deployment']}
        target = ROOT / 'docs' / 'REAL_SAMPLE_CHECK.json'
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
