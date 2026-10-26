"""PDF extraction and provenance. Long PDFs are processed page by page."""
import hashlib
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from difflib import SequenceMatcher

import fitz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import db, llm

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pdf-parser')
LOCK = threading.RLock()
MAX_BYTES = 80 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def normalize(value):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', value.lower())


def fingerprint(raw):
    return hashlib.sha256(raw).hexdigest()


def preview(raw):
    try:
        with fitz.open(stream=raw, filetype='pdf') as doc:
            if doc.needs_pass:
                raise ValueError('暂不支持加密 PDF，请先使用有权解密的副本。')
            if not doc.page_count:
                raise ValueError('PDF 没有页面。')
            first_page_text = doc[0].get_text(sort=True)
            text = '\n'.join(doc[i].get_text(sort=True) for i in range(min(3, len(doc))))
            lines = [s.strip() for s in text.splitlines() if len(s.strip()) > 8]
            meta_title = (doc.metadata.get('title') or '').strip()
            title = meta_title if len(meta_title) > 8 and meta_title.lower() not in ('untitled', 'document') else ''
            if not title:
                candidates = []
                for block in doc[0].get_text('dict')['blocks']:
                    for line in block.get('lines', []):
                        # Ignore rotated arXiv stamps and small licensing headers.
                        if abs(line.get('dir', (1, 0))[1]) > .1:
                            continue
                        value = ''.join(s['text'] for s in line['spans']).strip()
                        size = max((s['size'] for s in line['spans']), default=0)
                        if len(value) > 6 and line['bbox'][1] < doc[0].rect.height * .55 and not re.search(r'arxiv:|doi:|copyright|permission|provided proper', value, re.I):
                            candidates.append((size, line['bbox'][1], value))
                if candidates:
                    largest = max(c[0] for c in candidates)
                    chosen = sorted([c for c in candidates if c[0] >= largest * .96], key=lambda c: c[1])
                    top = chosen[0][1]
                    title = ' '.join(c[2] for c in chosen if c[1] < top + largest * 4)
                title = title or (lines[0] if lines else '未识别标题')
            match = re.search(r'(?:arXiv\s*:\s*)?(\d{4}\.\d{4,5})(?:v(\d+))?', text, re.I)
            return {'title': title[:300], 'sample': first_page_text[:16000], 'page_count': len(doc),
                    'arxiv_id': match.group(1) if match else '',
                    'version': int(match.group(2)) if match and match.group(2) else None,
                    'pdf_metadata': doc.metadata, 'needs_ocr': len(text.strip()) < 80}
    except (RuntimeError, fitz.FileDataError) as exc:
        raise ValueError('无法读取 PDF，文件可能损坏或并非 PDF。') from exc


def find_duplicates(raw, info):
    candidates = db.rows("SELECT * FROM papers WHERE status IN ('ready','partial','processing')")
    found = []
    sha = fingerprint(raw)
    for paper in candidates:
        if paper['sha'] == sha:
            found.append({'paper': paper, 'score': 1.0, 'reason': '文件内容完全一致'})
            continue
        existing = db.one('SELECT text FROM pages WHERE paper_id=? ORDER BY page LIMIT 1', (paper['id'],))
        old_sample = existing['text'] if existing else ''
        old_title = normalize(paper['title'])
        title_score = SequenceMatcher(None, normalize(info['title']), old_title).ratio() if old_title else 0
        text_score = 0.0
        if old_sample and len(info['sample']) > 80:
            try:
                vectors = TfidfVectorizer(analyzer='char', ngram_range=(3, 5), max_features=12000).fit_transform([info['sample'][:10000], old_sample[:10000]])
                text_score = float(cosine_similarity(vectors[0], vectors[1])[0, 0])
            except ValueError:
                pass
        same_id = bool(info['arxiv_id'] and info['arxiv_id'] == paper['arxiv_id'])
        # Require content agreement; an arXiv identifier in a reference alone is not identity.
        if (title_score > .91 and text_score > .65) or text_score > .91 or (same_id and text_score > .72):
            reason = f'标题/正文内容高度相似（正文 {text_score:.0%}）'
            if paper['version'] and info['version']:
                reason += '；库中版本较新，建议保留' if paper['version'] > info['version'] else '；可保留两个版本用于对比'
            found.append({'paper': paper, 'score': round(text_score, 3), 'reason': reason})
    return found


