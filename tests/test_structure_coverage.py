"""验证正文结构覆盖、旧文献更新与保守的署名关系。"""
import json
import time

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from backend import db, metadata, structure
from backend.app import app


def paper_bytes():
    doc=fitz.open()
    first=doc.new_page()
    first.insert_text((60,70),'Structured Research Paper',fontsize=20)
    first.insert_text((60,115),'Alice Smith, Bob Jones',fontsize=12)
    first.insert_text((60,145),'Example University',fontsize=11)
    first.insert_text((60,185),'Abstract',fontsize=12,fontname='hebo')
    first.insert_text((60,210),'This paper studies a small reproducible model and its mathematical structure.',fontsize=11)
    first.insert_text((60,260),'1 Introduction',fontsize=13,fontname='hebo')
    second=doc.new_page()
    second.insert_text((60,70),'1.1 Method',fontsize=13,fontname='hebo')
    second.insert_text((60,110),'1.1.1 Details',fontsize=12,fontname='hebo')
    second.insert_text((100,190),'y = x + 1 (1)',fontsize=14)
    second.insert_text((60,260),'8 We used eight GPUs.',fontsize=11)
    last=doc.new_page()
    last.insert_text((60,70),'Appendix A Additional Results',fontsize=14,fontname='hebo')
    last.insert_text((60,110),'A.1 Extra Experiment',fontsize=12,fontname='hebo')
    last.insert_text((60,155),'Additional observations are recorded here for reproducible evaluation.',fontsize=11)
    raw=doc.tobytes();doc.close();return raw


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    with TestClient(app) as client:
        yield client


def wait(client,pid):
    for _ in range(150):
        value=client.get('/api/papers/'+pid).json()
        if value['status'] not in ('processing',):return value
        time.sleep(.03)
    pytest.fail('解析任务未完成')


def test_upload_builds_formula_outline_appendix_and_author_inventory(client):
    response=client.post('/api/papers',files={'file':('structure.pdf',paper_bytes(),'application/pdf')})
    assert response.status_code==200,response.text
    value=wait(client,response.json()['paper_id']);meta=value['metadata']
    assert value['status']=='ready'
    assert value['abstract'].startswith('This paper studies')
    assert [a['name'] for a in meta['authors_structured']]==['Alice Smith','Bob Jones']
    assert all(a['affiliations']==['Example University'] for a in meta['authors_structured'])
    assert any(h['level']==3 and h['title']=='1.1.1 Details' for h in meta['toc'])
    assert not any(h['title'].startswith('8 We') for h in meta['toc'])
    assert any(a['page']==3 for a in meta['appendices'])
    assert meta['formula_pages']==[2]
    candidate=meta['formulas'][0]
    assert candidate['page']==2 and candidate['bbox'] and candidate['status']=='candidate'
    assert candidate['latex']=='' and 'x + 1' in candidate['text']
    assert meta['structure_coverage']['pages']==3


def test_old_paper_structure_refresh_preserves_chunks_and_manual_author(client):
    pid=client.post('/api/papers',files={'file':('structure.pdf',paper_bytes(),'application/pdf')}).json()['paper_id']
    wait(client,pid)
    db.execute('UPDATE papers SET metadata=?,authors=? WHERE id=?',('{}','Manual Name',pid))
    chunk_ids=[r['id'] for r in db.rows('SELECT id FROM chunks WHERE paper_id=? ORDER BY id',(pid,))]
    job=client.post(f'/api/papers/{pid}/structure-refresh').json()['job_id']
    for _ in range(150):
        task=next(x for x in client.get('/api/jobs').json() if x['id']==job)
        if task['status'] in ('done','failed'):break
        time.sleep(.03)
    assert task['status']=='done'
    current=client.get('/api/papers/'+pid).json()
    assert current['authors']=='Manual Name' and current['metadata']['structure_version']==structure.VERSION
    assert chunk_ids==[r['id'] for r in db.rows('SELECT id FROM chunks WHERE paper_id=? ORDER BY id',(pid,))]


