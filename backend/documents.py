"""逐页解析 PDF 并保存原文出处。"""
import hashlib
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from difflib import SequenceMatcher

import pymupdf as fitz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import db, deduplication, embeddings, llm, metadata, structure

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
                        # 忽略旋转的 arXiv 水印和小字号版权页眉。
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
            body='\n'.join(doc[i].get_text(sort=True) for i in range(1,min(5,len(doc)))) or first_page_text
            return {'title': title[:300], 'sample': first_page_text[:16000], 'page_count': len(doc),
                    'semantic_sample':body[:60000],
                    'arxiv_id': match.group(1) if match else '',
                    'version': int(match.group(2)) if match and match.group(2) else None,
                    'pdf_metadata': doc.metadata, 'needs_ocr': len(text.strip()) < 80}
    except (RuntimeError, fitz.FileDataError) as exc:
        raise ValueError('无法读取 PDF，文件可能损坏或并非 PDF。') from exc


def find_duplicates(raw, info):
    candidates = db.rows("SELECT * FROM papers WHERE status IN ('ready','partial','processing')")
    found = []
    sha = fingerprint(raw)
    semantic={}
    if not any(p['sha']==sha for p in candidates):
        try:
            # 先检查可能的同篇文献，再进行数量受限的语义比较。
            ranked=sorted(candidates,key=lambda p:SequenceMatcher(None,normalize(info['title']),normalize(p['title'])).ratio(),reverse=True)
            semantic=deduplication.similarities(info,ranked)
        except llm.ModelError as exc:
            info['dedup_warning']=str(exc)+'；本次使用文件与词频内容对比。'
    for paper in candidates:
        if paper['sha'] == sha:
            found.append({'paper': paper, 'score': 1.0, 'reason': '文件内容完全一致','method':'sha256','recommendation':'keep_existing'})
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
        # 需同时核对正文；参考文献中的 arXiv 编号不能证明文件属于同一论文。
        semantic_score=semantic.get(paper['id'])
        semantic_match=semantic_score is not None and semantic_score>=.96 and (same_id or title_score>.7 or text_score>.5)
        if (title_score > .91 and text_score > .65) or text_score > .91 or (same_id and text_score > .72) or semantic_match:
            reason = f'标题/正文内容高度相似（正文 {text_score:.0%}）'
            if semantic_score is not None:
                reason+=f'；BGE-M3 正文样本语义相似度 {semantic_score:.1%}'
            recommendation='compare'
            if paper['version'] and info['version']:
                if paper['version']>info['version']:
                    reason+='；库中版本较新，建议保留';recommendation='keep_existing'
                elif paper['version']<info['version']:
                    reason+='；新上传版本较新，建议优先保留新版本';recommendation='keep_incoming'
                else:
                    reason+='；版本号相同，可对比正文后选择'
            found.append({'paper': paper, 'score': round(text_score, 3), 'reason': reason,
                          'semantic_score':round(semantic_score,5) if semantic_score is not None else None,
                          'method':'lexical+semantic' if semantic_score is not None else 'lexical','recommendation':recommendation})
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
    return {'paper_id': pid, 'job_id': jid, 'warning':info.get('dedup_warning','')}


_RAPIDOCR = None
_RAPIDOCR_LOCK = threading.Lock()


def _rapidocr_engine():
    global _RAPIDOCR
    if _RAPIDOCR is False:
        return None
    with _RAPIDOCR_LOCK:
        if _RAPIDOCR is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                _RAPIDOCR = RapidOCR()
            except Exception:
                _RAPIDOCR = False
    return _RAPIDOCR or None


def _two_column_order(blocks, width):
    """明确为双栏时，先读取完整左栏，再读取右栏。"""
    if len(blocks) < 6 or width <= 0:
        return blocks
    centers = sorted((b['bbox'][0] + b['bbox'][2]) / 2 for b in blocks)
    gap, split_index = max((centers[i + 1] - centers[i], i) for i in range(len(centers) - 1))
    if gap < width * .05:
        return blocks
    mid = (centers[split_index] + centers[split_index + 1]) / 2
    left = [b for b in blocks if (b['bbox'][0] + b['bbox'][2]) / 2 < mid]
    right = [b for b in blocks if (b['bbox'][0] + b['bbox'][2]) / 2 >= mid]
    if len(left) < 2 or len(right) < 2:
        return blocks
    order = lambda b: (round(b['bbox'][1], 1), b['bbox'][0])
    return sorted(left, key=order) + sorted(right, key=order)


