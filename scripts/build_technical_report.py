"""依据已验证记录生成技术说明，不写入个人身份或密钥。需要 reportlab 与中文字体；参赛信息在最终登记时补充。"""
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle,getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate,Paragraph,Spacer,PageBreak,Table,TableStyle,Flowable)

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/pdf/ResearchDock_技术说明_校核稿.pdf'
FONT=Path('C:/Windows/Fonts/simhei.ttf')
if not FONT.exists():
    raise SystemExit('Provide a Chinese TTF font in FONT before rebuilding this report.')
pdfmetrics.registerFont(TTFont('ReportCJK',str(FONT)))
pdfmetrics.registerFontFamily('ReportCJK',normal='ReportCJK',bold='ReportCJK',italic='ReportCJK',boldItalic='ReportCJK')
GREEN=colors.HexColor('#31675A');INK=colors.HexColor('#20332D');GRAY=colors.HexColor('#65736E')
styles=getSampleStyleSheet()
styles.add(ParagraphStyle(name='BodyCJK',fontName='ReportCJK',fontSize=10.5,leading=18,spaceAfter=11,textColor=INK,wordWrap='CJK'))
styles.add(ParagraphStyle(name='HeadingCJK',fontName='ReportCJK',fontSize=20,leading=29,spaceAfter=24,textColor=GREEN,wordWrap='CJK'))
styles.add(ParagraphStyle(name='SubCJK',fontName='ReportCJK',fontSize=12,leading=20,spaceBefore=8,spaceAfter=10,textColor=GREEN,wordWrap='CJK'))
styles.add(ParagraphStyle(name='SmallCJK',fontName='ReportCJK',fontSize=8.7,leading=14,textColor=GRAY,wordWrap='CJK'))
styles.add(ParagraphStyle(name='CoverCJK',fontName='ReportCJK',fontSize=34,leading=48,textColor=INK,spaceAfter=26))


def p(text,style='BodyCJK'):
    return Paragraph(escape(str(text)).replace('\n','<br/>'),styles[style])


def table(rows,widths):
    cells=[[p(cell,'SmallCJK') for cell in row] for row in rows]
    t=Table(cells,colWidths=widths,hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E3EEE8')),
                          ('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),8),
                          ('TOPPADDING',(0,0),(-1,-1),8),('LEFTPADDING',(0,0),(-1,-1),8),
                          ('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#D5E0DA'))]))
    return t


class Architecture(Flowable):
    def __init__(self):
        super().__init__();self.width=480;self.height=206
    def draw(self):
        c=self.canv
        labels=[['浏览器六模块','FastAPI / SSE','证据与结果校验'],['PDF / OCR / 块坐标','混合召回 + 精排','MiMo 文本 / 工具 / 视觉'],['SQLite + 向量缓存','本地 BGE / 云端词频','arXiv / 原文 PDF']]
        for row,items in enumerate(labels):
            for col,label in enumerate(items):
                x=col*162;y=155-row*68
                c.setFillColor(colors.HexColor('#EFF5F0'));c.setStrokeColor(colors.HexColor('#BCD0C3'))
                c.roundRect(x,y,151,43,6,stroke=1,fill=1)
                c.setFillColor(INK);c.setFont('ReportCJK',9.5);c.drawCentredString(x+75.5,y+17,label)
                if row<2:
                    c.setStrokeColor(GREEN);c.line(x+75.5,y,x+75.5,y-22)
                    c.line(x+75.5,y-22,x+72.5,y-18);c.line(x+75.5,y-22,x+78.5,y-18)


def load_sections():
    """以已维护的技术说明为唯一正文来源，避免生成器覆盖新内容。"""
    text=(ROOT/'docs/TECHNICAL_REPORT.md').read_text(encoding='utf8')
    sections=[];items=None;paragraph=[];rows=[]
    def flush():
        if paragraph:
            items.append(('text',' '.join(paragraph)));paragraph.clear()
        if rows:
            cleaned=[row for row in rows if not all(set(cell.strip())<=set('-: ') for cell in row)]
            items.append(('table',cleaned));rows.clear()
    for line in text.splitlines():
        if line.startswith('## '):
            if items is not None:flush()
            items=[];sections.append((line[3:].strip(),items))
        elif items is None:continue
        elif line.startswith('### '):
            flush();items.append(('sub',line[4:].strip()))
        elif line.startswith('|'):
            if paragraph:flush()
            rows.append([cell.strip() for cell in line.strip().strip('|').split('|')])
        elif not line.strip():flush()
        elif '详见 PDF 架构图' in line:flush();items.append(('diagram',None))
        else:
            if rows:flush()
            paragraph.append(line.strip())
    if items is not None:flush()
    return sections


def footer(c,doc):
    c.setStrokeColor(colors.HexColor('#D7E3DC'));c.line(52,46,A4[0]-52,46)
    c.setFillColor(GRAY);c.setFont('ReportCJK',8)
    c.drawString(52,30,'RESEARCHDOCK / 技术校核稿 / 2026-11-16')
    c.drawRightString(A4[0]-52,30,f'{doc.page}')


def main():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    story=[Spacer(1,68),p('研舟\nResearchDock','CoverCJK'),p('基于大模型与 Agent 的科研辅助工作台','SubCJK'),Spacer(1,26),
           p('技术说明 · 提交前校核稿','HeadingCJK'),p('版本日期：2026 年 11 月 16 日'),Spacer(1,24),
           table([['文献实测','真实流程','自动回归'],['40 份 / 2,010 页','云端 Docker / HTTPS','275 项通过']],[163,163,163]),Spacer(1,30),
           p('支持 PDF 上传、正文检索、论文问答、翻译、引用分析和 CSV 绘图。'),
           p('本稿记录功能、实现和测试结果。公网评审入口：https://39.105.29.231；参赛信息待最终登记。','SmallCJK')]
    markdown=['# 研舟 ResearchDock 技术说明（提交前校核稿）','日期：2026-11-16','参赛信息和公网地址待最终登记。']
    for title,items in load_sections():
        story.extend([PageBreak(),p(title,'HeadingCJK')]);markdown.extend(['','## '+title])
        for kind,value in items:
            if kind=='diagram':
                story.extend([Architecture(),Spacer(1,12)]);markdown.append('前端 → FastAPI/SSE → PDF/OCR、检索精排、MiMo → SQLite/本地模型/arXiv（详见 PDF 架构图）。')
            elif kind=='table':
                widths=[110,175,204] if len(value[0])==3 else [489/len(value[0])]*len(value[0])
                story.extend([table(value,widths),Spacer(1,16)])
                markdown.extend(['| '+' | '.join(row)+' |' for row in value[:1]]+['| '+' | '.join(['---']*len(value[0]))+' |']+['| '+' | '.join(row)+' |' for row in value[1:]])
            else:
                story.append(p(value,'SubCJK' if kind=='sub' else 'BodyCJK'))
                markdown.extend(['',('### ' if kind=='sub' else '')+value])
    doc=SimpleDocTemplate(str(OUT),pagesize=A4,leftMargin=52,rightMargin=52,topMargin=52,bottomMargin=64,
                          title='研舟 ResearchDock 技术说明（校核稿）',author='ResearchDock',pageCompression=1)
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    print(str(OUT))


if __name__=='__main__':main()
