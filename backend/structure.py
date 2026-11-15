"""逐页建立章节、附录和公式候选清单，保留原文与区域坐标。"""
import re
import unicodedata
from collections import Counter

VERSION = 2
APPENDIX = re.compile(r'^(?:Appendix(?:\s+[A-Z0-9]+)?|Appendices|Supplementary\s+Material|附录(?:\s*[A-Z一二三四五六七八九十0-9]+)?)(?:\s*[:.：、-]?\s*.*)?$', re.I)
NUMBERED = re.compile(r'^(\d{1,2}(?:\.\d{1,2}){0,2})\.?\s+([^\d\s].*)$')
LETTERED = re.compile(r'^([A-Z](?:\.\d{1,2}){0,2})[.\s]\s*([A-Z\u4e00-\u9fff].*)$')
MATH = re.compile(r'[=∑∏∫√≈≠≤≥∈∂∇]|\\(?:frac|sum|int|sqrt)\b')


def page_lines(page, blocks, source):
    lines=[]
    if source=='native':
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines',[]):
                if abs(line.get('dir',(1,0))[1])>.1:
                    continue
                spans=line.get('spans',[])
                text=''.join(s.get('text','') for s in spans).strip()
                if text:
                    lines.append({'text':text,'bbox':list(line['bbox']),
                                  'size':max((s.get('size',0) for s in spans),default=0),
                                  'bold':any(re.search(r'bold|black|semibold',s.get('font',''),re.I) for s in spans),
                                  'math':any(re.search(r'math|cmsy|cmmi|symbol',s.get('font',''),re.I) for s in spans)})
    else:
        for block in blocks:
            for line in block.get('lines',[]):
                lines.append({'text':line['text'].strip(),'bbox':line['bbox'],'size':0,'bold':False,'math':False})
            if not block.get('lines'):
                lines.extend({'text':text.strip(),'bbox':block['bbox'],'size':0,'bold':False,'math':False}
                             for text in block['text'].splitlines() if text.strip())
    return lines


def inspect_page(page, blocks, number, source):
    lines=page_lines(page,blocks,source)
    # 某些书籍把章节编号和标题存为两行；按字号及几何间距连接。
    joined=[]
    for i,line in enumerate(lines):
        if re.fullmatch(r'(?:\d{1,2}|[A-Z])(?:\.\d{1,2}){1,2}',line['text']) and i+1<len(lines):
            after=lines[i+1]
            if (abs(after['bbox'][1]-line['bbox'][1])<max(5,line['size']*1.8)
                    and abs(after['size']-line['size'])<.5 and after['bold']):
                joined.append({**line,'text':line['text']+' '+after['text'],
                               'bbox':[min(line['bbox'][0],after['bbox'][0]),min(line['bbox'][1],after['bbox'][1]),
                                       max(line['bbox'][2],after['bbox'][2]),max(line['bbox'][3],after['bbox'][3])]})
    lines.extend(joined)
    sizes=Counter()
    for line in lines:
        if line['size'] and len(line['text'])>25:
            sizes[round(line['size'],1)]+=len(line['text'])
    body_size=sizes.most_common(1)[0][0] if sizes else 0
    headings=[]
    for line in lines:
        text=line['text']
        if source!='native':
            text=re.sub(r'^(\d{1,2}(?:\.\d{1,2}){0,2})(?=[A-Z\u4e00-\u9fff])',r'\1 ',text)
        if not 2<=len(text)<=110 or text.endswith(('.',',',';','。','；')) or MATH.search(text):
            continue
        appendix=APPENDIX.fullmatch(text)
        numbered=NUMBERED.fullmatch(text)
        lettered=LETTERED.fullmatch(text)
        if numbered and int(numbered[1].split('.')[0])>40:
            numbered=None
        if numbered and not re.search(r'[A-Za-z\u4e00-\u9fff]',numbered[2]):
            numbered=None
        # 长正文句子、图表题和目录页中的点线不作为章节标题。
        if re.search(r'\.{3,}|\s\d+\s*$|^(?:Figure|Table|图|表)\s*\d',text,re.I):
            continue
        if len(text.split())>13:
            continue
        styled=line['bold'] or (body_size and line['size']>=body_size*1.12)
        if appendix and source=='native' and body_size and not styled:
            appendix=None
        if numbered and not styled and not re.match(r'[A-Z\u4e00-\u9fff]',numbered[2]):
            numbered=None
        if (numbered or lettered) and body_size and line['size']<body_size*.94:
            continue
        if appendix or (numbered and (styled or len(text.split())<=8)) or (lettered and styled):
            label=numbered[1] if numbered else lettered[1] if lettered else ''
            headings.append({'level':min(3,label.count('.')+1),'title':text,'page':number,
                             'bbox':line['bbox'],'estimated':True,'kind':'appendix' if appendix else 'section',
                             'method':'page-layout' if source=='native' else 'ocr-layout'})
    formulas=[]
    for index,block in enumerate(blocks):
        text=block['text'].strip()
        # 这里只建立候选，不把丢失上下标的文本伪装为已还原的 LaTeX。
        if not 3<=len(text)<=2200 or not MATH.search(text):
            continue
        math_lines=[line for line in lines if MATH.search(line['text']) and
                    line['bbox'][0]>=block['bbox'][0]-2 and line['bbox'][1]>=block['bbox'][1]-2 and
                    line['bbox'][2]<=block['bbox'][2]+2 and line['bbox'][3]<=block['bbox'][3]+2]
        symbolic=any(line['math'] for line in math_lines)
        short_equation=any(len(line['text'].split())<=12 and re.search(r'[=∑∫√]|\\frac',line['text']) for line in math_lines)
        if not symbolic and not short_equation:
            continue
        formulas.append({'id':f'formula-{number}-{index}','page':number,'block_index':index,
                         'bbox':list(block['bbox']),'text':text,'latex':'','status':'candidate',
                         'source':source,'note':'原文公式候选；LaTeX 尚未转录，请使用批量公式识别并核对原图。'})
    return {'headings':headings,'formulas':formulas}