def _merge_lines(lines, max_chars=900):
    """将 OCR 行合并为段落块，保留可用于检索的上下文。"""
    merged = []
    for line in lines:
        if merged and len(merged[-1]['text']) + len(line['text']) + 1 <= max_chars:
            last = merged[-1]
            previous = last['lines'][-1]
            gap = line['bbox'][1] - previous['bbox'][3]
            height = previous['bbox'][3] - previous['bbox'][1]
            horizontal = min(previous['bbox'][2], line['bbox'][2]) - max(previous['bbox'][0], line['bbox'][0])
            # 高幅旋转水印不能合并后续全部正文行。
            if (-2 <= gap < height * 1.5 and horizontal > 0
                    and previous['bbox'][2] - previous['bbox'][0] > height * 1.5):
                last['text'] += '\n' + line['text']
                last['lines'].append(line)
                last['bbox'] = [min(last['bbox'][0], line['bbox'][0]), min(last['bbox'][1], line['bbox'][1]),
                                max(last['bbox'][2], line['bbox'][2]), max(last['bbox'][3], line['bbox'][3])]
                continue
        merged.append({'bbox': list(line['bbox']), 'text': line['text'], 'lines': [line]})
    return merged


def _ocr_title(blocks, page_height):
    """根据 OCR 几何信息估计显著的横向题名，不让模型猜测。"""
    lines = [line for block in blocks for line in block.get('lines', [block])]
    candidates = []
    for line in lines:
        x0, y0, x1, y1 = line['bbox']
        text = line['text'].strip()
        height = y1 - y0
        if (len(text) > 6 and 0 < height and y0 < page_height * .5 and x1-x0 > height * 2
                and not re.search(r'arxiv|doi:|copyright|permission|provided proper|@|^abstract$', text, re.I)):
            candidates.append((height, y0, text))
    if not candidates:
        return ''
    largest = max(c[0] for c in candidates)
    chosen = sorted((c for c in candidates if c[0] >= largest * .88), key=lambda c: c[1])
    top = chosen[0][1]
    return ' '.join(c[2] for c in chosen if c[1] < top + largest * 3)[:300]


def _classify_page(page):
    """将无可提取文本的页面分为空白页、矢量图表公式页或纯图像页。"""
    if page.get_image_info():
        return 'image-only'
    if page.get_drawings():
        return 'drawing'
    return 'blank'


def _ocr_page(page):
    """先尝试 MuPDF 的 Tesseract，再尝试 150 dpi 页图的 RapidOCR，返回文本、文本块和引擎名称。"""
    dpi = int(os.getenv('OCR_DPI', '150'))
    try:
        text_page = page.get_textpage_ocr(language=os.getenv('OCR_LANG', 'eng'), dpi=dpi, full=True)
        text = page.get_text(textpage=text_page, sort=True).strip()
        raw = page.get_text('blocks', textpage=text_page, sort=True)
        blocks = [{'bbox': list(b[:4]), 'text': b[4].strip()} for b in raw if b[6] == 0 and b[4].strip()]
        return text, blocks, 'tesseract'
    except Exception:
        pass
    engine = _rapidocr_engine()
    if engine is None:
        return '', [], None
    try:
        import numpy as np
        pix = page.get_pixmap(dpi=dpi)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3][:, :, ::-1].copy()
        result, _ = engine(image)
        if not result:
            return '', [], 'rapidocr'
        scale = dpi / 72
        lines = []
        for box, value, _score in result:
            xs = [p[0] / scale for p in box]
            ys = [p[1] / scale for p in box]
            lines.append({'bbox': [min(xs), min(ys), max(xs), max(ys)], 'text': str(value).strip()})
        lines.sort(key=lambda l: (round(l['bbox'][1], 1), l['bbox'][0]))
        lines = _two_column_order(lines, page.rect.width)
        blocks = _merge_lines(lines)
        return '\n\n'.join(b['text'] for b in blocks).strip(), blocks, 'rapidocr'
    except Exception:
        logging.getLogger(__name__).exception('rapidocr failed on a page')
        return '', [], None


