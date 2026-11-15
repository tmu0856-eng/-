import time

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from backend import db,research
from backend.app import app


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    with TestClient(app) as client:
        db.execute("INSERT INTO papers(id,title,status,page_count) VALUES('source','Source','ready',1)")
        monkeypatch.setattr(research,'reference_entries',lambda pid:[{'label':'1','text':'Jimmy Lei Ba. Layer Normalization. arXiv:1607.06450, 2016.','page':1},
                     {'label':'2','text':'A closed publication without arXiv ID.','page':1}])
        yield client


def wait_job(client,jid):
    for _ in range(200):
        job=client.get('/api/reference-imports/'+jid).json()
        if job['status'] not in ('queued','running'):return job
        time.sleep(.03)
    raise AssertionError('Batch did not finish')


def test_reference_candidates_keep_verbatim_text_and_local_identity(client):
    db.execute("INSERT INTO papers(id,title,arxiv_id,status) VALUES('ln','Layer Normalization','1607.06450','ready')")
    values=client.get('/api/papers/source/references').json()
    assert values[0]['arxiv_id']=='1607.06450' and values[0]['local_paper_id']=='ln'
    assert not values[0]['can_import'] and not values[1]['can_import']


def test_unknown_and_unresolved_labels_rejected_before_download(client,monkeypatch):
    monkeypatch.setattr(research,'download_arxiv',lambda ident:pytest.fail('Invalid reference downloaded'))
    for labels in (['missing'],['2']):
        assert client.post('/api/papers/source/references/import',json={'labels':labels}).status_code==422


def test_existing_paper_skips_network(client,monkeypatch):
    db.execute("INSERT INTO papers(id,title,arxiv_id,status) VALUES('ln','Layer Normalization','1607.06450','ready')")
    monkeypatch.setattr(research,'download_arxiv',lambda ident:pytest.fail('Existing reference downloaded'))
    response=client.post('/api/papers/source/references/import',json={'labels':['1']})
    job=wait_job(client,response.json()['job_id'])
    assert job['results'][0]['status']=='already_in_library'


def test_batch_download_enqueues_parse_and_persists_result(client,monkeypatch):
    pdf=fitz.open();page=pdf.new_page();page.insert_text((40,50),'A synthetic public paper with enough text for extraction.');raw=pdf.tobytes();pdf.close()
    monkeypatch.setattr(research,'download_arxiv',lambda ident:raw)
    response=client.post('/api/papers/source/references/import',json={'labels':['1','1']})
    job=wait_job(client,response.json()['job_id'])
    assert job['total']==1 and job['results'][0]['status']=='queued_for_parse'
    assert client.get('/api/papers/'+job['results'][0]['paper_id']).status_code==200


def test_download_failure_is_reported_without_inventing_paper(client,monkeypatch):
    def fail(ident):raise ValueError('fixture unavailable')
    monkeypatch.setattr(research,'download_arxiv',fail)
    response=client.post('/api/papers/source/references/import',json={'labels':['1']})
    job=wait_job(client,response.json()['job_id'])
    assert job['results'][0]['status']=='failed'
    assert len(client.get('/api/papers').json())==1


def test_batch_size_limit(client):
    assert client.post('/api/papers/source/references/import',json={'labels':['1']*6}).status_code==422
