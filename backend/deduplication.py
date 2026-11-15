"""对 PDF 正文采样，保守给出语义重复提示。"""
import hashlib

import numpy as np

from . import db, embeddings


def windows(text):
    text=' '.join(text.split())
    if len(text)<=500:
        return [text]
    positions=sorted({0,max(0,len(text)//2-250),max(0,len(text)-500)})
    return [text[i:i+500] for i in positions]


def signature(text):
    values=embeddings.encode(windows(text))
    if not values:
        return None
    vector=np.asarray(values,dtype=np.float32).mean(axis=0)
    norm=float(np.linalg.norm(vector))
    if norm<1e-12:
        return None
    return vector/norm


def similarities(info, candidates):
    sample=info.get('semantic_sample','').strip()
    if not embeddings.configured() or len(sample)<80 or not candidates:
        return {}
    space=embeddings.space_id()+':body-sample3x500:v1'
    incoming=signature(sample)
    if incoming is None:
        return {}
    values={}
    # 限制导入前检查的开销，不根据相似度自动删除文献。
    for paper in candidates[:12]:
        cached=db.one('SELECT * FROM document_vectors WHERE paper_id=? AND space=?',(paper['id'],space))
        if cached:
            vector=np.frombuffer(cached['vector'],dtype='<f4')
        else:
            from . import documents
            path=db.DATA/'files'/(paper['id']+'.pdf')
            if path.is_file():
                old=documents.preview(path.read_bytes()).get('semantic_sample','')
            else:
                pages=db.rows('SELECT text FROM pages WHERE paper_id=? ORDER BY page LIMIT 5',(paper['id'],))
                old='\n'.join(p['text'] for p in (pages[1:] or pages))
            if len(old.strip())<80:
                continue
            vector=signature(old)
            if vector is None:
                continue
            db.execute('INSERT OR REPLACE INTO document_vectors VALUES(?,?,?,?)',
                       (paper['id'],space,hashlib.sha256(old.encode()).hexdigest(),np.asarray(vector,dtype='<f4').tobytes()))
        if vector.shape==incoming.shape and np.isfinite(vector).all():
            values[paper['id']]=float(np.dot(incoming,vector))
    return values
