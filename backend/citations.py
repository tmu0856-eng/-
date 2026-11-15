"""建立正文引用与参考条目的双向位置锚点。"""
import json
import re

import pymupdf as fitz

from . import db

MARKER = re.compile(r'\[([0-9][0-9,;\s–—-]{0,60})\]')


def labels(value):
    """展开有限长度的编号范围，避免异常文献生成超大引用列表。"""
    result=[]
    value=re.sub(r'\s*([–—-])\s*',r'\1',value.strip())
    for part in re.split(r'[,;\s]+',value):
        if not part:continue
        match=re.fullmatch(r'(\d{1,3})(?:[–—-](\d{1,3}))?',part)
        if not match:return []
        first=int(match[1]);last=int(match[2] or first)
        if first<1 or last<first or last-first>30:return []
        result.extend(str(n) for n in range(first,last+1))
    return list(dict.fromkeys(result))[:50]


def _compact(text):
    return re.sub(r'\W','',text).lower()


def _entry_anchor(entry, page):
    blocks=json.loads(page['blocks']);best=None
    prefix=_compact(entry['text'])[:45]
    for bi,block in enumerate(blocks):
        text=block['text']
        if entry['label']:
            matches=list(re.finditer(r'(?m)^\s*(?:\['+re.escape(entry['label'])+r'\]|'+re.escape(entry['label'])+r'\.)(?:\s|$)',text))
        else:
            words=entry['text'].split()[:3]
            match=re.search(r'\s+'.join(re.escape(w) for w in words),text) if words else None
            matches=[match] if match else []
        for match in matches:
            following=text[match.end():]
            score=0
            compact=_compact(following)
            for length in (12,22,35,45):
                if prefix[:length] and prefix[:length] in compact:score+=length
            if not entry['label']:score=40
            if score<12:continue
            next_entry=re.search(r'(?m)^\s*(?:\[\d{1,3}\]|\d{1,3}\.\s+[A-Z])',text[match.end():])
            end=match.end()+next_entry.start() if next_entry else len(text)
            candidate={'page':entry['page'],'block_index':bi,'start':match.start(),'end':end,
                       'bbox':block['bbox'],'anchor_id':f'reference-{entry["page"]}-{bi}-{match.start()}',
                       'bbox_precision':'block'}
            if best is None or score>best[0]:best=(score,candidate)
    return best[1] if best else {'page':entry['page'],'bbox':None,'anchor_id':'','bbox_precision':'page'}


def attach(pid, entries):
    pages=db.rows('SELECT page,blocks FROM pages WHERE paper_id=? ORDER BY page',(pid,))
    by_page={p['page']:p for p in pages};result=[]
    for entry in entries:
        source=_entry_anchor(entry,by_page[entry['page']]) if entry['page'] in by_page else {'page':entry['page'],'bbox':None,'anchor_id':''}
        result.append({**entry,'source':source,'occurrences':[]})
    indexed={}
    for entry in result:
        if entry['label']:indexed.setdefault(entry['label'],[]).append(entry)
    for entry in result:
        entry['ambiguous_label']=len(indexed.get(entry['label'],[]))>1
    excluded={}
    for entry in result:
        source=entry['source']
        if source.get('block_index') is not None:
            excluded.setdefault((source['page'],source['block_index']),[]).append((source['start'],source['end']))
    try:doc=fitz.open(db.DATA/'files'/(pid+'.pdf'))
    except (RuntimeError,OSError):doc=None
    count=0
    try:
        for page in pages:
            for bi,block in enumerate(json.loads(page['blocks'])):
                text=block['text']
                for match in MARKER.finditer(text):
                    if any(start<=match.start()<end for start,end in excluded.get((page['page'],bi),[])):
                        continue
                    numbers=labels(match[1]);targets=[entry for n in numbers for entry in indexed.get(n,[])]
                    if not targets:continue
                    count+=1
                    if count>5000:break
                    bbox=block['bbox'];precision='block'
                    if doc:
                        boxes=doc[page['page']-1].search_for(match[0],clip=fitz.Rect(bbox))
                        ordinal=len(list(re.finditer(re.escape(match[0]),text[:match.start()])))
                        if ordinal<len(boxes):bbox=list(boxes[ordinal]);precision='text-match'
                    anchor={'id':f'citation-{page["page"]}-{bi}-{match.start()}','page':page['page'],
                            'block_index':bi,'start':match.start(),'end':match.end(),'labels':numbers,
                            'marker':match[0],'bbox':bbox,'bbox_precision':precision,
                            'excerpt':text[max(0,match.start()-80):match.end()+120]}
                    for target in targets:
                        if len(target['occurrences'])<100:target['occurrences'].append(anchor)
            if count>5000:break
    finally:
        if doc:doc.close()
    for entry in result:
        entry['occurrence_count']=len(entry['occurrences'])
        entry['occurrences_limited']=count>5000 or len(entry['occurrences'])==100
    return result
