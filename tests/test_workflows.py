"""Integration checks use synthetic PDFs, never pretend these are official test results."""
import io
import json
import time

import fitz
import pytest
from fastapi.testclient import TestClient

from backend import db, documents, llm, research, retrieval
from backend.app import app


def synthetic_pdf(title='A Synthetic Research Paper', text=None, count=2):
    doc = fitz.open()
    doc.set_metadata({'title': title, 'author': 'Synthetic Test Author'})
    for i in range(count):
        page = doc.new_page()
        content = (f'{title}\n1 Introduction\nSynthetic testing material only.\n'
                   'The model was trained using Adam optimizer with learning rate 0.001.\n'
                   'Training used eight synthetic GPU devices.\n2 Future Work\nFuture work studies longer context windows.\n') if i == 0 else (
                   'References\n[1] A Synthetic Foundational Paper. Synthetic Test Author.\n[2] Another Test Reference.\n')
        page.insert_textbox(fitz.Rect(50, 45, 550, 780), text or content, fontsize=11)
    raw = doc.tobytes()
    doc.close()
    return raw


def wait_paper(client, pid):
    for _ in range(200):
        p = client.get('/api/papers/' + pid).json()
        if p['status'] != 'processing':
            assert p['status'] in ('ready', 'partial'), p
            return p
        time.sleep(.03)
    raise AssertionError('PDF parse did not finish')


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    for key in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL', 'EMBEDDING_MODEL'):
        monkeypatch.delenv(key, raising=False)
    with TestClient(app) as c:
        yield c
    # No background parse may escape this isolated storage fixture.
    for job in db.rows("SELECT status FROM jobs WHERE status IN ('queued','running')"):
        raise AssertionError(f'Outstanding job: {job}')


def upload(client, raw=None, name='paper.pdf', keep=False):
    return client.post('/api/papers', files={'file': (name, raw or synthetic_pdf(), 'application/pdf')}, data={'keep': str(keep).lower()})


def test_ui_and_status_work_without_model(client):
    assert client.get('/').status_code == 200
    assert 'ResearchDock' in client.get('/').text
    status = client.get('/api/status').json()
    assert status['llm_configured'] is False
    assert status['papers'] == 0
    assert client.get('/app.js').status_code == 200


def test_upload_extract_read_and_delete(client):
    response = upload(client)
    assert response.status_code == 200, response.text
    pid = response.json()['paper_id']
    p = wait_paper(client, pid)
    assert p['page_count'] == 2
    assert p['authors'] == 'Synthetic Test Author'
    page = client.get(f'/api/papers/{pid}/pages/1').json()
    assert 'Adam optimizer' in page['text']
    assert page['blocks'][0]['bbox']
    assert client.get(f'/api/papers/{pid}/pages/1/image').headers['content-type'] == 'image/png'
    assert client.get(f'/api/papers/{pid}/pdf').content.startswith(b'%PDF-')
    assert client.get(f'/api/papers/{pid}/pages/999/image').status_code == 404
    refs = client.get(f'/api/papers/{pid}/references').json()
    assert refs[0]['label'] == '1'
    assert refs[0]['page'] == 2
    assert 'Foundational' in refs[0]['text']
    updated = client.patch('/api/papers/'+pid, json={'title':'Verified Synthetic Title','authors':'Verified Test Author'})
    assert updated.json()['title'] == 'Verified Synthetic Title'
    assert client.delete('/api/papers/' + pid).status_code == 200
    assert client.get('/api/papers').json() == []
    assert not list((db.DATA / 'files').glob('*.pdf'))


def test_repeated_upload_detected_even_when_renamed(client):
    raw = synthetic_pdf()
    pid = upload(client, raw).json()['paper_id']
    wait_paper(client, pid)
    duplicate = upload(client, raw, 'renamed.pdf')
    assert duplicate.status_code == 409
    assert duplicate.json()['duplicates'][0]['score'] == 1
    kept = upload(client, raw, 'keep.pdf', True)
    wait_paper(client, kept.json()['paper_id'])
    assert len(client.get('/api/papers').json()) == 2


def test_content_duplicate_not_just_file_hash(client):
    first = synthetic_pdf()
    pid = upload(client, first).json()['paper_id']
    wait_paper(client, pid)
    other = fitz.open(stream=first, filetype='pdf')
    other.set_metadata({'title': 'A Synthetic Research Paper', 'author': 'Changed Metadata'})
    modified = other.tobytes()
    other.close()
    response = upload(client, modified, 'completely-different-name.pdf')
    assert response.status_code == 409
    assert '正文' in response.json()['duplicates'][0]['reason']