def test_shared_author_row_does_not_invent_multiple_affiliation_links():
    blocks=[{'bbox':[50,100,500,120],'text':'Alice Smith, Bob Jones'},
            {'bbox':[50,140,500,155],'text':'First University'},
            {'bbox':[50,170,500,185],'text':'Second Institute'}]
    result=metadata.authors_from_header(blocks,800,'Sample Title')
    assert len(result)==2 and all(not a['affiliations'] for a in result)
    assert all(len(a['affiliation_candidates'])==2 for a in result)


def test_ocr_header_can_keep_names_without_fabricated_emails():
    blocks=[{'bbox':[60,100,500,120],'text':'Alice Smith and Bob Jones'},
            {'bbox':[60,140,500,160],'text':'Example University'}]
    result=metadata.authors_from_header(blocks,800,'Research Title')
    assert len(result)==2 and all(not a['email'] for a in result)


def test_margin_appendix_cross_reference_is_not_an_appendix_heading():
    doc=fitz.open();page=doc.new_page()
    page.insert_text((60,70),'Appendix A',fontsize=17,fontname='hebo')
    page.insert_text((60,120),'This appendix explains notation and mathematical definitions.',fontsize=11)
    page.insert_text((20,170),'Appendix B.2',fontsize=8)
    blocks=[{'text':b[4].strip(),'bbox':list(b[:4])} for b in page.get_text('blocks') if b[6]==0]
    result=structure.inspect_page(page,blocks,1,'native')
    assert [h['title'] for h in result['headings'] if h['kind']=='appendix']==['Appendix A']
    doc.close()


def test_numbered_author_row_keeps_names_and_drops_institution_labels():
    blocks=[{'bbox':[50,100,550,115],'text':'Ruibin Xiong* 1 2 Yunchang Yang* 3 Di He 4 5'},
            {'bbox':[50,140,550,160],'text':'Meta AI'},
            {'bbox':[50,180,550,200],'text':'Major Contributors'}]
    names=[a['name'] for a in metadata.authors_from_header(blocks,800,'Sample Title')]
    assert names==['Ruibin Xiong','Yunchang Yang','Di He']


def test_native_bookmarks_can_be_supplemented_with_deeper_headings():
    doc=fitz.open();page=doc.new_page()
    page.insert_text((60,70),'B.1.2',fontsize=13,fontname='hebo')
    page.insert_text((120,70),'Calculus',fontsize=13,fontname='hebo')
    page.insert_text((60,110),'This paragraph provides sufficient ordinary body text.',fontsize=11)
    blocks=[{'text':b[4].strip(),'bbox':list(b[:4])} for b in page.get_text('blocks') if b[6]==0]
    found=structure.inspect_page(page,blocks,1,'native')
    meta=structure.assemble([{'title':'Mathematics','level':1,'page':1}],found['headings'],[],1,{},[])
    assert any(h['title']=='B.1.2 Calculus' and h['level']==3 for h in meta['toc'])
    doc.close()


def test_abstract_skips_rotated_watermark_and_stops_before_contribution_note():
    blocks=[{'bbox':[100,100,160,115],'text':'Abstract'},
            {'bbox':[10,120,25,500],'text':'arXiv:1706.03762v7'},
            {'bbox':[80,130,500,230],'text':'The dominant sequence transduction models use recurrent networks. We propose a Transformer.'},
            {'bbox':[80,240,500,280],'text':'∗Equal contribution. This is a footnote.'},
            {'bbox':[80,300,500,320],'text':'1 Introduction'}]
    value=metadata.abstract_from_blocks([blocks])
    assert value.startswith('The dominant') and 'arXiv' not in value and 'footnote' not in value


def test_tall_abstract_column_is_not_mistaken_for_rotated_watermark():
    text='A long abstract remains normal horizontal text in a narrow two-column academic paper.'
    blocks=[{'bbox':[60,70,260,420],'text':'Abstract\n'+text},
            {'bbox':[60,440,260,455],'text':'1 Introduction'}]
    assert metadata.abstract_from_blocks([blocks])==text
