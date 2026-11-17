"""生成与报名表第六项七条要求对应的作品介绍。"""
import sys
from pathlib import Path
from PIL import Image as PILImage
from reportlab.platypus import SimpleDocTemplate,Spacer,PageBreak,Table,TableStyle,Image,KeepTogether
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import build_technical_report as base

OUT=ROOT/'output/pdf/研舟_作品介绍_穆宣廷_太原科技大学.pdf'
p=base.p;table=base.table
WIDTH=489

def heading(text):return p(text,'HeadingCJK')
def sub(text):return p(text,'SubCJK')
def screenshot(path,caption,width=489,height=245):
    path=ROOT/path
    with PILImage.open(path) as im:w,h=im.size
    scale=min(width/w,height/h)
    return [Image(str(path),width=w*scale,height=h*scale),Spacer(1,8),p(caption,'SmallCJK')]

def footer(c,doc):
    c.setStrokeColor(colors.HexColor('#D7E3DC'));c.line(52,46,A4[0]-52,46)
    c.setFillColor(base.GRAY);c.setFont('ReportCJK',8)
    c.drawString(52,30,'RESEARCHDOCK / 作品介绍 / 2026-11-16')
    c.drawRightString(A4[0]-52,30,str(doc.page))

story=[Spacer(1,44),p('研舟\nResearchDock','CoverCJK'),p('基于大模型与 Agent 的科研辅助工作台','SubCJK'),Spacer(1,18),heading('作品介绍'),
       table([['姓名','学校'],['穆宣廷','太原科技大学']],[180,309]),Spacer(1,24),
       p('把论文导入、原文查证、双语阅读、引用分析与写作绘图放在同一个工作台。结果保留来源，阅读可以回到原文，材料可以继续整理。'),Spacer(1,10),
       table([['文献与页数','真实流程检查','自动回归'],['40 篇 / 2,010 页','32 项通过','275 项通过']],[163,163,163]),Spacer(1,25),
       p('在线演示：https://39.105.29.231\n源码仓库：https://github.com/tmu0856-eng/-','SmallCJK'),
       Spacer(1,18),p('本说明按项目详情、项目亮点、AI 技术与测试、关键技术、项目图片、发表获奖情况、项目基础七部分组织。','SmallCJK')]

story += [PageBreak(),heading('01  项目详情与使用说明'),
 p('研舟面向科研论文阅读和材料整理，涵盖 PDF 文献管理、证据问答、双语阅读、论文发现、引用分析和写作绘图。系统保存原始 PDF、页码、文本区域与引用关系，支持从回答、公式和图谱信息回到原文核读。'),
 table([['使用步骤','具体操作'],
 ['导入文献','登录工作台，单篇或批量导入 PDF，查看解析进度、页数与版本提示。'],
 ['阅读与提问','选择论文，查看目录、作者与摘要；输入单篇或多篇问题，点击回答出处定位原文。'],
 ['翻译与公式','切换中文译文，查看参考条目；对照原图查看公式排版，修改后保存与重新打开。'],
 ['发现与分析','通过题名、英文关键词或编号检索论文；设置追踪主题，查看引用图谱和节点关系。'],
 ['整理与输出','生成论文框架、综述和架构脚本，使用 CSV 绘图，导出译文、图像、数据或脚本。']],[87,402]),Spacer(1,22),
 heading('02  项目亮点'),
 p('文献与结果相连。回答保存证据页码和区域，正文引用与参考条目双向定位，图谱连边保留出处，公式关联原始页面、转录与修改记录。'),
 p('追问与范围相连。后续问题可承接已引用文献，参数对象不明确时先澄清；回答重新检索原文，多文比较分别保留各篇证据。'),
 p('阅读与整理相连。导入后可以连续进入阅读、翻译、引用分析和写作；已识别的页面、译文和检索结果可以缓存复用。'),
 p('配置与环境相配。本地配置支持语义召回和精排，轻量云端使用远程模型接口与词项检索，分别提供可复现的环境配置。')]