def test_invalid_pdf_rejected(client):
    assert upload(client, b'not-a-pdf').status_code == 422
    assert upload(client, b'%PDF-broken').status_code == 422


def test_title_ignores_license_and_rotated_arxiv_stamp(client):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((80,60), 'Provided proper attribution, permission is granted.', fontsize=12)
    page.insert_text((150,140), 'Attention Is All You Need', fontsize=17)
    page.insert_text((20,450), 'arXiv:1706.03762v7', fontsize=20, rotate=90)
    page.insert_text((80,200), 'Synthetic author. Synthetic body paragraph for title extraction.', fontsize=10)
    info = documents.preview(doc.tobytes())
    doc.close()
    assert info['title'] == 'Attention Is All You Need'


def test_reference_list_does_not_swallow_following_figures(client):
    doc = fitz.open(stream=synthetic_pdf(), filetype='pdf')
    page = doc.new_page()
    page.insert_text((50,60), 'Attention Visualizations\nFigure 3: This is an appendix, not a bibliography entry.', fontsize=12)
    pid = upload(client, doc.tobytes()).json()['paper_id']
    doc.close()
    wait_paper(client, pid)
    refs = client.get(f'/api/papers/{pid}/references').json()
    assert len(refs) == 2
    assert all('appendix' not in r['text'] for r in refs)


def test_evidence_question_and_unknown_question(client):
    pid = upload(client).json()['paper_id']
    wait_paper(client, pid)
    result = client.post('/api/ask', json={'question': 'optimizer learning rate', 'paper_ids': [pid]}).json()
    assert result['mode'] == 'evidence_only'
    assert any('Adam' in s['text'] for s in result['sources'])
    assert all(s['page'] >= 1 and len(s['bbox']) == 4 for s in result['sources'])
    unknown = client.post('/api/ask', json={'question': 'quasiparticle superconductivity'}).json()
    assert unknown['mode'] == 'insufficient'
    assert unknown['sources'] == []


def test_empty_library_does_not_invent_answer(client):
    response = client.post('/api/ask', json={'question': 'What is the training GPU?'})
    assert response.json()['mode'] == 'insufficient'
    assert response.json()['sources'] == []


def test_invalid_model_source_is_rejected(client, monkeypatch):
    pid = upload(client).json()['paper_id']
    wait_paper(client, pid)
    monkeypatch.setattr(llm, 'configured', lambda: True)
    monkeypatch.setattr(llm, 'complete', lambda *args: {'claims': [{'text': 'unsupported', 'source_ids': ['FAKE']}]})
    response = client.post('/api/ask', json={'question': 'optimizer'})
    assert response.status_code == 503
    assert '不存在' in response.json()['detail']


def test_claim_without_source_is_rejected(client, monkeypatch):
    pid = upload(client).json()['paper_id']
    wait_paper(client, pid)
    monkeypatch.setattr(llm, 'configured', lambda: True)
    monkeypatch.setattr(llm, 'complete', lambda *args: {'claims': [{'text': 'The GPU was a fictional machine.', 'source_ids': []}]})
    assert client.post('/api/ask', json={'question': 'optimizer'}).status_code == 503


def test_stream_has_real_evidence_result(client):
    pid = upload(client).json()['paper_id']
    wait_paper(client, pid)
    response = client.post('/api/ask/stream', json={'question': 'Adam optimizer'})
    assert response.status_code == 200
    assert 'event: step' in response.text
    assert 'event: result' in response.text
    assert 'evidence_only' in response.text


def test_multi_paper_scope_returns_both(client):
    a = upload(client, synthetic_pdf('A Synthetic Research Paper')).json()['paper_id']
    wait_paper(client, a)
    b = upload(client, synthetic_pdf('A Different Synthetic Model', text='A Different Synthetic Model\nA separate optimizer is SGD. Learning rate is 0.02. Training data is synthetic.'), keep=True).json()['paper_id']
    wait_paper(client, b)
    result = client.post('/api/ask', json={'question': 'optimizer learning rate', 'paper_ids': [a,b]}).json()
    assert {s['paper_id'] for s in result['sources']} == {a,b}


