# 研舟 ResearchDock

传智杯科研助手赛项项目，提供 PDF 管理、论文问答、双语阅读、arXiv 检索、引用图谱和写作绘图。

源码仓库：[GitHub](https://github.com/tmu0856-eng/-)。

**云端演示已部署：[评审入口](https://39.105.29.231)（需要登录）。** 应用在服务器 Docker 中独立运行。资料包 39 份 PDF、1,986 页的 32 项真实流程检查通过；补充实测见 [验收记录](docs/VALIDATION.md)。技术说明源稿见 [技术说明](docs/TECHNICAL_REPORT.md)。

## 开始使用

要求 Python 3.11+。在项目目录打开终端：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

已安装依赖时直接 `python run.py`，或在 PowerShell 执行 `./start.ps1`。

打开 <http://127.0.0.1:8765>。API 说明位于 <http://127.0.0.1:8765/docs>。

## 模型配置

复制 `.env.example` 为 `.env`，填写服务商的基础地址、密钥、模型名并重启：

```dotenv
LLM_BASE_URL=你的兼容 Chat Completions 服务基础地址
LLM_API_KEY=你的密钥
LLM_MODEL=服务商实际支持的模型名
```

基础地址如服务商要求 `/v1` 则应包含它；后端会拼接 `/chat/completions`，不要把完整聊天接口作为基础地址。本项目不包含模型额度，调用费用以服务商为准。

本地配置使用 `mimo-v2.6-pro`，通过小米官方接口验证参数问答、缺少证据的问题、短文本翻译、单页翻译与简短论文框架。`.env.example` 提供不含密钥的 MiMo 配置示例；实际密钥只在被 Git 忽略的 `.env` 中。默认输出上限为 4096 Token，可使用 `LLM_MAX_COMPLETION_TOKENS` 调整；超过上限的回答将报错而不展示残缺 JSON。MiMo 的 JSON 模式仅保证 JSON 语法，不等于内容事实准确。[官方结构化输出说明](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/text-generation/structured-output)

没有配置模型时：文献管理、原文阅读、本地检索、版本差异、引用图谱、CSV 绘图可以使用；问答只显示原文证据，翻译与写作需要配置回答模型。

当前小内存云端版使用 MiMo API 与 BM25 + TF-IDF，保留 25,464 条已有文献向量；暂未启用远程向量编码和精排。本地版仍可使用下述 BGE-M3 与 reranker 配置。服务器与电脑的运行相互独立，详见 [独立 Docker 部署](deploy/CLOUD_DOCKER.md)。

arXiv 发现支持关键词、题名和新旧编号。默认采用 arXiv 查询相关性排序；可单独选择“最新发表”“文献库推荐”和“仅搜标题”。完整题名保留连续短语，缓存按排序、范围和策略版本隔离。接口连接失败时有限尝试两个官方入口，查询间隔至少 3.1 秒；限流时不立即重试。成功结果持久缓存在本地 SQLite，5 分钟内复用；上游连接失败时最多回退到 24 小时内的成功缓存，并在页面明确显示“非本次实时结果”和原抓取时间。没有有效缓存时显示 arXiv 专属错误，可重试、打开官网或导入手动下载的 PDF。

语义检索支持本地 **BAAI/bge-m3** 的 1024 维稠密向量，与 BM25 + TF-IDF 进行 RRF 混合检索。MiMo 仍负责回答和工具调用；本地向量编码不需要 API 密钥，不向外部发送论文。

```powershell
python -m pip install -r requirements-embedding.txt
python scripts/download_bge_m3.py
```

在 `.env` 设置后重启：

```dotenv
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_LOCAL_PATH=.local/models/bge-m3
EMBEDDING_DEVICE=auto
EMBEDDING_BATCH_SIZE=8
EMBEDDING_MAX_LENGTH=512
EMBEDDING_AUTO_INDEX=1
```

官方权重约 2.27 GB，程序优先使用可用 CUDA，GPU 使用 FP16，CPU 使用 FP32。下载脚本固定仓库 revision 并校验官方 LFS SHA-256；直连不稳定时可指定 `--download-endpoint https://hf-mirror.com`，元信息和校验值仍取自官方 API。模型缓存在忽略的 `.local/models/`，不提交到 Git。

启动时自动为已有和新导入论文补建向量，按批落盘，中断后续建；配置页显示进度并可点击“补建语义索引”。也可访问 `GET /api/embeddings/status` 和 `POST /api/embeddings/reindex`。向量按模型 revision、池化方式和编码长度隔离，更换后无需重新导入 PDF。索引未完成或模型失败时保留词频检索并显示状态/回退说明。旧版无模型标记的 `chunks.embedding` 不参与混合检索，需补建。

本次接入使用 CLS 池化和 L2 归一化的稠密向量；未使用 BGE-M3 的 sparse / ColBERT 输出。默认编码上限 512 Token，用于控制本机资源，过长文本块会截断；模型本身支持的更长长度不等于当前配置。修改 `EMBEDDING_MAX_LENGTH` 后会建立独立索引。

### 两阶段证据检索

已接入本地 `BAAI/bge-reranker-v2-m3`：先用混合检索召回最多 40 条候选，再对“问题＋原文片段”评分，按调用方需要保留证据（普通问答最多 10 条，Agent 遵守工具的返回条数）。保留论文范围、跨文覆盖及作者首页证据，最终证据重新编号；保留原始出处；表格和数值问题会补充同页正文，并按整页定位。精排失败时回退到混合召回排序，并在执行记录中显示原因。

```powershell
python -m pip install -r requirements-embedding.txt
python scripts/download_bge_m3.py --model BAAI/bge-reranker-v2-m3
```

在 `.env` 配置并重启：

```dotenv
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_LOCAL_PATH=.local/models/bge-reranker-v2-m3
RERANKER_DEVICE=auto
RERANKER_CANDIDATES=40
RERANKER_BATCH_SIZE=4
RERANKER_MAX_LENGTH=512
```

无需额外 API 密钥，也无需重建已有 BGE-M3 向量。两种本地模型串行使用 GPU 计算，降低与后台索引同时运行时的显存峰值。配置页和 `GET /api/reranker/status` 显示模型状态及最近精排耗时。清空 `RERANKER_MODEL` 可关闭精排。默认 Docker 基础镜像不安装本地模型依赖；容器使用这两个本地模型还需安装可选依赖并挂载模型目录。

实测与限制见 [精排验证记录](docs/RERANKER_CHECK.md)：当前小样本首条命中有提升，但并非每道问题的排序都会改善。评分表示相关性，不能代替原文事实核验。

也可设置 `EMBEDDING_PROVIDER=api`，指定 `EMBEDDING_MODEL`、`EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` 使用兼容服务。远程地址和密钥留空时复用聊天配置，需确认该服务实际提供向量接口。

模型调用会将所选论文片段发送给配置的服务商。导入未公开文献前，应确认自己具有相应使用与处理权限。

## 功能介绍

| 模块 | 已实现功能 |
| --- | --- |
| PDF 管理 | 批量上传、逐页结构清单、三级目录补全、独立附录、摘要与署名区提取、公式候选定位、KaTeX 排版、原图对照、逐条修改保存与恢复、最多 20 页批量视觉转录；扫描件和 541 页书籍实测 |
| 证据问答 | 动态 Agent 工具调用、本地 BGE-M3 混合召回 + bge-reranker-v2-m3 精排、多文范围、参数追问与消歧、引用编号校验、页码与区域定位；真实 MiMo 问答与 Agent 流式验证 |
| 对照阅读 | 原文页图、目录、数字引用到条目区域高亮、条目反查各处正文引用、返回原按钮与滚动位置、当前页/全文翻译和导出 |
| 检索追踪 | arXiv 检索、限定官方主机的 PDF 下载、含 arXiv 编号的参考文献批量入库、本地相关性推荐、反馈、主题定时刷新 |
| 引用图谱 | 有出处连边、交互图谱、局域 PageRank、图谱输入综述、Future Work 出版先后及逐字证据约束、可选最近研究线索；39 篇资料 261 条带出处边 |
| 写作绘图 | 框架/综述/Mermaid 脚本、真实库条目 References、学术翻译；CSV 五类图型、Python/SVG/PNG/CSV 导出 |

证据问答可切换“自主工具调用”：MiMo 原生 Function Calling 按问题选择文献列表、原文检索、指定页阅读，并可改写关键词补查。勾选“允许 arXiv 发现”才开放外部查询；返回的元信息单独展示，不作为本地原文事实的引用。文献范围由后端锁定，参数通过严格校验，重复调用跳过；最多 4 轮、8 次调用、24 条证据，整体时间预算 180 秒（正在执行的外部请求遵守各自超时，可能有少量调度开销）。工具只能读取，不能执行代码、下载、导入或删除。默认 HTTP `/api/ask` 保留直接检索，传入 `agent: true` 开启 Agent，`allow_external: true` 允许 arXiv 发现。

Agent 分为规划、核读和综合三个阶段，均使用同一模型：先拆解子查询，再调用工具收集原文，最后生成回答。也可直接检索后回答。界面通过 SSE 展示实际工具和执行结果，不展示模型私有推理；回答为模型 Token 级流式输出，在途未核对行仅作过程提示，每条声明须通过引用编号与证据校验后才作为结论展示。只验证引用编号存在不能证明每句话均被证据支持。原生工具协议依据 [MiMo 官方接口说明](https://mimo.mi.com/docs/zh-CN/api/chat/openai-api)。

作者等元信息结合 PDF 元数据和首页署名几何结构提取；可在“解析结构”查看机构与出处，缺失项不编造，并可手动编辑。局域图谱中没有匹配边，不意味着论文没有引用或没有价值。

参数追问只从之前的用户问题确定对象，重新检索原文；同时涉及 Adam、标签平滑等多个对象时先澄清，回复“Adam”等短对象名后继续处理待明确的问题。切换新主题不会沿用更早的参数对象。引用支持 `[1]`、`[1, 2]`、`[1–3]`；重复编号会标为候选匹配，不默认为某一条。验证范围见 [三项专项记录](docs/TRIAD_CHECK.json)。

## PDF 与 OCR

原始文件、SQLite、图表保存在 `data/`，已被 Git 忽略。解析器逐页写入，前端按页加载图片。扫描页先尝试 Tesseract，失败后使用 RapidOCR；识别失败的页码会记录在解析说明中。

如需在 Windows 使用 Tesseract，安装程序与语言包后，将 `TESSDATA_PREFIX` 指向 `tessdata`，设置 `OCR_LANG=eng`（中文需要相应语言包）。新上传文献逐页建立章节、附录和公式候选，已有文献可点击“更新结构清单”，保留正文与向量索引。阅读页可单页或分批转录 LaTeX、表格和图注；每批最多 20 页，开始前展示页码与调用费用提示，可取消后续页，失败页可重试，已完成页复用缓存。候选清单不代表已完成全文公式还原，视觉结果需核对原图。

## 验证与测试

```powershell
python -m pytest -q
node --check frontend/app.js
node --check frontend/reader-tools.js
python -m compileall -q backend run.py
```

测试在独立临时目录中使用**合成测试 PDF**，包括完整 541 页写入检查，不能代替官方教材的复杂排版、内存和性能测试。

真实 v1/v7 公开样本放入被忽略的 `data/samples/` 后，在应用运行且文献库已有 v7 时可执行：

```powershell
python scripts/verify_real_samples.py
```

报告生成到 `docs/REAL_SAMPLE_CHECK.json`。样本来源：[Attention Is All You Need](https://arxiv.org/abs/1706.03762)，原文版权归原作者，本仓库不分发原论文 PDF。

可选执行 `python scripts/verify_agent.py`，使用已配置的模型和库中的公开 v7 样本进行一次收费接口实测，生成 `docs/AGENT_LIVE_CHECK.json`。普通 pytest 不调用真实收费模型。

可选执行 `python scripts/verify_graph20.py`（导入 20 篇交叉引用 arXiv 文献并核验引用图谱，生成 `docs/GRAPH20_CHECK.json`）与 `python scripts/verify_llm_checks.py`（Embedding 探测、多文推理、References 核验、翻译质量真实调用，生成 `docs/LLM_LIVE_CHECK.json`）。两者会产生真实网络与模型调用。

## Docker 与部署

```powershell
docker compose up --build -d
```

Docker 包含英文 OCR 引擎（Tesseract），数据目录挂载到宿主机。Compose 默认仅绑定本机。镜像已构建并运行核验：`/api/status` 与首页返回 200。本机网络无法直连 Docker Hub 或 Debian/PyPI 官方源时，构建可加镜像参数（不传参数时行为不变）：

```powershell
docker build -t researchdock . --build-arg APT_MIRROR=mirrors.aliyun.com --build-arg PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
```

公网演示已使用 `compose.cloud.yaml` 独立部署到服务器，采用 HTTPS、评审登录、请求限额和持久化独立文献库，详见 [云端部署说明](deploy/CLOUD_DOCKER.md) 与 [云端验证记录](docs/CLOUD_DOCKER_CHECK.json)。密钥和评审账号保存在私密运行配置中，不进入镜像或 Git。云端保留公开演示数据，与日常开发库分开。

已通过真实公网登录、PDF 页图、扫描 OCR、引用定位、MiMo Agent SSE 和容器重启后的数据校验。本地双模型 Compose 是另一种部署配置，其完整 CPU 镜像仍需单独验收。备用的本机隧道方案见 [部署说明](deploy/README.md)。

## 项目结构

```text
backend/app.py          HTTP API、长任务与静态页面
backend/documents.py    PDF 提取、内容去重与版本差异
backend/retrieval.py    本地/语义检索、证据输出与引用校验
backend/agent.py        有界动态工具选择、文献范围隔离与执行记录
backend/research.py     arXiv、引用图谱与写作
backend/charts.py       CSV 校验与可复现绘图
backend/llm.py          后端模型适配
backend/db.py           SQLite 持久化
frontend/              无构建依赖的交互界面
tests/                 集成与边界测试
docs/                  技术说明与验收证据
```

项目信息：个人参赛项目；姓名、学校与组别由参赛者提交前补充。开发与实测记录见 `docs/DEVELOPMENT_LOG.md`。

公式结果可在阅读页的“查看已识别结果”打开，支持数学排版、原始页图对照、修改 LaTeX 与编号、保存及恢复原始转录。排版组件随应用提供，无需访问外部 CDN；修改不会重新调用模型。

启用评审访问控制后，未登录访问会进入独立 `/login` 页面；支持密码显隐、登录状态提示与手机布局，登录后进入工作台。
