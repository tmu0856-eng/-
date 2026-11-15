"""在专用验收目录核对署名结构与 BGE 版本提示，不调用生成模型或修改主文献库。"""
import json
import os
import sys
import time
from datetime import datetime,timezone
from pathlib import Path

import pymupdf as fitz
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/'.env')
os.environ['RESEARCHDOCK_DATA']=str(ROOT/'data/acceptance-structure-20261115')
os.environ['EMBEDDING_AUTO_INDEX']='0'
sys.path.insert(0,str(ROOT))
from backend import db,documents,metadata

MATERIALS=ROOT/'data/contest-materials/科研小助手/pdf'
report={'date':datetime.now(timezone.utc).isoformat(),'scope':'Selected real native PDFs from user material pack; isolated library, no paid generation calls.','checks':[]}


def check(name,passed,**details):
    report['checks'].append({'name':name,'passed':bool(passed),**details})
    print(json.dumps({'check':name,'passed':bool(passed)}),flush=True)


for filename,count in [('1706.03762v7.pdf',8),('N19-1423.pdf',4),('1607.06450v1.pdf',3),('language_understanding_paper.pdf',4)]:
    raw=(MATERIALS/filename).read_bytes()
    info=documents.preview(raw)
    with fitz.open(stream=raw,filetype='pdf') as pdf:
        page=pdf[0]
        blocks=[{'bbox':list(b[:4]),'text':b[4]} for b in page.get_text('blocks') if b[6]==0]
        authors=metadata.authors_from_header(blocks,page.rect.height,info['title'])
    valid=len(authors)==count
    if filename=='1706.03762v7.pdf':valid=valid and authors[5]['name']=='Aidan N. Gomez' and 'University of Toronto' in authors[5]['affiliations']
    check('Author structure '+filename,valid,authors=authors)
db.init()
existing=db.one("SELECT id FROM papers WHERE filename='1706.03762v7.pdf'")
if existing:
    pid=existing['id']
else:
    raw=(MATERIALS/'1706.03762v7.pdf').read_bytes()
    task=documents.enqueue(raw,'1706.03762v7.pdf',documents.preview(raw));pid=task['paper_id']
    end=time.monotonic()+90
    while time.monotonic()<end:
        if db.one('SELECT status FROM papers WHERE id=?',(pid,))['status']!='processing':break
        time.sleep(.1)
raw=(MATERIALS/'1706.03762v1.pdf').read_bytes()
info=documents.preview(raw)
duplicates=documents.find_duplicates(raw,info)
match=next((d for d in duplicates if d['paper']['id']==pid),None)
check('Older version duplicate with semantic evidence',match is not None and match.get('semantic_score') is not None and match['recommendation']=='keep_existing',
      duplicates=[{k:v for k,v in d.items() if k!='paper'} for d in duplicates],warning=info.get('dedup_warning',''))
raw=(MATERIALS/'N19-1423.pdf').read_bytes()
check('Related BERT is not a duplicate',not documents.find_duplicates(raw,documents.preview(raw)))
report['passed']=all(c['passed'] for c in report['checks'])
(ROOT/'docs/STRUCTURE_DEDUP_CHECK.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':report['passed'],'checks':len(report['checks'])}),flush=True)
documents.POOL.shutdown(wait=True)
if not report['passed']:raise SystemExit(1)