def parse(pid, jid):
    warnings, toc, images, tables = [], [], [], []
    page_sources, review_pages, ocr_engines = {}, [], set()
    headings, formulas = [], []
    try:
        db.execute("UPDATE jobs SET status='running',message='提取页面与位置锚点' WHERE id=?", (jid,))
        with fitz.open(db.DATA / 'files' / (pid + '.pdf')) as doc:
            toc = [{'level': item[0], 'title': item[1], 'page': item[2]} for item in doc.get_toc()]
            native_toc = bool(toc)
            first_text, ocr_title, structured_authors = [], '', []
            for index, page in enumerate(doc):
                text = page.get_text(sort=True).strip()
                source = 'native' if len(text) >= 30 else None
                blocks = []
                if source is None:
                    ocr_text, ocr_blocks, engine = _ocr_page(page)
                    if len(ocr_text) >= 30:
                        text, blocks, source = ocr_text, ocr_blocks, 'ocr'
                        ocr_engines.add(engine)
                    else:
                        source = _classify_page(page)
                        text = ocr_text
                        blocks = ocr_blocks or ([{'bbox': [0, 0, page.rect.width, page.rect.height], 'text': ocr_text}] if ocr_text else [])
                        if source == 'image-only':
                            review_pages.append(index + 1)
                else:
                    raw_blocks = page.get_text('blocks', sort=True)
                    blocks = [{'bbox': list(b[:4]), 'text': b[4].strip()} for b in raw_blocks if b[6] == 0 and b[4].strip()]
                    blocks = _two_column_order(blocks, page.rect.width)
                    ordered = '\n\n'.join(b['text'] for b in blocks).strip()
                    # 按分栏顺序读取，保持参考文献标题和条目连续。
                    if ordered:
                        text = ordered
                page_sources[index + 1] = source
                if index==0 and source in ('native','ocr'):
                    current=db.one('SELECT title FROM papers WHERE id=?',(pid,))
                    structured_authors=metadata.authors_from_header(blocks,page.rect.height,current['title'])
                    if source=='ocr':
                        for author in structured_authors:
                            author['method']='ocr-header-estimate'
                if index == 0 and source == 'ocr':
                    ocr_title = _ocr_title(blocks, page.rect.height)
                if index < 3:
                    first_text.append(text)
                # 保留页面区域坐标，便于回到原图核对公式和表格。
                for image in page.get_image_info():
                    images.append({'page': index + 1, 'bbox': list(image['bbox'])})
                try:
                    for table in page.find_tables().tables:
                        tables.append({'page': index + 1, 'bbox': list(table.bbox), 'cells': table.extract()})
                except Exception:
                    pass
                items=structure.inspect_page(page,blocks,index+1,source)
                headings.extend(items['headings']);formulas.extend(items['formulas'])
                with db.connect() as con:
                    con.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?,?)', (pid, index + 1, text, db.encode(blocks), page.rect.width, page.rect.height))
                    for block in blocks:
                        content = block['text']
                        for start in range(0, len(content), 1200):
                            piece = content[start:start + 1450]
                            if len(piece.strip()) > 15:
                                con.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)', (uuid.uuid4().hex, pid, index + 1, piece, db.encode(block['bbox']), None))
                    con.execute('UPDATE jobs SET done=?,message=? WHERE id=?', (index + 1, f'已解析 {index + 1}/{len(doc)} 页', jid))
            import json
            abstract = metadata.abstract_from_blocks([json.loads(x['blocks']) for x in db.rows(
                'SELECT blocks FROM pages WHERE paper_id=? AND page<=2 ORDER BY page',(pid,))])
            author_line = (doc.metadata.get('author') or '').strip() or '; '.join(a['name'] for a in structured_authors)
        blank_pages = sorted(p for p, s in page_sources.items() if s == 'blank')
        drawing_pages = sorted(p for p, s in page_sources.items() if s == 'drawing')
        if review_pages:
            warnings.append(f'{len(review_pages)} 页为扫描/图像页且 OCR 未成功（例如第 {", ".join(map(str, review_pages[:8]))} 页），需人工核对；OCR 引擎缺失时扫描件解析不视为通过。')
        if blank_pages:
            warnings.append(f'{len(blank_pages)} 页为空白页（无文本无图像），属正常排版，不计为解析失败。')
        if drawing_pages:
            warnings.append(f'{len(drawing_pages)} 页为纯图形/公式页（无文本层），文本无从提取，原文页图保留可查。')
        if ocr_engines:
            warnings.append('OCR 引擎：' + '、'.join(sorted(ocr_engines)) + '。')
        warnings.append('已逐页建立公式候选与结构清单；复杂公式需通过批量视觉转录核对，不将候选文本当作可靠 LaTeX。')
        meta = {'toc': toc[:1000], 'images': images, 'tables': tables, 'unreadable_pages': review_pages,
                'page_sources': page_sources, 'ocr_engines': sorted(ocr_engines),
                'formula_mode': 'original-page', 'reading_order': 'column-aware-geometric'}
        meta['authors_structured']=structured_authors
        meta.update(structure.assemble(toc if native_toc else [],headings,formulas,len(page_sources),page_sources,structured_authors))
        paper_info=db.one('SELECT arxiv_id FROM papers WHERE id=?',(pid,))
        meta['publication']=metadata.publication(first_text[0] if first_text else '',paper_info['arxiv_id'])
        if ocr_title:
            meta['ocr_title_candidate'] = ocr_title
            meta['ocr_title_method'] = 'estimated-from-line-geometry'
        with db.connect() as con:
            if ocr_title:
                con.execute("UPDATE papers SET title=? WHERE id=? AND title='未识别标题'", (ocr_title, pid))
            con.execute("UPDATE papers SET authors=CASE WHEN COALESCE(authors,'')='' THEN ? ELSE authors END,abstract=?,status=?,warning=?,metadata=? WHERE id=?",
                        (author_line, abstract, 'partial' if review_pages else 'ready', '\n'.join(warnings), db.encode(meta), pid))
            con.execute("UPDATE jobs SET status='done',message='解析完成' WHERE id=?", (jid,))
        # 索引独立运行，模型失败不影响 PDF 解析结果。
        embeddings.start_indexing()
    except Exception as exc:
        logging.getLogger(__name__).exception('PDF parse failed for %s', pid)
        db.execute("UPDATE jobs SET status='failed',message=? WHERE id=?", ('解析失败：' + type(exc).__name__, jid))
        db.execute("UPDATE papers SET status='failed',warning=? WHERE id=?", ('PDF 解析失败，请检查文件。' + type(exc).__name__, pid))