story += [PageBreak(),heading('03  AI 技术落地与测试情况'),
 p('AI 功能直接参与文献问答、工具选择、页面视觉识别、翻译与写作。检索与来源约束由后端管理，界面展示实际工具调用和原文出处。'),
 table([['组件','集成方式与用途'],
 ['MiMo / mimo-v2.6-pro','通过兼容接口接入文本回答、JSON 输出、工具调用与单页图像识别。'],
 ['Agent 工具流程','按规划、核读和综合阶段调用文献列表、原文搜索、指定页读取，以及用户开启的 arXiv 发现。'],
 ['本地 BGE-M3 与精排','1024 维 dense 向量结合 BM25、TF-IDF，经 RRF 融合后由 bge-reranker-v2-m3 精排。'],
 ['轻量云端配置','MiMo 远程接口 + BM25 与 TF-IDF；文献与已有向量缓存保存到持久化目录。']],[126,363]),Spacer(1,22),
 sub('测试与验证'),
 table([['验证内容','实际记录'],
 ['自动回归','275 项通过；覆盖解析、追问、检索、工具参数、接口、公式保存和导出。'],
 ['完整资料包流程','39 份 PDF、1,986 页；32/32 项真实流程检查通过。'],
 ['补充专项','扩展到 40 份 PDF、2,010 页；覆盖公式编辑、登录、完整题名检索和引用定位。'],
 ['部署与数据','公网 HTTPS、登录、SSE、OCR 与容器重启持久化检查通过。']],[126,363]),Spacer(1,12),
 p('普通回归采用隔离配置与模型测试替身；真实流程使用实际模型和外部接口分别核验。样本包含 Attention v1/v7、扫描 PDF、BERT、Layer Normalization 和 541 页教材，记录见源码 docs 目录。','SmallCJK')]

story += [PageBreak(),heading('04  关键技术与系统架构'),base.Architecture(),Spacer(1,15),
 sub('文档解析与长文档处理'),
 p('PyMuPDF 提取页图、文本块与 bbox，OCR 处理扫描页。章节结合原生目录、编号、字号和几何信息组织，文献逐页处理，前端按需加载。版本检查综合使用哈希、论文编号、正文近似度与可选语义签名。'),
 sub('证据链与交互状态'),
 p('后端锁定文献范围，管理来源编号与引用原句；回答、正文引用和图谱连边均可回查出处。公式通过本地 KaTeX 排版，支持原图对照、编辑、保存、恢复和修订冲突检查。'),
 sub('检索、引用分析与可视化'),
 p('arXiv 提供相关性、最新发表与文献库推荐模式，可限定标题；缓存区分检索式、排序与字段。图谱基于真实参考条目匹配和局域 PageRank，CSV 绘图提供图像、图注、数据与 Python 脚本。'),
 sub('工程与部署'),
 p('FastAPI 提供 HTTP 与 SSE，SQLite WAL 保存文献、证据、任务和缓存。云端由独立 Docker 容器与 Nginx HTTPS 反向代理组成，模型密钥通过环境变量配置，数据保存在持久化目录。')]

story += [PageBreak(),heading('05  项目相关图片'),
 p('以下图片来自系统实际界面，展示登录、文献问答和原文来源等操作。','SmallCJK'),Spacer(1,15)]
story += screenshot('docs/screenshots/login-desktop.jpg','图 1  独立登录页：作品入口与评审访问。',height=245)
story += [Spacer(1,22)]
story += screenshot('docs/screenshots/qa-workflow.jpg','图 2  证据问答：文献范围、实际执行记录与带出处结果。',height=245)

left=screenshot('docs/screenshots/formula-edit-saved.jpg','图 3  公式原图对照、编辑与保存。',width=230,height=238)
right=screenshot('docs/screenshots/arxiv-relevance.jpg','图 4  完整题名检索、排序与论文结果。',width=230,height=238)
gallery=Table([[left,right]],colWidths=[244.5,244.5],hAlign='LEFT')
gallery.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),12)]))
story += [PageBreak(),heading('05  项目界面展示（续）'),gallery,Spacer(1,24),
 heading('06  发表与获奖情况'),
 p('根据参赛者确认，截至本次提交材料整理时，本作品未发表、未获奖。'),Spacer(1,10),
 heading('07  项目基础与改进说明'),
 p('根据参赛者确认，本作品不属于在其他现成完整项目基础上优化的作品。系统按本赛项科研助手需求组织实现；使用的 MiMo、BGE、KaTeX、PyMuPDF 等模型与组件在技术说明、依赖文件和相关许可证中列明。'),
 p('开发中使用 Codex 辅助代码编写、调试、测试与文档整理，具体实现通过真实 PDF、接口与浏览器操作核验。','SmallCJK')]

OUT.parent.mkdir(parents=True,exist_ok=True)
doc=SimpleDocTemplate(str(OUT),pagesize=A4,leftMargin=52,rightMargin=52,topMargin=48,bottomMargin=64,
                      title='研舟 ResearchDock 作品介绍',author='穆宣廷',pageCompression=1)
doc.build(story,onFirstPage=footer,onLaterPages=footer)
print(str(OUT))
