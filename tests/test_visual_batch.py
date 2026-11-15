"""验证公式批量识别的缓存、失败恢复、费用边界和取消行为。"""
import json
import threading
import time

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from backend import db,llm
from backend.app import app


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setenv('LLM_MODEL','fixture-vision')
    with TestClient(app) as client:
        doc=fitz.open()
        for i in range(3):doc.new_page().insert_text((60,100),f'y = x + {i}')
        doc.save(db.DATA/'files'/'p.pdf');doc.close()
        db.execute('INSERT INTO papers(id,sha,status,page_count,metadata) VALUES(?,?,?,?,?)',
                   ('p','fixture-sha','ready',3,json.dumps({'formula_pages':[1,2,3]})))
        yield client


def wait(client,jid):
    for _ in range(180):
        job=client.get('/api/visual-jobs/'+jid).json()
        if job['status'] not in ('running','queued'):return job
        time.sleep(.02)
    pytest.fail('批量任务未完成')


def result():return {'formulas':[{'latex':'y=x+1','label':'(1)'}],'tables':[],'figures':[]}


def test_batch_collects_all_selected_pages_and_skips_cache(client,monkeypatch):
    calls=[]
    monkeypatch.setattr(llm,'complete',lambda *a,**k:calls.append(1) or result())
    response=client.post('/api/papers/p/visual-batch',json={'pages':[1,2]}).json()
    assert wait(client,response['job_id'])['status']=='done'
    status=client.get('/api/papers/p/visual-status').json()
    assert status['cached_pages']==[1,2] and status['pending_pages']==[3]
    second=client.post('/api/papers/p/visual-batch',json={}).json()
    assert second['pages']==[3] and wait(client,second['job_id'])['status']=='done'
    assert len(calls)==3
    assert client.get('/api/papers/p/visual-cache/2').json()['cached']
    assert not db.rows('SELECT * FROM chunks')
    assert client.post('/api/papers/p/visual-batch',json={}).status_code==422


def test_invalid_batch_never_calls_model(client,monkeypatch):
    monkeypatch.setattr(llm,'complete',lambda *a,**k:pytest.fail('不合法请求不能调用模型'))
    for body in ({'pages':[0]},{'pages':[4]},{'pages':list(range(1,22))}):
        assert client.post('/api/papers/p/visual-batch',json=body).status_code==422
    assert not db.rows('SELECT * FROM visual_jobs')
    assert client.get('/api/papers/p/visual-cache/1').status_code==404


def test_partial_failure_is_reported_and_failed_page_can_retry(client,monkeypatch):
    calls=[]
    def complete(*a,**k):
        calls.append(1)
        if len(calls)==2:raise llm.ModelError('当前页暂时不可用')
        return result()
    monkeypatch.setattr(llm,'complete',complete)
    jid=client.post('/api/papers/p/visual-batch',json={'pages':[1,2,3]}).json()['job_id']
    job=wait(client,jid)
    assert job['status']=='partial' and job['done']==3
    assert client.get('/api/papers/p/visual-status').json()['pending_pages']==[2]
    retry=client.post('/api/papers/p/visual-batch',json={}).json()
    assert retry['pages']==[2] and wait(client,retry['job_id'])['status']=='done'


def test_cancel_stops_later_pages_and_prevents_deletion_in_flight(client,monkeypatch):
    started=threading.Event();release=threading.Event();calls=[]
    def complete(*a,**k):
        calls.append(1);started.set();release.wait(3);return result()
    monkeypatch.setattr(llm,'complete',complete)
    jid=client.post('/api/papers/p/visual-batch',json={'pages':[1,2,3]}).json()['job_id']
    assert started.wait(2)
    try:
        assert client.delete('/api/papers/p').status_code==409
        assert client.post('/api/papers/p/visual-batch',json={'pages':[2]}).status_code==422
        assert client.post(f'/api/visual-jobs/{jid}/cancel',json={}).status_code==200
    finally:release.set()
    job=wait(client,jid)
    assert job['status']=='cancelled' and job['done']==1 and len(calls)==1
    assert client.get('/api/papers/p/visual-status').json()['cached_pages']==[1]