def enrich_header(pid):
    paper=db.one('SELECT * FROM papers WHERE id=?',(pid,))
    page=db.one('SELECT * FROM pages WHERE paper_id=? AND page=1',(pid,))
    if not paper or not page:
        raise ValueError('论文首页尚未解析。')
    import json
    meta=json.loads(paper['metadata'])
    meta['publication']=metadata.publication(page['text'],paper['arxiv_id'])
    authors=metadata.authors_from_header(json.loads(page['blocks']),page['height'],paper['title'])
    if meta.get('page_sources',{}).get('1')=='ocr':
        for author in authors:author['method']='ocr-header-estimate'
    meta['authors_structured']=authors
    db.execute("UPDATE papers SET authors=CASE WHEN COALESCE(authors,'')='' THEN ? ELSE authors END,metadata=? WHERE id=?",
               ('; '.join(a['name'] for a in authors),db.encode(meta),pid))
    return {'authors':authors,'note':'按首页版式提取；请点击原文核对作者、机构关系。'}


def refresh_structure(pid, jid=None):
    """重建已导入文献的结构清单，不删除正文、向量或手工作者信息。"""
    import json
    value=db.one('SELECT * FROM papers WHERE id=?',(pid,))
    if not value or value['status'] not in ('ready','partial'):
        raise ValueError('请先等待文献解析完成。')
    meta=json.loads(value['metadata']);headings=[];formulas=[]
    pages=db.rows('SELECT * FROM pages WHERE paper_id=? ORDER BY page',(pid,))
    sources=meta.get('page_sources',{})
    with fitz.open(db.DATA/'files'/(pid+'.pdf')) as doc:
        native=[{'level':x[0],'title':x[1],'page':x[2]} for x in doc.get_toc()]
        for saved in pages:
            blocks=json.loads(saved['blocks'])
            items=structure.inspect_page(doc[saved['page']-1],blocks,saved['page'],sources.get(str(saved['page']),'native'))
            headings.extend(items['headings']);formulas.extend(items['formulas'])
            if jid:db.execute('UPDATE jobs SET done=?,message=? WHERE id=?',(saved['page'],f'已核对结构 {saved["page"]}/{len(doc)} 页',jid))
    authors=enrich_header(pid)['authors']
    # enrich_header 可能补充出版信息，重新读取后合并，避免覆盖其他元数据。
    meta=json.loads(db.one('SELECT metadata FROM papers WHERE id=?',(pid,))['metadata'])
    meta.update(structure.assemble(native,headings,formulas,len(pages),sources,authors))
    abstract=metadata.abstract_from_blocks([json.loads(p['blocks']) for p in pages[:2]])
    db.execute("UPDATE papers SET metadata=?,abstract=CASE WHEN COALESCE(abstract,'')='' THEN ? ELSE abstract END WHERE id=?",
               (db.encode(meta),abstract,pid))
    return meta['structure_coverage']


def version_diff(left, right):
    a = db.rows('SELECT text FROM pages WHERE paper_id=? ORDER BY page', (left,))
    b = db.rows('SELECT text FROM pages WHERE paper_id=? ORDER BY page', (right,))
    if not a or not b:
        raise ValueError('两个版本均需完成解析。')
    # 比较规范化后的段落内容，不仅比较文件字节或文件名。
    aa = [p.strip() for page in a for p in page['text'].split('\n') if p.strip()]
    bb = [p.strip() for page in b for p in page['text'].split('\n') if p.strip()]
    matcher = SequenceMatcher(None, aa, bb, autojunk=True)
    changes = []
    for tag, i, j, k, l in matcher.get_opcodes():
        if tag != 'equal':
            changes.append({'kind': tag, 'before': '\n'.join(aa[i:j])[:1800], 'after': '\n'.join(bb[k:l])[:1800]})
    return {'similarity': round(matcher.ratio(), 3), 'change_count': len(changes), 'changes': changes[:80], 'truncated': len(changes) > 80}