def assemble(native_toc, headings, formulas, page_count, sources, authors):
    toc=[dict(item) for item in native_toc] if native_toc else []
    if not toc:
        seen=set()
        for item in headings:
            key=(item['page'],item['title'])
            if key not in seen:
                toc.append(item);seen.add(key)
    else:
        def title_key(text):
            text=re.sub(r'^(?:\d{1,2}|[A-Z])(?:\.\d{1,2}){0,2}\.?\s+','',text)
            return re.sub(r'\W','',unicodedata.normalize('NFKC',text)).lower()
        known={(item['page'],title_key(item['title'])) for item in toc}
        native_titles={title_key(item['title']) for item in toc}
        added=set()
        for item in headings:
            key=(item['page'],title_key(item['title']))
            full=unicodedata.normalize('NFKC',item['title'])
            if key not in known and key[1] not in native_titles and full not in added and item['level']>=2:
                toc.append(item);known.add(key);added.add(full)
        toc.sort(key=lambda x:(x['page'],x.get('bbox',[0,0])[1]))
    appendices=[]
    start=None
    appendix_seen=set()
    for item in sorted(headings,key=lambda x:(x['page'],x['bbox'][1])):
        key=unicodedata.normalize('NFKC',item['title'])
        if key in appendix_seen:continue
        if item['kind']=='appendix':
            start=item['page']
            appendices.append(item)
            appendix_seen.add(key)
        elif start and item['page']>=start and LETTERED.fullmatch(item['title']):
            appendices.append({**item,'kind':'appendix'})
            appendix_seen.add(key)
    for item in toc:
        if APPENDIX.fullmatch(item['title']) and not any(a['page']==item['page'] and a['title']==item['title'] for a in appendices):
            appendices.append({**item,'kind':'appendix','method':'native-bookmark'})
    candidate_pages=sorted({f['page'] for f in formulas}|{int(p) for p,s in sources.items() if s in ('image-only','drawing')})
    return {'structure_version':VERSION,'toc':toc[:1500],'appendices':appendices[:300],
            'formulas':formulas[:4000],'formula_pages':candidate_pages,
            'formula_mode':'automatic-candidates+visual-transcription',
            'structure_coverage':{'pages':page_count,'author_count':len(authors),
                                  'heading_count':len(toc),'appendix_count':len(appendices),
                                  'formula_candidates':len(formulas),'formula_candidate_pages':len(candidate_pages),
                                  'note':'计数表示提取覆盖，不代表准确率；公式候选仍需转录，未识别结构不等于原文不存在。'}}
