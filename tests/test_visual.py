import json

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from backend import db,llm,visual
from backend.app import app


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    with TestClient(app) as client:
        doc=fitz.open();doc.new_page().insert_text((50,80),'A visible formula: x = y + 1')
        doc.save(db.DATA/'files'/'sample.pdf');doc.close()
        db.execute("INSERT INTO papers(id,sha,page_count,status) VALUES('sample','sha-fixture',1,'ready')")
        yield client


def test_visual_uses_original_image_and_caches_without_indexing(client,monkeypatch):
    calls=[]
    def complete(system,user,**kwargs):
        calls.append(user)
        assert user[1]['image_url']['url'].startswith('data:image/png;base64,')
        return {'formulas':[{'label':'(1)','latex':r'x = y + 1'}],
                'tables':[{'caption':'A table','rows':[['x','1']]}], 'figures':[]}
    monkeypatch.setattr(llm,'complete',complete)
    first=client.post('/api/papers/sample/visual-parse',json={'page':1}).json()
    assert not first['verified'] and not first['cached'] and first['source']['page']==1
    assert first['formulas'][0]['latex']=='x = y + 1'
    assert client.post('/api/papers/sample/visual-parse',json={'page':1}).json()['cached']
    assert len(calls)==1 and not db.rows('SELECT * FROM chunks')
    db.execute("DELETE FROM papers WHERE id='sample'")
    assert not db.rows('SELECT * FROM visual_pages')


def test_visual_page_bounds_no_model_call(client,monkeypatch):
    monkeypatch.setattr(llm,'complete',lambda *a,**k:pytest.fail('No call expected'))
    assert client.post('/api/papers/sample/visual-parse',json={'page':2}).status_code==422
    assert client.post('/api/papers/missing/visual-parse',json={'page':1}).status_code==404


@pytest.mark.parametrize('value',[{'formulas':'invented'}, {'tables':[{'rows':[[123]]}]},
                                  {'formulas':[{'latex':None}]}, {'figures':[0]}])
def test_malformed_visual_response_is_not_saved(client,monkeypatch,value):
    monkeypatch.setattr(llm,'complete',lambda *a,**k:value)
    assert client.post('/api/papers/sample/visual-parse',json={'page':1}).status_code==503
    assert not db.rows('SELECT * FROM visual_pages')


def test_model_failure_not_cached(client,monkeypatch):
    def fail(*a,**k):raise llm.ModelError('fixture unavailable')
    monkeypatch.setattr(llm,'complete',fail)
    assert client.post('/api/papers/sample/visual-parse',json={'page':1}).status_code==503
    assert not db.rows('SELECT * FROM visual_pages')
