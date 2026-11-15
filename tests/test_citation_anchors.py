"""验证同页、重复与范围引用可以分别定位并反向查找。"""
import json

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from backend import citations, db, research
from backend.app import app


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    with TestClient(app) as client:
        doc=fitz.open();page=doc.new_page()
        for y,text in [(60,'The first method [1] is useful.'),(100,'We compare [1, 2] and cite [1] again.'),
                       (300,'References'),(340,'[1] Alice Smith. First Study. 2020.'),(380,'[2] Bob Jones. Second Study. 2021.')]:
            page.insert_text((60,y),text)
        blocks=[{'text':b[4].strip(),'bbox':list(b[:4])} for b in page.get_text('blocks') if b[6]==0]
        doc.save(db.DATA/'files'/'paper.pdf');doc.close()
        db.execute("INSERT INTO papers(id,title,status,page_count) VALUES('paper','Sample','ready',1)")
        db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?)',('paper',1,'\n\n'.join(b['text'] for b in blocks),json.dumps(blocks),595,842))
        yield client


def test_reference_has_distinct_occurrences_and_source_region(client):
    entries=client.get('/api/papers/paper/references').json()
    first=next(r for r in entries if r['label']=='1')
    second=next(r for r in entries if r['label']=='2')
    assert len(first['occurrences'])==3 and len(second['occurrences'])==1
    assert len({x['id'] for x in first['occurrences']})==3
    assert all(x['bbox_precision']=='text-match' for x in first['occurrences'])
    assert first['source']['bbox'][1]>250
    assert all(x['bbox'][1]<200 for x in first['occurrences'])
    assert first['occurrences'][1]['labels']==['1','2']


def test_range_labels_and_invalid_ranges():
    assert citations.labels('1, 3–5')==['1','3','4','5']
    assert citations.labels('1 – 3')==['1','2','3']
    assert citations.labels('2-1')==[]
    assert citations.labels('1-999')==[]
    assert citations.labels('0')==[]


def test_reference_block_is_not_a_body_citation(client):
    entries=research.reference_candidates('paper')
    for entry in entries:
        assert all(item['block_index']!=entry['source']['block_index'] for item in entry['occurrences'])


def test_missing_native_file_keeps_text_offsets_without_fake_precise_box(client):
    (db.DATA/'files'/'paper.pdf').unlink()
    entries=research.reference_candidates('paper')
    assert entries[0]['occurrences'][0]['bbox_precision']=='block'
    assert entries[0]['occurrences'][0]['end']>entries[0]['occurrences'][0]['start']


def test_repeated_bibliography_numbers_preserve_all_candidates(client):
    entries=[{'label':'1','text':'Alice Smith. First Study. 2020.','page':1},
             {'label':'1','text':'Another Author. Another Study. 2022.','page':1}]
    result=citations.attach('paper',entries)
    assert all(item['ambiguous_label'] for item in result)
    assert all(len(item['occurrences'])==3 for item in result)