def test_graph_edges_have_reference_evidence(client):
    base = upload(client, synthetic_pdf('A Synthetic Foundational Paper', text='A Synthetic Foundational Paper\nThis foundational method is only synthetic fixture data.'), keep=True).json()['paper_id']
    wait_paper(client, base)
    source = upload(client, synthetic_pdf(), keep=True).json()['paper_id']
    wait_paper(client, source)
    graph = client.get('/api/graph').json()
    edge = next(e for e in graph['edges'] if e['source'] == source and e['target'] == base)
    assert edge['page'] == 2 and 'Foundational' in edge['evidence']
    assert len(graph['nodes']) == 2


def test_version_diff_compares_extracted_text(client):
    a = upload(client).json()['paper_id']
    wait_paper(client, a)
    b = upload(client, synthetic_pdf(text='Modified synthetic version. Adam optimizer learning rate 0.002. New validation data.'), keep=True).json()['paper_id']
    wait_paper(client, b)
    result = client.post('/api/diff', json={'left': a, 'right': b}).json()
    assert result['change_count'] > 0
    assert result['similarity'] < 1


def test_translation_requires_model_and_does_not_fake_output(client):
    pid = upload(client).json()['paper_id']
    wait_paper(client, pid)
    assert client.post(f'/api/papers/{pid}/translate', json={'page': 1}).status_code == 503
    assert client.post('/api/translate', json={'text': 'A synthetic abstract.'}).status_code == 503
    assert client.post('/api/writing', json={'kind':'outline','idea':'A test model'}).status_code == 503


def test_csv_chart_has_downloadable_outputs(client):
    raw = b'model,accuracy,recall,f1\na,0.7,0.6,0.65\nb,0.8,0.7,0.75\n'
    response = client.post('/api/charts', files={'file':('test.csv',raw,'text/csv')}, data={'kind':'bar'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['row_count'] == 2
    assert client.get(result['image']).content.startswith(b'\x89PNG')
    assert '<svg' in client.get(result['svg']).text
    script = client.get(result['script']).text
    compile(script, 'exported.py', 'exec')
    assert client.get(result['csv']).content == raw
    assert '0.7' in result['caption']


@pytest.mark.parametrize('kind', ['line','scatter','radar','radar3d'])
def test_other_chart_types_produce_real_images(client, kind):
    raw = b'index,accuracy,recall,f1\n1,0.7,0.6,0.65\n2,0.8,0.7,0.75\n'
    response = client.post('/api/charts',files={'file':('test.csv',raw,'text/csv')},data={'kind':kind})
    assert response.status_code == 200, response.text
    assert client.get(response.json()['image']).content.startswith(b'\x89PNG')


@pytest.mark.parametrize('raw', [b'a,a\n1,2', b'a,b\n1,nan', b'a,b\n1,2,3', b'a\n1', b'a,b\n'])
def test_bad_csv_is_reported(client, raw):
    response = client.post('/api/charts',files={'file':('bad.csv',raw,'text/csv')})
    assert response.status_code == 422


def test_no_arbitrary_remote_download_or_path_access(client):
    response = client.post('/api/discover/import', json={'arxiv_id':'http://localhost/admin'})
    assert response.status_code == 422
    assert client.get('/api/charts/not-an-id.py').status_code == 404
    assert client.get('/api/papers/unknown/pdf').status_code == 404


def test_cross_site_mutations_rejected(client):
    response = client.post('/api/ask', headers={'Origin':'https://unrelated.example'}, json={'question':'optimizer'})
    assert response.status_code == 403


def test_watch_schedule_persists_and_validates_interval(client):
    response = client.post('/api/watches', json={'query':'attention','hours':24})
    assert response.status_code == 200
    wid = response.json()['id']
    assert client.get('/api/watches').json()[0]['id'] == wid
    assert client.post('/api/watches', json={'query':'attention','hours':0}).status_code == 422
    assert client.delete('/api/watches/'+wid).status_code == 200


def test_541_page_synthetic_document_is_not_truncated(client):
    pid = upload(client, synthetic_pdf('Synthetic Long Document', text='Synthetic page content for long-document parsing and persistence validation.', count=541)).json()['paper_id']
    for _ in range(1200):
        p = client.get('/api/papers/'+pid).json()
        if p['status'] != 'processing':
            break
        time.sleep(.05)
    assert p['status'] == 'ready', p
    assert client.get(f'/api/papers/{pid}/pages/541').status_code == 200
    assert db.one('SELECT COUNT(*) n FROM pages WHERE paper_id=?', (pid,))['n'] == 541
