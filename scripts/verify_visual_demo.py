"""按需执行收费视觉转录与本地评审登录检查，不发布公网服务。"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
report={'checked_at':datetime.now(timezone.utc).isoformat(),'checks':[],
        'scope':'Single-page transcription spot checks, not formula/table accuracy evaluation.',
        'note':'An initial check incorrectly expected scaled-dot attention on page 4 of v1. Original-page inspection confirms it is on page 3; page 4 contains MultiHead. No model output was changed to satisfy the check.'}


def check(name,passed,**details):
    report['checks'].append({'name':name,'passed':bool(passed),**details})
    print(json.dumps({'name':name,'passed':bool(passed)}),flush=True)


config=dotenv_values(ROOT/'.env.demo')
with httpx.Client(base_url='http://127.0.0.1:8767',trust_env=False,timeout=30) as c:
    denied=c.get('/api/papers');health=c.get('/api/health')
    login=c.post('/api/auth/login',json={'username':config['DEMO_USERNAME'],'password':config['DEMO_PASSWORD']})
    check('Reviewer login protects data',denied.status_code==401 and health.status_code==200
          and login.status_code==200 and c.get('/api/papers').status_code==200
          and 'HttpOnly' in login.headers.get('set-cookie',''))
    c.post('/api/auth/logout')
    check('Reviewer logout removes access',c.get('/api/papers').status_code==401)

with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=180) as c:
    papers=c.get('/api/papers').raise_for_status().json()
    pid=next(p['id'] for p in papers if p.get('arxiv_id')=='1706.03762' and p.get('version')==1)
    for page in (3,4,8):
        started=time.monotonic()
        r=c.post(f'/api/papers/{pid}/visual-parse',json={'page':page})
        value=r.json()
        latex=' '.join(f['latex'] for f in value.get('formulas',[]))
        if page==3:
            passed='softmax' in latex.lower() and 'sqrt' in latex and all(x in latex for x in ('Q','K','V'))
        elif page==4:
            passed='MultiHead' in latex and 'Concat' in latex and 'W' in latex
        else:
            cells=' '.join(cell for t in value.get('tables',[]) for row in t['rows'] for cell in row)
            passed=all(number in cells for number in ('27.3','28.4','41.0'))
        check('Original v1 visual page '+str(page),r.status_code==200 and passed,
              status=r.status_code,seconds=round(time.monotonic()-started,2),result=value)
    check('Visual cache reused',c.post(f'/api/papers/{pid}/visual-parse',json={'page':3}).json().get('cached') is True)

report['summary']={'passed':sum(x['passed'] for x in report['checks']),'total':len(report['checks'])}
(ROOT/'docs/VISUAL_DEMO_CHECK.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report['summary']))
