import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import db,deduplication,documents,embeddings,metadata
from backend.app import app


def test_author_row_order_and_footnotes():
    blocks=[{'bbox':[300,150.01,430,190],'text':'Third Author∗\nResearch University\nthird@example.org'},
            {'bbox':[50,100,180,140],'text':'First Author∗\nExample University\nfirst@example.org'},
            {'bbox':[50,150,180,190],'text':'Second Author†\nExample University\nsecond@example.org'},
            {'bbox':[200,250,250,265],'text':'Abstract'}]
    values=metadata.authors_from_header(blocks,800,'Paper Title')
    assert [v['name'] for v in values]==['First Author','Second Author','Third Author']
    assert values[1]['email']=='second@example.org' and values[1]['bbox']==[50,150,180,190]


def test_grouped_names_and_emails_are_explicitly_derived():
    blocks=[{'bbox':[100,100,500,160],'text':'Jacob Devlin\nMing-Wei Chang\nGoogle AI Language\n{jacobdevlin,mingweichang}@google.com'}]
    values=metadata.authors_from_header(blocks,800,'BERT')
    assert len(values)==2 and values[1]['email']=='mingweichang@google.com'
    assert values[1]['email_derived_from_group'] and values[1]['affiliations']==['Google AI Language']


def test_title_is_not_mistaken_for_author():
    values=metadata.authors_from_header([{'bbox':[100,100,500,150],'text':'Important Research Paper\nResearch University'}],800,'Important Research Paper')
    assert not values


@pytest.fixture
def library(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    with TestClient(app) as client:
        db.execute("INSERT INTO papers(id,title,authors,status,metadata) VALUES('p','Sample Paper','Manual Author','ready','{}')")
        blocks=[{'bbox':[100,100,200,140],'text':'Real Author\nExample University\nreal@example.org'}]
        db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?)',('p',1,'header',json.dumps(blocks),600,800))
        yield client


def test_enrichment_preserves_manual_author(library):
    value=library.post('/api/papers/p/enrich-header').json()
    assert value['authors'][0]['name']=='Real Author'
    assert library.get('/api/papers/p').json()['authors']=='Manual Author'


def test_semantic_signature_cache_and_delete(library,monkeypatch):
    monkeypatch.setattr(embeddings,'configured',lambda:True)
    monkeypatch.setattr(embeddings,'space_id',lambda:'fixture-space')
    calls=[]
    monkeypatch.setattr(embeddings,'encode',lambda texts:calls.append(texts) or [[1.,0.] for _ in texts])
    text='A long original academic paragraph about a scientific method. '*10
    db.execute('UPDATE pages SET text=? WHERE paper_id=?',(text,'p'))
    info={'semantic_sample':text}
    paper={'id':'p'}
    assert deduplication.similarities(info,[paper])['p']==pytest.approx(1)
    first=len(calls)
    deduplication.similarities(info,[paper])
    assert len(calls)==first+1  # Incoming sample encoded, existing signature reused.
    db.execute("DELETE FROM papers WHERE id='p'")
    assert not db.rows('SELECT * FROM document_vectors')


def test_semantic_duplicate_suggestion_does_not_depend_on_name(library,monkeypatch):
    db.execute("UPDATE papers SET sha='oldbytes',title='Sample Paper',version=7,arxiv_id='1706.03762' WHERE id='p'")
    monkeypatch.setattr(deduplication,'similarities',lambda info,papers:{'p':.99})
    info={'title':'Sample Paper','sample':'A completely rewritten description with distinct vocabulary. '*4,'semantic_sample':'body','arxiv_id':'1706.03762','version':1}
    found=documents.find_duplicates(b'new bytes',info)
    assert found and found[0]['semantic_score']==.99 and found[0]['recommendation']=='keep_existing'
    assert db.one("SELECT id FROM papers WHERE id='p'")


def test_related_but_not_identical_semantics_are_not_duplicates(library,monkeypatch):
    monkeypatch.setattr(deduplication,'similarities',lambda info,papers:{'p':.9})
    info={'title':'Different Work','sample':'Different topic and distinct vocabulary. '*4,'arxiv_id':'','version':None}
    assert not documents.find_duplicates(b'new bytes',info)
