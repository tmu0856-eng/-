"""结合原文区域提取作者信息，无法确认的字段保留为空。"""
import re


INSTITUTION=re.compile(r'University|Institute|Google|OpenAI|Research|Brain|Laborator|School|College|Department|Academy|Microsoft|Facebook|大学|研究所|学院|实验室|公司',re.I)
EMAIL=re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')


def publication(text,arxiv_id=''):
    """使用带日期的论文标识或页脚作为出版依据，不把 PDF 创建时间当成出版时间。"""
    identifier=re.fullmatch(r'(\d{2})(\d{2})\.\d{4,5}(?:v\d+)?',arxiv_id or '')
    if identifier and 1<=int(identifier[2])<=12:
        year=(2000 if int(identifier[1])<90 else 1900)+int(identifier[1])
        return {'date':f'{year:04d}-{int(identifier[2]):02d}','precision':'month','source':'arxiv_identifier','evidence':arxiv_id}
    footer=re.search(r'(?:Proceedings of|©|copyright|c⃝)[^\n]{0,120}?\b((?:19|20)\d{2})\b',text,re.I)
    if footer:
        return {'date':footer[1],'precision':'year','source':'publication_footer','evidence':footer[0]}
    return {}


def _name(text):
    value=text.strip().strip('*∗†‡↑0123456789¹²³⁴⁵⁶⁷⁸⁹ ,;')
    if INSTITUTION.search(value) or '@' in value or len(value)>85:
        return ''
    if re.fullmatch(r'[\u4e00-\u9fff]{2,5}',value):
        return value
    words=value.split()
    if not 2<=len(words)<=6:
        return ''
    if all(w[0].isupper() and all(c.isalpha() or c in ".-'’" for c in w) for w in words):
        return value
    return ''


def authors_from_header(blocks,height,title):
    abstract_y=min((b['bbox'][1] for b in blocks if re.match(r'^\s*(?:Abstract|摘要)\s*$',b['text'],re.I)),default=height*.48)
    header=sorted((b for b in blocks if b['bbox'][1]<abstract_y and b['bbox'][2]-b['bbox'][0]>b['bbox'][3]-b['bbox'][1]),
                  key=lambda b:(round(b['bbox'][1]/3),b['bbox'][0]))
    result=[]
    normalized_title=''.join(title.lower().split())
    for block in header:
        text=block['text']
        if (len(text)>1600 or re.search(r'arxiv:|permission|copyright|provided proper',text,re.I)
                or ''.join(text.lower().split())==normalized_title):
            continue
        lines=[line.strip() for line in text.splitlines() if line.strip()]
        # 题名可能跨多行，不能误识别为多个作者。
        if lines and all(''.join(line.lower().split()) in normalized_title for line in lines):
            continue
        if lines and len(lines[0])>7 and ''.join(lines[0].lower().split()) in normalized_title:
            continue
        names=[]
        for line in lines:
            name=_name(line)
            if not name:
                break
            names.append(name)
        if not names or not any('@' in line or INSTITUTION.search(line) for line in lines[len(names):]):
            continue
        institutions=[re.sub(r'^and\s+','',line,flags=re.I) for line in lines[len(names):] if INSTITUTION.search(line) and '@' not in line]
        emails=EMAIL.findall(text)
        shared=re.search(r'\{([^}]+)\}@([\w.-]+\.[A-Za-z]{2,})',text)
        derived=False
        if shared:
            prefixes=[x.strip() for x in shared[1].split(',')]
            if len(prefixes)==len(names) and all(re.fullmatch(r'[\w.+-]+',x) for x in prefixes):
                emails=[prefix+'@'+shared[2] for prefix in prefixes];derived=True
        for i,name in enumerate(names):
            if any(a['name']==name for a in result):
                continue
            result.append({'order':len(result)+1,'name':name,'affiliations':institutions,
                           'email':emails[i] if len(emails)==len(names) else '', 'email_derived_from_group':derived,
                           'page':1,'bbox':block['bbox'],'source_text':text,'method':'first-page-layout-estimate'})
    return result
