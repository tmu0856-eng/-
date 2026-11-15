"""在隔离的评审库中检查补充赛题问题，会消耗模型接口额度。"""
import json
import time
from datetime import datetime,timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
report={'checked_at':datetime.now(timezone.utc).isoformat(),'checks':[],
        'scope':'Selected official-style sample questions; source coverage and named values only, not a universal factual score.',
        'version_note':'Attention v7 table 2 says EN-FR 41.8, unlike v1 table 2 (41.0). The v7 prose still says 41.0, so a faithful response may flag the inconsistency. Initial record retained separately.'}


def check(name,passed,**detail):
    report['checks'].append({'name':name,'passed':bool(passed),**detail})
    (ROOT/'docs/EXTENDED_QA_CHECK.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'name':name,'passed':bool(passed)}),flush=True)


with httpx.Client(base_url='http://127.0.0.1:8767',trust_env=False,timeout=240) as c:
    config=dotenv_values(ROOT/'.env.demo')
    c.post('/api/auth/login',json={'username':config['DEMO_USERNAME'],'password':config['DEMO_PASSWORD']}).raise_for_status()
    papers=c.get('/api/papers').raise_for_status().json()
    gpt2=next((p for p in papers if p['filename']=='language_models_are_unsupervised_multitask_learners.pdf'),None)
    if not gpt2:
        path=ROOT/'data/samples/language_models_are_unsupervised_multitask_learners.pdf'
        r=c.post('/api/papers',files={'file':(path.name,path.read_bytes(),'application/pdf')}).raise_for_status().json()
        for _ in range(120):
            gpt2=c.get('/api/papers/'+r['paper_id']).raise_for_status().json()
            if gpt2['status'] in ('ready','partial','failed'):break
            time.sleep(1)
        if gpt2['status'] not in ('ready','partial'):
            raise RuntimeError('GPT-2 public PDF did not parse successfully.')
        papers=c.get('/api/papers').raise_for_status().json()
    ids={p['filename']:p['id'] for p in papers}
    primary=ids['1706.03762v7.pdf']
    def ask(name,question,scope,verify,**extra):
        start=time.monotonic()
        r=c.post('/api/ask',json={'question':question,'paper_ids':scope,**extra})
        value=r.json()
        check(name,r.status_code==200 and verify(value),status=r.status_code,
              seconds=round(time.monotonic()-start,2),response=value)
        return value
    first=ask('Sixth author for conversation context','第六位作者是谁？机构是什么？',[primary],
              lambda v:'Gomez' in v.get('answer','') and bool(v.get('sources')))
    history=[{'role':'user','content':'第六位作者是谁？机构是什么？','paper_ids':[primary]},
             {'role':'assistant','content':first.get('answer',''),'paper_ids':[primary]}]
    ask('Follow-up inherits cited document scope','其中 Adam 的 beta2 和 epsilon 是多少？',[],
        lambda v:'0.98' in v.get('answer','') and bool(v.get('sources'))
        and all(s['paper_id']==primary for s in v['sources']),history=history)
    ask('Translation results and previous system comparison',
        '按原文表 2，列出 Transformer big 的 EN-DE 和 EN-FR BLEU，列出之前最佳系统结果，并比较差值；区分单模型与 ensemble，不能把负差值说成提升。',[primary],
        lambda v:all(x in v.get('answer','') for x in ('28.4','41.8','26.36','41.29')) and bool(v.get('sources')))
    ask('Layer Normalization reference authors and year',
        'Layer Normalization 在这篇论文参考文献中列出的作者和年份分别是什么？',[primary],
        lambda v:'2016' in v.get('answer','') and 'Ba' in v.get('answer','') and bool(v.get('sources')))
    scope=[ids['language_understanding_paper.pdf'],gpt2['id'],ids['2005.14165v4.pdf']]
    ask('GPT 1 2 3 context and parameter comparison',
        '逐篇比较 GPT-1、GPT-2、GPT-3：各自最大模型的参数量和上下文长度是多少？只能报告三篇原文明确给出的值；某篇未明示参数总量就标未明示，不要凭常识补全；分别引用三篇。',scope,
        lambda v:{s['paper_id'] for s in v.get('sources',[])}==set(scope)
        and all(x in v.get('answer','') for x in ('512','1024','2048','175')))
    ask('GPT 2 pre-normalization architectural change',
        'GPT-2 相比 GPT-1 如何调整 Layer Normalization 在各子模块中的位置？原文还提到增加了哪处 Layer Normalization？', [gpt2['id']],
        lambda v:bool(v.get('sources')) and any(x in v.get('answer','') for x in ('输入','前','pre'))
        and any(x in v.get('answer','') for x in ('最后','final')))

report['summary']={'passed':sum(x['passed'] for x in report['checks']),'total':len(report['checks'])}
(ROOT/'docs/EXTENDED_QA_CHECK.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report['summary']))