def enqueue(raw, filename, info):
    pid, jid = uuid.uuid4().hex, uuid.uuid4().hex
    path = db.DATA / 'files' / (pid + '.pdf')
    path.write_bytes(raw)
    try:
        with db.connect() as con:
            con.execute('INSERT INTO papers(id,filename,title,authors,abstract,sha,version,arxiv_id,page_count,status,warning,created,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (pid, filename, info['title'], '', '', fingerprint(raw), info['version'], info['arxiv_id'], info['page_count'], 'processing', '', now(), db.encode(info['pdf_metadata'])))
            con.execute('INSERT INTO jobs(id,paper_id,status,total,message,created) VALUES(?,?,?,?,?,?)', (jid, pid, 'queued', info['page_count'], '等待解析', now()))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    POOL.submit(parse, pid, jid)
    return {'paper_id': pid, 'job_id': jid}


def parse(pid, jid):
    warnings, toc, images, tables, empty_pages = [], [], [], [], []
    try:
        db.execute("UPDATE jobs SET status='running',message='提取页面与位置锚点' WHERE id=?", (jid,))
        with fitz.open(db.DATA / 'files' / (pid + '.pdf')) as doc:
            toc = [{'level': item[0], 'title': item[1], 'page': item[2]} for item in doc.get_toc()]
            native_toc = bool(toc)
            first_text = []
            for index, page in enumerate(doc):
                text_page = None
                text = page.get_text(sort=True).strip()
                if len(text) < 30:
                    try:
                        import os
                        text_page = page.get_textpage_ocr(language=os.getenv('OCR_LANG', 'eng'), dpi=150, full=True)
                        text = page.get_text(textpage=text_page, sort=True).strip()
                    except Exception:
                        empty_pages.append(index + 1)
                raw_blocks = page.get_text('blocks', textpage=text_page, sort=True)
                blocks = [{'bbox': list(b[:4]), 'text': b[4].strip()} for b in raw_blocks if b[6] == 0 and b[4].strip()]
                if index < 3:
                    first_text.append(text)
                # Keep page rectangles: formula/table images remain verifiable in the original.
                for image in page.get_image_info():
                    images.append({'page': index + 1, 'bbox': list(image['bbox'])})
                try:
                    for table in page.find_tables().tables:
                        tables.append({'page': index + 1, 'bbox': list(table.bbox), 'cells': table.extract()})
                except Exception:
                    pass
                if not native_toc:
                    for line in text.splitlines():
                        if re.match(r'^\d+(?:\.\d+){0,2}\s+[A-Z\u4e00-\u9fff]', line) and len(line) < 130:
                            # Estimated headings, separate from native bookmarks.
                            toc.append({'level': min(3, line.split()[0].count('.') + 1), 'title': line, 'page': index + 1, 'estimated': True})
                with db.connect() as con:
                    con.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?,?)', (pid, index + 1, text, db.encode(blocks), page.rect.width, page.rect.height))
                    for block in blocks:
                        content = block['text']
                        for start in range(0, len(content), 1200):
                            piece = content[start:start + 1450]
                            if len(piece.strip()) > 15:
                                con.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)', (uuid.uuid4().hex, pid, index + 1, piece, db.encode(block['bbox']), None))
                    con.execute('UPDATE jobs SET done=?,message=? WHERE id=?', (index + 1, f'已解析 {index + 1}/{len(doc)} 页', jid))
                del text_page
            combined = '\n'.join(first_text)
            abstract_match = re.search(r'\bAbstract\b\s*[:\n]?(.{50,2200}?)(?:\n\s*(?:1\s+|Introduction|Keywords)|$)', combined, re.S | re.I)
            abstract = abstract_match.group(1).strip() if abstract_match else ''
            author_line = (doc.metadata.get('author') or '').strip()
        if empty_pages:
            warnings.append(f'{len(empty_pages)} 页未能提取文本（例如第 {", ".join(map(str, empty_pages[:8]))} 页）；需要 OCR 引擎/语言包，不代表扫描件解析通过。')
        warnings.append('公式保留原文页图；复杂公式的 LaTeX 结构还原尚未验收。')
        meta = {'toc': toc[:1000], 'images': images, 'tables': tables, 'unreadable_pages': empty_pages,
                'formula_mode': 'original-page', 'reading_order': 'geometric-sort'}
        with db.connect() as con:
            con.execute('UPDATE papers SET authors=?,abstract=?,status=?,warning=?,metadata=? WHERE id=?',
                        (author_line, abstract, 'partial' if empty_pages else 'ready', '\n'.join(warnings), db.encode(meta), pid))
            con.execute("UPDATE jobs SET status='done',message='解析完成' WHERE id=?", (jid,))
        # Embeddings are optional. Failure is visible and does not destroy parsed text.
        if __import__('os').getenv('EMBEDDING_MODEL'):
            try:
                chunks = db.rows('SELECT id,text FROM chunks WHERE paper_id=?', (pid,))
                for offset in range(0, len(chunks), 16):
                    batch = chunks[offset:offset + 16]
                    values = llm.embed([c['text'] for c in batch])
                    if values:
                        with db.connect() as con:
                            for c, v in zip(batch, values):
                                con.execute('UPDATE chunks SET embedding=? WHERE id=?', (db.encode(v), c['id']))
            except llm.ModelError as exc:
                db.execute('UPDATE papers SET warning=warning || ? WHERE id=?', ('\n' + str(exc), pid))
    except Exception as exc:
        logging.getLogger(__name__).exception('PDF parse failed for %s', pid)
        db.execute("UPDATE jobs SET status='failed',message=? WHERE id=?", ('解析失败：' + type(exc).__name__, jid))
        db.execute("UPDATE papers SET status='failed',warning=? WHERE id=?", ('PDF 解析失败，请检查文件。' + type(exc).__name__, pid))


def version_diff(left, right):
    a = db.rows('SELECT text FROM pages WHERE paper_id=? ORDER BY page', (left,))
    b = db.rows('SELECT text FROM pages WHERE paper_id=? ORDER BY page', (right,))
    if not a or not b:
        raise ValueError('两个版本均需完成解析。')
    # Compare normalized paragraphs, not bytes or filenames.
    aa = [p.strip() for page in a for p in page['text'].split('\n') if p.strip()]
    bb = [p.strip() for page in b for p in page['text'].split('\n') if p.strip()]
    matcher = SequenceMatcher(None, aa, bb, autojunk=True)
    changes = []
    for tag, i, j, k, l in matcher.get_opcodes():
        if tag != 'equal':
            changes.append({'kind': tag, 'before': '\n'.join(aa[i:j])[:1800], 'after': '\n'.join(bb[k:l])[:1800]})
    return {'similarity': round(matcher.ratio(), 3), 'change_count': len(changes), 'changes': changes[:80], 'truncated': len(changes) > 80}
