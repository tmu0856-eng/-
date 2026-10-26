# 研舟 ResearchDock

面向传智杯 AI 大模型科研助手赛项的本地开发项目。连接文献导入、证据问答、对照阅读、论文发现、引用图谱与学术输出。

**当前为可运行初版，不代表已满足全部比赛要求。** 详见 [开发规划](PLAN.md) 和 [验收记录](docs/VALIDATION.md)。

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

基础地址如服务商要求 `/v1` 则应包含它；后端会拼接 `/chat/completions`，不要把完整聊天接口作为基础地址。未选择固定商业模型，费用取决于实际服务商及用量。

没有配置模型时：文献管理、原文阅读、本地检索、版本差异、引用图谱、CSV 绘图可以使用；问答只显示原文证据，翻译与写作明确提示未配置，不返回伪造的 AI 内容。

语义检索可选设置 `EMBEDDING_MODEL`，默认复用模型地址与密钥；也可单独指定 `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY`。仅新导入文献建立语义向量；更换向量模型后需重新导入，不能混用不同向量空间。

模型调用会将所选论文片段发送给配置的服务商。导入未公开文献前，应确认自己具有相应使用与处理权限。

## 六模块的实际状态

| 模块 | 初版已有 | 仍需补足 / 验证 |
| --- | --- | --- |
| PDF 管理 | 批量上传、异步逐页解析、编辑元信息、删除、页图、正文块与坐标、表格提取、版本去重与正文差异 | OCR 环境、复杂双栏阅读顺序、机构结构化提取、复杂公式 LaTeX 还原 |
| 证据问答 | BM25 + TF-IDF 检索、可选 Embedding、多文范围、引用编号校验、页码与区域定位 | 实际模型正确率、专业重排、无证据边界、复杂跨文推理 |
| 对照阅读 | 原文页图、可选文本、目录、引用条目与原文页跳转、当前页/全文翻译、选段翻译、滚动比例联动、译文导出 | 悬停翻译、跨页段落精确对齐、复杂引用/书籍结构适配 |
| 检索追踪 | arXiv 真检索、限定官方主机的 PDF 下载、本地相关性推荐、代码链接标识、点赞/踩、主题定时刷新 | 引用含金量语境判断、多用户推荐、下载与外部接口的更广覆盖 |
| 引用图谱 | 本地 References 题名匹配、每条边保留出处、交互图谱、局域 PageRank、基于文献的综述草稿 | 20 篇官方样本、DOI/变体题名消歧、前沿时间线与 Future Work 已解决判断 |
| 写作绘图 | 论文框架/综述/Mermaid 脚本、学术翻译；CSV 折线/柱状/散点/雷达/三维雷达、真实图像、Python/SVG/PNG/CSV 导出、描述性图注 | 模型 References 事实核验、架构图脚本渲染、更多学术图型与编辑 |

现有执行轨迹是有边界的固定工作流，不是完成了自主多智能体规划。SSE 提供阶段状态与经编号校验后的最终回答，尚非模型 Token 级流式输出。只验证引用编号存在不能证明每句话均被证据支持。

作者等元信息优先来自 PDF 元数据；缺失时不编造，可在阅读页手动核对编辑。局域图谱中没有匹配边，不意味着论文没有引用或没有价值。

## PDF 与 OCR

原始文件、SQLite、图表保存在 `data/`，已被 Git 忽略。解析器逐页写入，前端按页加载图片。扫描页会尝试 PyMuPDF 的 Tesseract OCR；失败时标为“需核对”，记录没有提取文本的页码。

Windows 需另外安装 Tesseract 与语言包，将 `TESSDATA_PREFIX` 指向 `tessdata`，设置 `OCR_LANG=eng`（中文需要相应语言包）。复杂公式暂以原文图片保留，不应将此视为 LaTeX 结构还原完成。

## 验证与测试

```powershell
python -m pytest -q
node --check frontend/app.js
python -m compileall -q backend run.py
```

测试在独立临时目录中使用**合成测试 PDF**，包括完整 541 页写入检查，不能代替官方教材的复杂排版、内存和性能测试。

真实 v1/v7 公开样本放入被忽略的 `data/samples/` 后，在应用运行且文献库已有 v7 时可执行：

```powershell
python scripts/verify_real_samples.py
```

报告生成到 `docs/REAL_SAMPLE_CHECK.json`。样本来源：[Attention Is All You Need](https://arxiv.org/abs/1706.03762)，原文版权归原作者，本仓库不分发原论文 PDF。

## Docker 与部署

```powershell
docker compose up --build -d
```

Docker 包含英文 OCR 引擎，数据目录挂载到宿主机。Compose 默认仅绑定本机。Docker 运行环境本次尚未验收。

提交赛项要求公网演示；当前项目只提供本地服务，**尚未公网部署**。上线前应加登录/评审账号、上传与模型调用限流、TLS、资源额度、监控及数据备份；不可将包含私有文献的本地开发版直接无鉴权暴露到公网。

## 项目结构

```text
backend/app.py          HTTP API、长任务与静态页面
backend/documents.py    PDF 提取、内容去重与版本差异
backend/retrieval.py    本地/语义检索、证据输出与引用校验
backend/research.py     arXiv、引用图谱与写作
backend/charts.py       CSV 校验与可复现绘图
backend/llm.py          后端模型适配
backend/db.py           SQLite 持久化
frontend/              无构建依赖的交互界面
tests/                 集成与边界测试
docs/                  开发记录与验收证据
```

项目信息：个人参赛项目；姓名、学校与组别由参赛者提交前补充。开发与实测记录见 `docs/DEVELOPMENT_LOG.md`。
