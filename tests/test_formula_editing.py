"""公式修正只更新视觉缓存，保留原文、原始转录与并发版本。"""
import json
import pytest
from fastapi.testclient import TestClient
from backend import db,llm,visual
from backend.app import app


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setenv('LLM_MODEL','fixture-formula')
    with TestClient(app) as client:
        db.execute("INSERT INTO papers(id,sha,page_count,status) VALUES('p','pdf-sha',2,'ready')")
        db.execute("INSERT INTO chunks VALUES('chunk','p',1,'original evidence','[1,2,3,4]',NULL)")
        value={'paper_id':'p','page':1,'formulas':[{'label':'(1)','latex':'x=y+1','uncertainty':''},
                                                {'label':'(2)','latex':r'\frac{a}{b}','uncertainty':''}],
               'tables':[],'figures':[],'verified':False,'note':visual.NOTE,'source':{'page':1,'bbox':[0,0,600,800]}}
        db.execute('INSERT INTO visual_pages VALUES(?,?,?,?)',('p',1,'visual-page-v1:fixture-formula:pdf-sha',db.encode(value)))
        yield client


def save(client,**body):
    return client.put('/api/papers/p/visual-cache/1/formulas/0',json={'latex':'x=y+2','label':'(1)','revision':0,**body})


def test_save_preserves_original_and_other_formula_without_model_or_index_change(client,monkeypatch):
    monkeypatch.setattr(llm,'complete',lambda *a,**k:pytest.fail('手动修改不得调用模型'))
    before=client.get('/api/papers/p/visual-cache/1').json()
    assert before['revision']==0 and before['formulas'][0]['original_latex']=='x=y+1'
    result=save(client,label='(1a)').json()
    item=result['formulas'][0]
    assert result['revision']==1 and item['latex']=='x=y+2' and item['label']=='(1a)'
    assert item['original_latex']=='x=y+1' and item['original_label']=='(1)' and item['edited']
    assert result['formulas'][1]==before['formulas'][1] and not result['verified']
    assert db.one("SELECT text FROM chunks WHERE id='chunk'")['text']=='original evidence'
    assert visual.parse('p',1)['formulas'][0]['latex']=='x=y+2'


def test_stale_revision_never_overwrites_saved_edit(client):
    assert save(client).status_code==200
    assert save(client,latex='stale overwrite').status_code==409
    assert client.get('/api/papers/p/visual-cache/1').json()['formulas'][0]['latex']=='x=y+2'


def test_restore_original_and_reopen_after_database_init(client):
    save(client)
    restored=save(client,latex='x=y+1',label='(1)',revision=1).json()
    assert restored['revision']==2 and not restored['formulas'][0]['edited']
    db.init()
    value=client.get('/api/papers/p/visual-cache/1').json()
    assert value['revision']==2 and value['formulas'][0]['latex']=='x=y+1'


@pytest.mark.parametrize('body',[{'latex':''},{'latex':'  '},{'latex':'x'*8001},{'label':'a'*121},{'revision':-1}])
def test_bad_edit_is_rejected_without_cache_mutation(client,body):
    assert save(client,**body).status_code==422
    assert client.get('/api/papers/p/visual-cache/1').json()['revision']==0


def test_missing_formula_page_and_other_model_cannot_be_edited(client,monkeypatch):
    body={'latex':'x','label':'','revision':0}
    for route in ('/api/papers/p/visual-cache/1/formulas/99','/api/papers/p/visual-cache/2/formulas/0',
                  '/api/papers/p/visual-cache/3/formulas/0'):
        assert client.put(route,json=body).status_code==422
    assert client.put('/api/papers/missing/visual-cache/1/formulas/0',json=body).status_code==404
    monkeypatch.setenv('LLM_MODEL','different-model')
    assert save(client).status_code==422
    monkeypatch.setenv('LLM_MODEL','fixture-formula')
    assert client.get('/api/papers/p/visual-cache/1').json()['revision']==0


def test_public_edit_requires_reviewer_login(client,monkeypatch):
    monkeypatch.setenv('DEMO_PASSWORD','private-test-password')
    assert save(client).status_code==401
    monkeypatch.delenv('DEMO_PASSWORD')
    assert client.get('/api/papers/p/visual-cache/1').json()['revision']==0
