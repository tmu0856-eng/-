"""仅使用合成 PDF 检查 OCR 回退与页面分类。"""
import time

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from backend import db, documents
from backend.app import app


def raster_pdf(text='The Adam optimizer trains on eight NVIDIA P100 GPUs.'):
    source = fitz.open()
    page = source.new_page()
    page.insert_textbox(fitz.Rect(50, 45, 550, 780), text, fontsize=14)
    pix = page.get_pixmap(dpi=150)
    source.close()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, pixmap=pix)
    raw = doc.tobytes()
    doc.close()
    return raw


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    for key in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL', 'EMBEDDING_MODEL'):
        monkeypatch.delenv(key, raising=False)
    with TestClient(app) as c:
        yield c


def test_two_column_orders_left_column_first():
    blocks = [
        {'bbox': [300, 200, 500, 220], 'text': 'right-1'},
        {'bbox': [50, 100, 250, 120], 'text': 'left-1'},
        {'bbox': [300, 100, 500, 120], 'text': 'right-0'},
        {'bbox': [50, 200, 250, 220], 'text': 'left-2'},
        {'bbox': [300, 300, 500, 320], 'text': 'right-2'},
        {'bbox': [50, 300, 250, 320], 'text': 'left-3'},
    ]
    ordered = [b['text'] for b in documents._two_column_order(blocks, 600)]
    assert ordered == ['left-1', 'left-2', 'left-3', 'right-0', 'right-1', 'right-2']
    single = [{'bbox': [50, 100 + i, 550, 120 + i], 'text': str(i)} for i in range(6)]
    assert documents._two_column_order(single, 600) == single


def test_page_classification():
    blank = fitz.open().new_page()
    assert documents._classify_page(blank) == 'blank'
    drawing = fitz.open().new_page()
    drawing.draw_rect(fitz.Rect(100, 100, 200, 200))
    assert documents._classify_page(drawing) == 'drawing'
    imaged = fitz.open().new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    pix.clear_with(128)
    imaged.insert_image(fitz.Rect(0, 0, 200, 200), pixmap=pix)
    assert documents._classify_page(imaged) == 'image-only'


def test_merge_lines_groups_nearby_and_splits_gaps():
    lines = [
        {'bbox': [0, 0, 100, 10], 'text': 'a'},
        {'bbox': [0, 12, 100, 22], 'text': 'b'},
        {'bbox': [0, 200, 100, 210], 'text': 'c'},
    ]
    merged = documents._merge_lines(lines)
    assert len(merged) == 2
    assert merged[0]['text'] == 'a\nb'
    assert merged[1]['text'] == 'c'


def test_rotated_watermark_does_not_absorb_title_and_body():
    lines = [{'bbox': [10, 50, 25, 500], 'text': 'arXiv watermark'},
             {'bbox': [150, 120, 450, 145], 'text': 'A Research Paper Title'},
             {'bbox': [150, 170, 450, 182], 'text': 'First Author and University'},
             {'bbox': [100, 240, 500, 250], 'text': 'Abstract body text'}]
    blocks = documents._merge_lines(lines)
    assert blocks[0]['text'] == 'arXiv watermark'
    assert documents._ocr_title(blocks, 792) == 'A Research Paper Title'


def test_scanned_title_saved_after_ocr_and_manual_title_preserved(client, monkeypatch):
    blocks = [{'bbox': [100, 80, 500, 110], 'text': 'Reliable Scan Paper Title'},
              {'bbox': [100, 160, 500, 172], 'text': 'A long enough abstract body for the OCR page.'}]
    monkeypatch.setattr(documents, '_ocr_page', lambda page: ('\n'.join(b['text'] for b in blocks), blocks, 'fixture'))
    response = client.post('/api/papers', files={'file': ('scan.pdf', raster_pdf(), 'application/pdf')})
    pid = response.json()['paper_id']
    for _ in range(200):
        paper = client.get('/api/papers/' + pid).json()
        if paper['status'] != 'processing':
            break
        time.sleep(.03)
    assert paper['title'] == 'Reliable Scan Paper Title'
    assert paper['metadata']['ocr_title_method'] == 'estimated-from-line-geometry'
    db.execute('UPDATE papers SET title=? WHERE id=?', ('My manual title', pid))
    documents.parse(pid, response.json()['job_id'])
    assert client.get('/api/papers/' + pid).json()['title'] == 'My manual title'


def test_raster_page_ocr_fallback_recovers_text(client):
    if documents._rapidocr_engine() is None:
        pytest.skip('rapidocr not installed')
    response = client.post('/api/papers', files={'file': ('scan.pdf', raster_pdf(), 'application/pdf')})
    pid = response.json()['paper_id']
    for _ in range(400):
        paper = client.get('/api/papers/' + pid).json()
        if paper['status'] != 'processing':
            break
        time.sleep(.05)
    assert paper['status'] == 'ready', paper.get('warning')
    assert paper['metadata']['page_sources'] == {'1': 'ocr'}
    assert paper['metadata']['ocr_engines']
    text = client.get('/api/papers/' + pid + '/pages/1').json()['text']
    assert 'adam' in text.lower() or 'p100' in text.lower() or 'gpu' in text.lower()


def test_blank_page_is_not_a_failure(client):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 45, 550, 780), 'Only page with extractable body text for parsing.')
    doc.new_page()
    raw = doc.tobytes()
    doc.close()
    response = client.post('/api/papers', files={'file': ('mixed.pdf', raw, 'application/pdf')})
    pid = response.json()['paper_id']
    for _ in range(200):
        paper = client.get('/api/papers/' + pid).json()
        if paper['status'] != 'processing':
            break
        time.sleep(.03)
    assert paper['status'] == 'ready'
    assert paper['metadata']['page_sources'] == {'1': 'native', '2': 'blank'}
    assert paper['metadata']['unreadable_pages'] == []
