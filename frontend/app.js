/* 研舟界面：动态文本统一转义，不接受模型生成的可执行 HTML。 */
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
  library:'<path d="M4 4h6v16H4zM14 4l5-1 3 15-5 1zM4 8h6M4 16h6M16 7l5-1"/>',
  ask:'<path d="M4 4h16v12H9l-5 4zM8 8h8M8 12h5"/>',
  reader:'<path d="M12 5C7 2 3 4 3 4v15s5-2 9 1c4-3 9-1 9-1V4s-4-2-9 1zm0 0v15"/>',
  discover:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6M10 7v6M7 10h6"/>',
  graph:'<circle cx="12" cy="4" r="2"/><circle cx="4" cy="18" r="2"/><circle cx="20" cy="18" r="2"/><path d="m11 6-6 10m8-10 6 10M6 18h12"/>',
  writing:'<path d="m4 16-1 5 5-1L21 7l-4-4zM14 6l4 4M12 21h9"/>',
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6"/>',
  pages:'<path d="M6 3h9l4 4v14H6zM15 3v5h4M10 12h5M10 16h5"/>',
  check:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.pages}</svg>`;
const views = [{id:'library',name:'文献库',en:'LIBRARY'}, {id:'ask',name:'证据问答',en:'PAPER Q&A'}, {id:'reader',name:'对照阅读',en:'READER'}, {id:'discover',name:'发现与追踪',en:'ARXIV SEARCH'}, {id:'graph',name:'引用图谱',en:'CITATION GRAPH'}, {id:'writing',name:'写作与绘图',en:'WRITING & CHARTS'}];
const state = {view:'library',papers:[],status:{},filter:'all',search:'',selected:new Set(),chat:[],askBusy:false,agentEnabled:true,agentExternal:false,reader:null,page:1,highlight:null,translated:false,sync:true,writingKind:'outline',writingOutput:'',discoverResults:[],discoverQuery:'',discoverSort:'relevance',discoverField:'all',discoverHasSearched:false,discoverBusy:false,discoverError:null,graphScale:1};
let renderVersion = 0, toastTimer;

async function api(url, options={}) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); } catch { throw new Error('服务返回格式异常，请检查后端是否运行'); }
  if (!response.ok) {
    if(response.status===401)renderLogin();
    const detail = Array.isArray(data.detail) ? data.detail.map(x=>x.msg).join('；') : data.detail;
    const error = new Error(detail || '请求失败'); error.status=response.status; error.data=data; throw error;
  }
  return data;
}
const post = (url,data) => api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function toast(message) { const el=$('#toast'); el.textContent=message; el.classList.add('visible'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.classList.remove('visible'),5000); }
function modal(html) { $('#modal').classList.remove('formula-dialog'); $('#modal-content').innerHTML=html; if(!$('#modal').open) $('#modal').showModal(); }
function closeModal() { $('#modal').close(); }
function fail(error, target) { if(target) target.innerHTML=`<div class="error-box">${esc(error.message)}</div>`; else toast(error.message); }
function loading(label='正在整理研究资料…') { return `<div class="loading"><span class="spinner"></span>${esc(label)}</div>`; }
function heading(view,subtitle,actions='') { return `<div class="heading"><div><div class="eyebrow">${view.en}</div><h1>${view.name}</h1><p>${subtitle}</p></div>${actions ? `<div class="btn-row">${actions}</div>`:''}</div>`; }
function download(text,name,type='text/markdown') { const a=document.createElement('a'), url=URL.createObjectURL(new Blob([text],{type}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000); }
function date(value) { return value?new Date(value).toLocaleDateString('zh-CN'):''; }
const readyPapers = () => state.papers.filter(p=>['ready','partial'].includes(p.status));
const statusName = s => ({ready:'已解析',partial:'需核对',processing:'解析中',failed:'解析失败'}[s] || s);

function nav() {
  $('#navigation').innerHTML=views.map(v=>`<a href="#${v.id}" class="${state.view===v.id?'active':''}" ${state.view===v.id?'aria-current="page"':''}>${icon(v.id)}<span>${v.name}</span>${v.id==='library'?`<span class="nav-count">${state.papers.length}</span>`:''}</a>`).join('');
  $('#breadcrumb-title').textContent=views.find(v=>v.id===state.view)?.name || '文献库';
}
async function refresh() {
  [state.papers,state.status]=await Promise.all([api('/api/papers'),api('/api/status')]);
  const indicator=$('#model-status');indicator.className='model-status'+(state.status.llm_configured?' ready':'');
  indicator.innerHTML=`<i></i>${state.status.llm_configured?esc(state.status.model):'本地模式 · 模型未配置'}`;
  for(const id of state.selected) if(!state.papers.some(p=>p.id===id)) state.selected.delete(id);
  nav();
}
async function route() {
  if(state.auth?.enabled&&!state.auth.authenticated){renderLogin();return;}
  const id=location.hash.slice(1).split('?')[0] || 'library';
  state.view=views.some(v=>v.id===id)?id:'library';
  $('.sidebar').classList.remove('open'); nav();
  const version=++renderVersion;
  try {
    if(state.view==='library') renderLibrary();
    if(state.view==='ask') renderAsk();
    if(state.view==='reader') await renderReader(version);
    if(state.view==='discover') await renderDiscover(version);
    if(state.view==='graph') await renderGraph(version);
    if(state.view==='writing') renderWriting();
  } catch(error) { if(version===renderVersion) fail(error,$('#main')); }
}

function libraryRows() {
  const papers=state.papers.filter(p=>(state.filter==='all'||p.status===state.filter) && (p.title+' '+p.filename+' '+p.authors).toLowerCase().includes(state.search.toLowerCase()));
  if(!state.papers.length) return `<div class="empty-state"><div class="empty-illustration"><div class="paper-back"></div><div class="paper-front"><i></i><i></i><i></i><i></i></div><span class="paper-plus">＋</span></div><h3>文献库为空</h3><p>上传 PDF 后，可以阅读正文、检索内容和查看引用。</p><button class="btn primary" data-action="upload">＋ 导入第一篇论文</button></div>`;
  if(!papers.length) return '<div class="empty-state"><h3>暂时没有匹配的文献</h3><p>试试其他关键词或切换筛选条件。</p></div>';
  return `<table class="paper-table"><thead><tr><th>论文名称</th><th>页数</th><th>状态</th><th>导入时间</th><th>操作</th></tr></thead><tbody>${papers.map(p=>`<tr><td><button class="paper-title-btn" data-action="read" data-id="${p.id}">${esc(p.title)}</button><div class="paper-sub">${esc(p.authors||p.filename)}${p.version?' · v'+p.version:''}</div></td><td>${p.page_count} 页</td><td><span class="status-pill ${p.status}" title="${esc(p.warning)}">${statusName(p.status)}</span></td><td>${date(p.created)}</td><td><button class="btn small ghost" data-action="read" data-id="${p.id}">阅读 ↗</button><button class="btn small ghost danger" data-action="delete" data-id="${p.id}" aria-label="删除 ${esc(p.title)}">×</button></td></tr>`).join('')}</tbody></table>`;
}
function stats() { const ready=readyPapers().length, pending=state.papers.filter(p=>p.status==='processing').length;return [ ['library',state.papers.length,'篇','全部文献'],['pages',state.papers.reduce((n,p)=>n+p.page_count,0),'页','原文页数'],['check',ready,'篇','可检索文献'],['clock',pending,'项','正在解析'] ].map(([i,n,u,t])=>`<div class="stat"><span class="stat-icon">${icon(i)}</span><div><strong>${n}<small>${u}</small></strong><label>${t}</label></div></div>`).join(''); }
function renderLibrary() {
  $('#main').innerHTML=heading(views[0],'上传、检索和管理 PDF 文献。',`<button class="btn" data-action="diff">⇄ 版本对比</button><button class="btn primary" data-action="upload">＋ 导入文献</button>`)+`
    <section class="intro-banner"><div><div class="eyebrow">PDF 文献管理</div><h2>导入论文后开始阅读</h2><p>支持批量上传、正文检索、引用定位和版本对比。</p></div><div class="journey"><span><b>01</b>导入文献</span><i>―</i><span><b>02</b>证据阅读</span><i>―</i><span><b>03</b>整理资料</span></div></section>
    <section class="stats" id="stats">${stats()}</section><div class="section-heading"><h2>我的文献 <small id="paper-count">${state.papers.length} 篇论文</small></h2><span class="muted">仅保存于当前运行环境</span></div>
    <div class="library-toolbar"><div class="filters">${[['all','全部文献'],['ready','已解析'],['processing','解析中'],['partial','需核对']].map(([k,n])=>`<button class="filter ${state.filter===k?'active':''}" data-action="filter" data-filter="${k}">${n}</button>`).join('')}</div><label class="search"><span class="search-symbol">⌕</span><input id="library-search" placeholder="搜索论文、作者或文件名" value="${esc(state.search)}" aria-label="搜索文献"><kbd>⌘ K</kbd></label></div>
    <section class="panel"><div id="library-rows">${libraryRows()}</div><div class="drop-zone" id="drop-zone" tabindex="0" role="button" aria-label="拖入或选择 PDF">${icon('upload')}<div>将 PDF 拖到这里，或点击选择文件<small>支持批量导入 · 单文件最大 80 MB · 扫描件需要 OCR 引擎</small></div></div><div class="library-foot"><span>◈ 原文文件与页码锚点一并保留</span><span>支持批量 PDF 上传</span></div></section>
    <section class="quick-grid"><a class="quick-card" href="#ask"><span class="quick-icon">${icon('ask')}</span><div><strong>论文问答</strong><small>选择文献，提问并查看出处</small></div><span class="quick-arrow">↗</span></a><a class="quick-card" href="#discover"><span class="quick-icon">${icon('discover')}</span><div><strong>检索论文</strong><small>搜索 arXiv 或追踪主题</small></div><span class="quick-arrow">↗</span></a><a class="quick-card" href="#writing"><span class="quick-icon">${icon('writing')}</span><div><strong>写作与绘图</strong><small>生成提纲、整理综述或上传 CSV 绘图</small></div><span class="quick-arrow">↗</span></a></section><section class="jobs" id="jobs"></section>`;
  $('#library-search').addEventListener('input',e=>{state.search=e.target.value;$('#library-rows').innerHTML=libraryRows();});
  const drop=$('#drop-zone');drop.onclick=()=>$('#pdf-input').click();drop.onkeydown=e=>{if(e.key==='Enter'||e.key===' ')$('#pdf-input').click();};
  drop.ondragover=e=>{e.preventDefault();drop.classList.add('dragging');};drop.ondragleave=()=>drop.classList.remove('dragging');drop.ondrop=e=>{e.preventDefault();drop.classList.remove('dragging');uploadFiles(e.dataTransfer.files);};
  renderJobs();
}
async function renderJobs() { const target=$('#jobs');if(!target)return;try{const jobs=await api('/api/jobs');if(state.view!=='library')return;target.innerHTML=jobs.length?`<div class="section-heading"><h2>最近任务 <small>后台解析与翻译</small></h2></div>`+jobs.slice(0,4).map(j=>`<div class="job"><span>${j.status==='done'?'✓':j.status==='failed'?'!':'◌'}</span><div>${esc(j.message)}<small> ${j.done}/${j.total}</small></div><progress value="${j.done}" max="${j.total||1}" aria-label="任务进度"></progress></div>`).join(''):'';}catch(e){fail(e);} }
async function uploadOne(file,keep=false) { const form=new FormData();form.append('file',file);form.append('keep',String(keep));try{return await api('/api/papers',{method:'POST',body:form});}catch(error){if(error.status!==409)throw error;return new Promise(resolve=>{modal(`<h2>发现相似文献</h2><p>${esc(file.name)}</p>${error.data.duplicates.map(d=>`<div class="notice info"><strong>${esc(d.paper.title)}</strong><br>${esc(d.reason)}</div>`).join('')}<p>原文仍然保留。你可以跳过导入，或同时保存以便对比版本。</p><div class="modal-actions"><button class="btn" id="skip-duplicate">跳过这篇</button><button class="btn primary" id="keep-duplicate">保留两个版本</button></div>`);const done=()=>resolve(null);$('#modal').addEventListener('close',done,{once:true});$('#skip-duplicate').onclick=()=>closeModal();$('#keep-duplicate').onclick=async()=>{$('#modal').removeEventListener('close',done);closeModal();try{resolve(await uploadOne(file,true));}catch(e){fail(e);resolve(null);}};});} }
async function uploadFiles(files) { if(!files.length)return;toast('正在导入文献…');let count=0;for(const file of files){try{const result=await uploadOne(file);if(result){count++;if(result.warning)toast(result.warning);}}catch(error){toast(`${file.name}：${error.message}`);}}await refresh();if(state.view==='library')renderLibrary();toast(`${count} 篇文献已加入后台解析队列`);$('#pdf-input').value=''; }
function scopeHTML(){return `<div class="panel scope-panel"><h3>文献范围 <span class="muted">${readyPapers().length} 篇</span></h3><button class="btn small" data-action="scope-all">${state.selected.size?'取消选择':'选择全部'}</button><div>${readyPapers().map(p=>`<label class="scope-item"><input type="checkbox" data-scope="${p.id}" ${state.selected.has(p.id)?'checked':''}><span>${esc(p.title)}<small>${p.version?`v${p.version} · `:''}${p.page_count} 页 · ${p.status==='partial'?'含未解析页面':'已解析'}</small></span></label>`).join('')||'<p class="muted">先在文献库导入一篇论文。</p>'}</div><p class="muted">不勾选时检索全部已解析论文。比较多篇论文时请明确勾选范围。</p></div>`;}
function renderAsk(){
  $('#main').innerHTML=heading(views[1],'选择论文后提问，点击引用查看原文。')+`<div class="split">${scopeHTML()}<div><div class="notice ${state.status.llm_configured?'info':''}" style="margin-bottom:15px">${state.status.llm_configured?'回答附原文来源，请核对引用内容。':'未配置回答模型，当前仅检索原文。'}</div><section class="panel chat-panel"><div class="chat-history" id="chat-history"></div><form class="chat-compose" id="ask-form"><label class="sr-only" for="question">研究问题</label><textarea id="question" placeholder="提一个具体问题，例如：这篇论文使用了什么优化器与学习率？" maxlength="2000" required></textarea><div class="compose-foot"><span>Enter 发送 · Shift + Enter 换行 · 回答附原文证据</span><button class="btn primary" id="send-question" type="submit">检索与回答 ↑</button></div></form></section></div></div>`;
  $('#ask-form').insertAdjacentHTML('beforebegin',`<div class="agent-options"><label><input id="agent-enabled" type="checkbox" ${state.agentEnabled?'checked':''}> 自主工具调用</label><label><input id="agent-external" type="checkbox" ${state.agentExternal?'checked':''} ${state.agentEnabled?'':'disabled'}> 允许 arXiv 发现</label><small>最多 4 轮 / 8 次调用 · 联网结果用于查找论文</small></div>`);
  $('#agent-enabled').onchange=e=>{state.agentEnabled=e.target.checked;$('#agent-external').disabled=!state.agentEnabled;};
  $('#agent-external').onchange=e=>state.agentExternal=e.target.checked;
  $('#send-question').disabled=state.askBusy;
  renderChat();$('#ask-form').onsubmit=e=>{e.preventDefault();sendQuestion();};$('#question').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#ask-form').requestSubmit();}};
}
function executionHTML(message){
  const steps=message.result?.trace||message.steps||[];
  if(!steps.length)return '';
  const symbols={running:'◌',failed:'!',rejected:'!',skipped:'↷',limited:'!'};
  return `<details class="execution-log" ${message.pending?'open':''}><summary>执行记录 · ${steps.length} 条${message.result?.execution==='agent'?` · 工具调用 · ${message.result.tool_calls} 次工具调用`:''}</summary><ol>${steps.map(t=>`<li class="execution-${esc(t.status||'done')}"><span>${t.status==='running'&&!message.pending?'✓':symbols[t.status]||'✓'}</span><div><strong>${t.role?`<span class="role-chip">${esc(t.role)}</span> `:''}${esc(t.tool)}</strong> ${esc(t.message)}${t.arguments&&Object.keys(t.arguments).length?`<code>${esc(JSON.stringify(t.arguments))}</code>`:''}</div></li>`).join('')}</ol></details>`;
}
function streamHTML(message){
  const claims=message.claims||[];
  return `<div class="stream-claims">${claims.map(c=>`<p class="stream-claim">${esc(c.text)}${c.source_ids.length?' '+c.source_ids.map(s=>`[${esc(s)}]`).join(' '):''}</p>`).join('')}${message.preview?`<p class="stream-preview">${esc(message.preview)}<span class="stream-caret"></span></p>`:''}<p class="stream-hint">${message.pending?'生成中 · 正在检查引用编号':'生成已中断，已保留引用编号检查通过的内容'}</p></div>`;
}
function renderChat(){
  const target=$('#chat-history');if(!target)return;
  const body=m=>{
    const streaming=(m.claims&&m.claims.length)||m.preview;
    if(m.pending)return streaming?streamHTML(m):loading(m.text);
    if(m.result)return esc(m.text);
    return streaming?streamHTML(m)+`<div class="error-box">${esc(m.text)}</div>`:esc(m.text);
  };
  target.innerHTML=state.chat.length?state.chat.map((m,i)=>m.role==='user'?`<div class="message user">${esc(m.text)}</div>`:`<div class="message"><div class="message-label">研舟 / ${m.pending?((m.claims&&m.claims.length)||m.preview?'正在生成并核对':'正在执行'):m.result?.mode==='clarification'?'需要澄清':m.result?.execution==='agent'?'AGENT · 证据回答':m.result?.mode==='generated'?'证据回答':'原文检索'}</div><div class="message-body">${body(m)}</div>${executionHTML(m)}${m.result?`<div class="source-grid">${m.result.sources.map((s,j)=>`<button class="source-card" data-action="source" data-message="${i}" data-source="${j}"><strong>[${esc(s.source_id)}] ${esc(s.title)} · 第 ${s.page} 页 ↗</strong><p>${esc(s.text)}</p></button>`).join('')}</div>${m.result.discoveries?.length?`<div class="agent-discoveries"><strong>可继续阅读的论文</strong><p class="muted">以下为 arXiv 元信息，未作为上方事实结论的原文证据。</p>${m.result.discoveries.map(p=>`<p><a href="https://arxiv.org/abs/${encodeURIComponent(p.arxiv_id)}" target="_blank" rel="noopener">${esc(p.title)} ↗</a><small>${esc(p.retrieval?.cache_state==='stale'?'缓存结果 · ':'')}${esc(p.retrieval?.fetched_at||'')}</small></p>`).join('')}</div>`:''}`:''}</div>`).join(''):`<div class="chat-empty"><div class="ai-icon">✳</div><h2>你的下一个问题是什么？</h2><p>选择论文，提出问题。助手可按需补查，并保留实际执行记录。</p><div class="suggestions">${['论文的训练硬件是什么？','模型使用了什么优化器？','这些论文有哪些方法差异？'].map(t=>`<button class="suggestion" data-action="suggest" data-text="${esc(t)}">${t}</button>`).join('')}</div></div>`;
  target.scrollTop=target.scrollHeight;
}
async function sendQuestion(){
  const field=$('#question'),text=field.value.trim();if(text.length<2||state.askBusy)return;
  if(!readyPapers().length){toast('请先导入并解析论文');return;}
  const scope=[...state.selected],agent=state.agentEnabled,allow_external=agent&&state.agentExternal;
  const history=state.chat.filter(m=>m.role==='user'||m.result).slice(-12).map(m=>({role:m.role,content:m.text.slice(0,4000),paper_ids:m.result?[...new Set(m.result.sources.map(s=>s.paper_id).concat(m.result.context?.paper_ids||[]))].slice(0,20):[]}));
  state.askBusy=true;field.value='';state.chat.push({role:'user',text});
  const message={role:'assistant',text:'正在检索原文…',pending:true,steps:[],claims:[],preview:''};state.chat.push(message);renderChat();$('#send-question').disabled=true;
  let reader;
  try{
    const response=await fetch('/api/ask/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:text,paper_ids:scope,agent,allow_external,history})});
    if(!response.ok){const error=await response.json().catch(()=>({}));throw new Error(error.detail||'问答请求失败');}
    reader=response.body.getReader();const decoder=new TextDecoder();let buffer='',gotResult=false;
    while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let split;
      while((split=buffer.indexOf('\n\n'))>=0){const raw=buffer.slice(0,split);buffer=buffer.slice(split+2);const event=raw.match(/event: (.*)/)?.[1];const data=JSON.parse(raw.match(/data: (.*)/)?.[1]||'{}');
        if(event==='step'){message.text=data.message;message.steps.push(data);}
        if(event==='token'){message.preview=data.text;}
        if(event==='claim'){message.claims.push(data);message.preview='';}
        if(event==='error')throw new Error(data.detail);
        if(event==='result'){message.result=data;message.text=data.answer;message.pending=false;gotResult=true;}
        renderChat();
      }
    }
    if(!gotResult)throw new Error('连接已结束，尚未收到完整回答');
  }catch(error){message.text=error.message;message.pending=false;renderChat();}
  finally{if(reader){await reader.cancel().catch(()=>{});reader.releaseLock();}state.askBusy=false;if($('#send-question'))$('#send-question').disabled=false;}
}

async function readPaper(id,page=1,bbox=null,anchor=null){if(!anchor?.preserveTrail)clearReaderNavigation();state.readerAnchor=anchor;state.reader=id;state.page=page;state.highlight=bbox;state.translated=false;if(location.hash==='#reader')await route();else location.hash='reader';}
async function renderReader(version=renderVersion){
  const available=state.papers;if(!state.reader||!available.some(p=>p.id===state.reader))state.reader=available[0]?.id;
  $('#main').innerHTML=heading(views[2],'按页阅读原文，查看译文和参考文献。');
  if(!state.reader){$('#main').innerHTML+='<div class="panel empty-state"><h3>还没有可阅读的文献</h3><p>导入后即可逐页阅读、定位证据和生成对照翻译。</p><button class="btn primary" data-action="upload">＋ 导入文献</button></div>';return;}
  $('#main').innerHTML+=loading('正在加载论文…');
  const p=await api('/api/papers/'+state.reader);if(version!==renderVersion)return;state.page=Math.max(1,Math.min(p.page_count,state.page));
  $('#main').innerHTML=heading(views[2],'按页阅读原文，查看译文和参考文献。',`<button class="btn" data-action="edit-paper">编辑信息</button><button class="btn" data-action="paper-metadata" data-id="${p.id}">解析结构</button><a class="btn" href="/api/papers/${p.id}/pdf" target="_blank" rel="noopener">原始 PDF ↗</a><button class="btn" data-action="references">参考文献</button>`)+`
    <div class="reader-controls"><label class="sr-only" for="reader-paper">选择论文</label><select class="field" id="reader-paper">${available.map(x=>`<option value="${x.id}" ${x.id===p.id?'selected':''}>${esc(x.title)}</option>`).join('')}</select><div class="btn-row"><button class="btn small" data-action="prev-page">←</button><label class="muted">第 <input class="page-number" type="number" id="page-number" value="${state.page}" min="1" max="${p.page_count}"> / ${p.page_count} 页</label><button class="btn small" data-action="next-page">→</button></div></div>
    ${p.warning?`<details class="notice" style="margin-bottom:15px"><summary>解析说明与待核对项</summary>${esc(p.warning).replace(/\n/g,'<br>')}</details>`:''}
    <div class="reader-layout"><aside class="panel toc"><h3>文章目录</h3>${(p.metadata.toc||[]).map(t=>`<button data-action="goto-page" data-page="${t.page}" style="padding-left:${Math.min(3,t.level)*7}px">${esc(t.title)}${t.estimated?' · 自动估计':''}</button>`).join('')||'<p class="muted">未提取到目录，可直接输入页码。</p>'}</aside><section><div class="reader-panes" id="reader-panes"><div class="pdf-pane" id="pdf-pane"><div class="pane-label">ORIGINAL / 原文页面</div><div class="pdf-page" id="pdf-page"><img src="/api/papers/${p.id}/pages/${state.page}/image" alt="${esc(p.title)} 第 ${state.page} 页" id="page-image"></div></div><div class="text-pane" id="text-pane"><div class="pane-label" id="text-label">TEXT / 可选择文本</div><div class="text-content" id="page-text">${loading('提取页面文本…')}</div></div></div><div class="reading-actions"><button class="btn small primary" data-action="translate-page">翻译当前页</button><button class="btn small" data-action="translate-selection">翻译选中文本</button><button class="btn small" data-action="original-text">查看原文文本</button><button class="btn small" data-action="visual-page">识别公式与图表</button><label class="muted"><input type="checkbox" id="sync-scroll" ${state.sync?'checked':''}> 按滚动比例联动</label><button class="btn small" data-action="translate-all">全文翻译</button><a class="btn small" href="/api/papers/${p.id}/translations.md">导出已有译文 ↓</a></div><p class="muted">文本中的引用编号可跳到参考文献；目录与正文阅读顺序仍需核对复杂版式。</p></section></div>`;
  $('.reader-controls').insertAdjacentHTML('afterend',readerToolsHTML(p));
  $('#reader-paper').onchange=e=>{clearReaderNavigation();state.page=1;state.highlight=null;state.reader=e.target.value;route();};$('#page-number').onchange=e=>{state.readerAnchor=null;state.page=Number(e.target.value);state.highlight=null;route();};$('#sync-scroll').onchange=e=>state.sync=e.target.checked;
  let syncing=false;for(const [from,to] of [['pdf-pane','text-pane'],['text-pane','pdf-pane']])$('#'+from).onscroll=()=>{if(!state.sync||syncing||state.restoringReader)return;syncing=true;const a=$('#'+from),b=$('#'+to),ratio=a.scrollTop/Math.max(1,a.scrollHeight-a.clientHeight);b.scrollTop=ratio*Math.max(0,b.scrollHeight-b.clientHeight);requestAnimationFrame(()=>syncing=false);};
  try {
    const [pg,refs]=await Promise.all([api(`/api/papers/${p.id}/pages/${state.page}`),loadReaderReferences(p.id).catch(()=>{toast('引用位置暂时未加载，原文仍可阅读');return []})]);
    if(version!==renderVersion)return;
    state.readerPage=pg;state.readerReferences=refs;renderOriginalText(pg);
    await positionReader(version);
  }catch(error){if($('#page-text'))fail(error,$('#page-text'));}
}
function renderOriginalText(pg){$('#page-text').innerHTML=originalPageHTML(pg);$('#text-label').textContent='TEXT / 可选择文本';state.translated=false;}

async function showVisualPage(){
  const pid=state.reader,page=state.page;
  modal(`<h2>识别第 ${page} 页公式与图表</h2><p>仅将当前页图片发送给已配置的模型服务，可能产生调用费用。返回 LaTeX、表格单元格和图注，需对照原文核对；重复查看优先复用缓存。</p><div class="modal-actions"><button class="btn" data-action="close-modal">取消</button><button class="btn primary" id="confirm-visual-page">识别当前页</button></div>`);
  $('#confirm-visual-page').onclick=async()=>{
    modal('<h2>视觉解析</h2>'+loading('正在核读当前页面…'));
    try{
      const value=await post(`/api/papers/${pid}/visual-parse`,{page});
      showVisualPageResult(value,pid);
    }catch(error){fail(error,$('#modal-content'));}
  };
}
async function showPaperMetadata(id){
  modal('<h2>文献解析结构</h2>'+loading('核对首页与结构…'));
  let p=await api('/api/papers/'+id);
  if(!(p.metadata.authors_structured||[]).length&&['ready','partial'].includes(p.status)){await api('/api/papers/'+id+'/enrich-header',{method:'POST'});p=await api('/api/papers/'+id);}
  const m=p.metadata,authors=m.authors_structured||[],tables=m.tables||[],images=m.images||[],toc=m.toc||[];
  const source=(page,bbox,label)=>`<button class="btn small" data-action="structure-source" data-id="${esc(id)}" data-page="${page}" data-bbox="${esc(JSON.stringify(bbox))}">${esc(label)} ↗</button>`;
  modal(`<h2>${esc(p.title)}</h2><p>以下结构来自 PDF 文本和版式，自动提取项请与原文核对。</p><h3>作者、机构与联系方式</h3>${authors.length?`<ol class="author-records">${authors.map(a=>`<li><strong>${esc(a.name)}</strong><p>${esc(a.affiliations.join('；')||'机构未确定')}<br>${esc(a.email||'邮箱未确定')}${a.email_derived_from_group?'（按原文分组邮箱展开）':''}</p>${source(a.page,a.bbox,'首页署名原文')}</li>`).join('')}</ol>`:'<p>未可靠提取作者与机构，可使用“编辑信息”补充并核对原文首页。</p>'}<h3>摘要</h3><p style="white-space:pre-wrap">${esc(p.abstract||'未检测到标准摘要，请核对首页。')}</p><h3>目录结构 · ${toc.length} 项</h3><details><summary>展开章节导航</summary>${toc.map(t=>`<p style="padding-left:${Math.min(3,Math.max(0,t.level-1))*14}px">${esc(t.title)} ${source(t.page,null,'第 '+t.page+' 页')}${t.estimated?' · 自动估计':''}</p>`).join('')||'<p>没有可用目录。</p>'}</details><h3>表格 · ${tables.length} 个区域</h3>${tables.slice(0,12).map((t,i)=>`<p>表格区域 ${i+1} · ${t.cells?.length||0} 行 ${source(t.page,t.bbox,'第 '+t.page+' 页')}</p>`).join('')||'<p>未检测到表格区域。</p>'}<h3>图像 · ${images.length} 个区域</h3><p>包含 PDF 内嵌图像及扫描页，不等同于已识别的学术图表数量。</p>${images.slice(0,8).map((im,i)=>`<p>图像区域 ${i+1} ${source(im.page,im.bbox,'第 '+im.page+' 页')}</p>`).join('')}<h3>公式</h3><p>原文页图已保留；在阅读页点击“识别公式与图表”可按页转录 LaTeX、表格和图注，结果需核对。</p><div class="modal-actions"><button class="btn primary" data-action="close-modal">关闭</button></div>`);
  $('#modal-content h2').insertAdjacentHTML('afterend',structureSummaryHTML(m,id));
}
async function references(number=null,origin=null){return readerReferences(number,origin);}

async function showReferenceImport(){
  if(!state.referenceImportJob)return;
  const job=await api('/api/reference-imports/'+state.referenceImportJob);
  const labels={already_in_library:'库中已有',duplicate:'重复文献，已跳过',queued_for_parse:'已下载，进入解析队列',failed:'未完成'};
  modal(`<h2>参考文献入库</h2><div id="reference-import-progress"><p>${job.status==='queued'?'排队中':job.status==='running'?'下载中':job.status==='failed'?'任务中断':'下载检查已结束'} · ${job.done}/${job.total}</p>${job.results.map(r=>`<p>条目 ${esc(r.label)}：${esc(labels[r.status]||r.status)}${r.message?' · '+esc(r.message):''}</p>`).join('')}${job.error?`<p class="error-box">${esc(job.error)}</p>`:''}<p>下载成功的文件仍需等待后台解析；可在文献库查看。</p></div><div class="modal-actions"><button class="btn primary" data-action="close-modal">关闭</button></div>`);
  if(!['queued','running'].includes(job.status)){state.referenceImportJob=null;await refresh();}
}

async function renderDiscover(version=renderVersion){
  $('#main').innerHTML=heading(views[3],'搜索 arXiv，下载论文或设置主题追踪。')+`<form class="discover-search" id="discover-form"><label class="sr-only" for="discover-query">论文检索关键词</label><input class="field" id="discover-query" value="${esc(state.discoverQuery)}" placeholder="输入论文题名、英文主题或 arXiv 编号" required minlength="2" maxlength="300"><button class="btn primary" id="discover-submit" ${state.discoverBusy?'disabled':''}>检索 arXiv ↗</button><button class="btn" type="button" data-action="watch-add">＋ 追踪主题</button></form><div class="discovery-filters"><label for="discover-sort">排序<select class="field" id="discover-sort" ${state.discoverBusy?'disabled':''}>${[['relevance','相关性优先'],['newest','最新发表'],['recommended','文献库推荐']].map(([v,t])=>`<option value="${v}" ${state.discoverSort===v?'selected':''}>${t}</option>`).join('')}</select></label><label for="discover-field">检索范围<select class="field" id="discover-field" ${state.discoverBusy?'disabled':''}><option value="all" ${state.discoverField==='all'?'selected':''}>全部字段</option><option value="title" ${state.discoverField==='title'?'selected':''}>仅搜标题</option></select></label><span>找指定论文：输入完整英文题名或 arXiv 编号；双引号可检索连续短语。</span></div><div class="notice info" style="margin-bottom:20px">默认按查询相关性排列；“文献库推荐”结合本地文献与反馈排序。主题追踪按最新发表刷新。</div><div class="results" id="discover-results" aria-live="polite"></div><section style="margin-top:28px"><div class="section-heading"><h2>我的追踪主题 <small>服务运行期间按计划刷新</small></h2></div><div id="watch-list"></div></section>`;
  $('#discover-form').onsubmit=e=>{e.preventDefault();searchDiscovery();};
  for(const id of ['discover-sort','discover-field'])$('#'+id).onchange=()=>{
    state.discoverSort=$('#discover-sort').value;state.discoverField=$('#discover-field').value;
    if(state.discoverHasSearched&&!state.discoverBusy)searchDiscovery();
  };
  if(state.discoverBusy)$('#discover-results').innerHTML=loading('正在连接 arXiv；网络波动时会自动重试，请稍候…');else renderResults();
  await renderWatches();
}
async function searchDiscovery(){
  if(state.discoverBusy)return;
  const query=($('#discover-query')?.value||state.discoverQuery).trim();
  if(query.length<2){toast('请输入至少两个字符的关键词或论文编号');return;}
  state.discoverQuery=query;state.discoverBusy=true;state.discoverHasSearched=true;state.discoverResults=[];state.discoverError=null;
  for(const id of ['discover-submit','discover-sort','discover-field'])if($('#'+id))$('#'+id).disabled=true;
  if($('#discover-results'))$('#discover-results').innerHTML=loading('正在连接 arXiv；网络波动时会自动重试，请稍候…');
  try{state.discoverResults=await api('/api/discover?'+new URLSearchParams({query,sort:state.discoverSort,field:state.discoverField}));renderResults();}
  catch(error){state.discoverError=error.message;renderResults();}
  finally{state.discoverBusy=false;for(const id of ['discover-submit','discover-sort','discover-field'])if($('#'+id))$('#'+id).disabled=false;}
}
function renderResults(){
  const target=$('#discover-results');if(!target)return;
  if(state.discoverError){const link='https://arxiv.org/search/?searchtype=all&query='+encodeURIComponent(state.discoverQuery);target.innerHTML=`<div class="error-box"><strong>arXiv 检索未完成</strong><p>${esc(state.discoverError)}</p><div class="btn-row"><button class="btn small" data-action="discover-retry">重试检索 ↻</button><a class="btn small" href="${esc(link)}" target="_blank" rel="noopener">打开 arXiv 官网 ↗</a><button class="btn small" data-action="upload">导入已下载 PDF</button></div></div>`;return;}
  if(!state.discoverResults.length){target.innerHTML=state.discoverHasSearched?`<div class="panel empty-state"><h3>没有找到匹配的论文</h3><p>可以缩短英文关键词，输入完整论文题名，或直接使用 arXiv 编号。</p></div>`:'<div class="panel empty-state"><h3>检索 arXiv 论文</h3><p>输入英文关键词，或添加一个你想持续关注的主题。</p></div>';return;}
  const meta=state.discoverResults[0].retrieval,stamp=meta?.fetched_at?new Date(meta.fetched_at).toLocaleString('zh-CN'):'';
  const notice=meta?`<div class="notice ${meta.cache_state==='stale'?'':'info'}">${meta.cache_state==='stale'?'arXiv 暂时无法连接，以下为最近成功抓取的缓存，并非本次实时结果。':'结果来源：arXiv'+(meta.cache_state==='cached'?'（缓存）':'')}${meta.sort?' · '+esc({relevance:'相关性优先',newest:'最新发表',recommended:'文献库推荐'}[meta.sort])+' · '+(meta.field==='title'?'仅搜标题':'全部字段'):''} · 抓取时间 ${esc(stamp)} · ${state.discoverResults.length} 篇</div>`:'';
  target.innerHTML=notice+state.discoverResults.map((p,i)=>`<article class="panel result-card"><div class="result-header"><div><h3><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)} ↗</a></h3><div class="result-meta">${esc(p.authors.slice(0,4).join(', '))} · ${esc(p.published.slice(0,10))} · ${esc(p.id)}</div></div><button class="btn small" data-action="import-arxiv" data-index="${i}">＋ 入库</button></div><p>${esc(p.summary)}</p><div class="result-bottom"><span class="result-reason">${esc(p.reason)}</span><div class="btn-row">${p.code_url?`<a class="btn small" href="${esc(p.code_url)}" target="_blank" rel="noopener">GitHub ↗</a>`:''}<button class="btn small ${p.feedback===1?'primary':''}" data-action="feedback" data-index="${i}" data-value="1" aria-label="推荐更多类似论文">＋ 有帮助</button><button class="btn small ${p.feedback===-1?'primary':''}" data-action="feedback" data-index="${i}" data-value="-1" aria-label="减少类似推荐">－</button></div></div></article>`).join('');
}
async function renderWatches(){const target=$('#watch-list');if(!target)return;try{const watches=await api('/api/watches');if(!target.isConnected)return;target.innerHTML=watches.map(w=>`<div class="watch-item"><div><strong>${esc(w.query)}</strong><small>每 ${w.hours} 小时 · ${w.last_run?'最近刷新 '+new Date(w.last_run).toLocaleString('zh-CN'):'等待首次刷新'}${w.last_error?' · '+esc(w.last_error):''}</small></div><div class="btn-row"><button class="btn small" data-action="watch-refresh" data-id="${w.id}">立即刷新</button><button class="btn small" data-action="watch-results" data-id="${w.id}">查看结果</button><button class="btn small ghost" data-action="watch-delete" data-id="${w.id}">删除</button></div></div>`).join('')||'<p class="muted">还没有追踪主题。停止本地服务后不会继续抓取。</p>';}catch(error){fail(error,target);}}

function graphPositions(nodes,edges){
  const points=nodes.map((n,i)=>({id:n.id,x:400+Math.cos(i*2.39996)*Math.sqrt((i+.5)/Math.max(1,nodes.length))*310,y:245+Math.sin(i*2.39996)*Math.sqrt((i+.5)/Math.max(1,nodes.length))*195}));
  const byId=new Map(points.map(p=>[p.id,p]));
  for(let step=0;step<180;step++){
    const forces=new Map(points.map(p=>[p.id,{x:(400-p.x)*.008,y:(245-p.y)*.008}]));
    for(let i=0;i<points.length;i++)for(let j=i+1;j<points.length;j++){const a=points[i],b=points[j],dx=a.x-b.x,dy=a.y-b.y,d=Math.max(1,Math.hypot(dx,dy)),push=d<68?(68-d)*.35:300/(d*d);forces.get(a.id).x+=dx/d*push;forces.get(a.id).y+=dy/d*push;forces.get(b.id).x-=dx/d*push;forces.get(b.id).y-=dy/d*push;}
    for(const edge of edges){const a=byId.get(edge.source),b=byId.get(edge.target);if(!a||!b)continue;const dx=b.x-a.x,dy=b.y-a.y,d=Math.max(1,Math.hypot(dx,dy)),pull=(d-135)*.003;forces.get(a.id).x+=dx/d*pull;forces.get(a.id).y+=dy/d*pull;forces.get(b.id).x-=dx/d*pull;forces.get(b.id).y-=dy/d*pull;}
    for(const p of points){const f=forces.get(p.id);p.x=Math.max(42,Math.min(758,p.x+Math.max(-8,Math.min(8,f.x))));p.y=Math.max(42,Math.min(445,p.y+Math.max(-8,Math.min(8,f.y))));}
  }
  return byId;
}
async function renderGraph(version=renderVersion){
  const header=()=>heading(views[4],'查看库内论文的引用关系，点击连边查看出处。',`<button class="btn" data-action="graph-refresh">↻ 重新计算</button><button class="btn primary" data-action="survey">生成文献综述 ↗</button>`);
  $('#main').innerHTML=header()+loading('正在核对本地参考文献…');
  const graph=await api('/api/graph');if(version!==renderVersion)return;state.graph=graph;state.graphScale=1;
  const count=graph.nodes.length,positions=graphPositions(graph.nodes,graph.edges),maxRank=Math.max(.001,...graph.nodes.map(n=>n.rank));
  const svg=`<svg viewBox="0 0 800 520" role="group" aria-label="本地文献引用关系图"><defs><marker id="arrow" viewBox="0 0 10 10" refX="26" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#91aa86"/></marker></defs><g id="graph-content">${graph.edges.map(e=>{const a=positions.get(e.source),b=positions.get(e.target);return `<line class="graph-edge" data-from="${esc(e.source)}" data-to="${esc(e.target)}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#91aa86" stroke-width="1.2" marker-end="url(#arrow)"/>`;}).join('')}${graph.nodes.map((n,i)=>{const pos=positions.get(n.id),r=17+n.rank/maxRank*10;return `<g class="graph-node" data-action="graph-node" data-id="${esc(n.id)}" tabindex="0" role="button" aria-label="${i+1}. ${esc(n.title)}"><title>${i+1}. ${esc(n.title)} · 被引 ${n.in_degree}</title><circle cx="${pos.x}" cy="${pos.y}" r="${r}" fill="${n.in_degree?'#769c67':'#a4b997'}" stroke="white" stroke-width="3"/><text x="${pos.x}" y="${pos.y+4}" text-anchor="middle" font-size="12" fill="white">${i+1}</text><text class="graph-node-label" x="${Math.max(130,Math.min(670,pos.x))}" y="${pos.y+r+17}" text-anchor="middle" font-size="11" fill="#284b35">${esc(n.title.length>38?n.title.slice(0,36)+'…':n.title)}</text></g>`;}).join('')}</g></svg>`;
  $('#main').innerHTML=header()+`<div class="notice info" style="margin-bottom:20px">${esc(graph.note)}</div><div class="graph-layout"><div class="panel graph-stage">${count?svg:'<div class="empty-state" style="margin-top:80px;background:transparent"><h3>暂无引用关系</h3><p>导入多篇相关论文后，根据原文 References 构建引用图谱。</p><button class="btn primary" data-action="upload">＋ 导入论文</button></div>'}<div class="graph-tools"><button class="btn small" data-action="graph-zoom" data-zoom="-0.1" aria-label="缩小图谱">−</button><button class="btn small" data-action="graph-reset">适应</button><button class="btn small" data-action="graph-zoom" data-zoom="0.1" aria-label="放大图谱">＋</button></div><div class="graph-legend">${count} 个文件节点 · ${graph.edges.length} 条匹配引用<br>悬停或聚焦查看标题，点击核对原文</div></div><aside class="panel graph-rank"><h3>文献索引 · 按局域 PageRank</h3><input id="graph-search" class="graph-search" type="search" placeholder="搜索论文标题" aria-label="搜索图谱论文"><div class="graph-index">${[...graph.nodes].sort((a,b)=>b.rank-a.rank).map(n=>`<button class="rank-item" data-action="graph-node" data-id="${esc(n.id)}"><b>${String(graph.nodes.indexOf(n)+1).padStart(2,'0')}</b><span><strong>${esc(n.title)}</strong><small>PageRank ${n.rank.toFixed(3)} · 被引 ${n.in_degree}</small></span></button>`).join('')||'<p class="muted">请先导入文献。</p>'}</div></aside></div>`;
  $('#graph-search').addEventListener('input',e=>{const q=e.target.value.trim().toLowerCase();for(const row of document.querySelectorAll('.graph-index .rank-item'))row.hidden=!row.textContent.toLowerCase().includes(q);});
  const highlight=id=>{for(const edge of document.querySelectorAll('.graph-edge')){edge.classList.toggle('related',Boolean(id)&&(edge.dataset.from===id||edge.dataset.to===id));}$('.graph-stage').classList.toggle('graph-focused',Boolean(id));};
  for(const node of document.querySelectorAll('.graph-node')){node.addEventListener('mouseenter',()=>highlight(node.dataset.id));node.addEventListener('mouseleave',()=>highlight(null));node.addEventListener('focus',()=>highlight(node.dataset.id));node.addEventListener('blur',()=>highlight(null));node.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();actions['graph-node'](node);}});}
}

function renderWriting(){const kind=state.writingKind;$('#main').innerHTML=heading(views[5],'生成论文框架、综述和架构图脚本，或用 CSV 绘图。')+`<div class="tabs">${[['outline','论文框架'],['survey','文献综述'],['mermaid','架构图脚本'],['translate','学术翻译'],['chart','CSV 数据绘图']].map(([k,n])=>`<button class="tab ${kind===k?'active':''}" data-action="writing-tab" data-kind="${k}">${n}</button>`).join('')}</div><div class="writing-grid"><section class="panel form-panel" id="writing-form-panel"></section><section class="panel output-panel" id="writing-output">${state.writingOutput?`<div class="output-content">${esc(state.writingOutput)}</div><div class="btn-row" style="margin-top:20px"><button class="btn" data-action="export-writing">导出 Markdown ↓</button></div>`:'<div class="output-placeholder">填写左侧要求后生成草稿。<br><span class="muted">结果可导出为 Markdown。</span></div>'}</section></div>`;
  if(kind==='chart'){$('#writing-form-panel').innerHTML=`<form id="chart-form"><label for="csv-file">上传 CSV 数据</label><input class="field" id="csv-file" type="file" accept=".csv,text/csv" required><label for="chart-kind">图表类型</label><select class="field" id="chart-kind"><option value="line">折线图</option><option value="bar">分组柱状图</option><option value="scatter">散点图</option><option value="radar">雷达图</option><option value="radar3d">三维雷达图</option></select><label for="chart-x">横轴 / 对象名称列</label><input class="field" id="chart-x" placeholder="留空使用第一列"><label for="chart-y">指标列名（英文逗号分隔）</label><input class="field" id="chart-y" placeholder="如 accuracy,latency；可留空"><p class="muted">最多 2 MB / 2000 行。雷达图需要 3 个以上数值列，最多 6 个对象；单位不同需先归一化。</p><button class="btn primary" type="submit" id="chart-submit" style="width:100%;margin-top:16px">生成图表与图注 ↗</button><a class="btn small" style="margin-top:12px" href="/examples/benchmark.csv" download>下载示例 CSV（模拟数据）</a></form>`;$('#chart-form').onsubmit=async e=>{e.preventDefault();const form=new FormData();form.append('file',$('#csv-file').files[0]);form.append('kind',$('#chart-kind').value);form.append('x',$('#chart-x').value);form.append('y',$('#chart-y').value);$('#chart-submit').disabled=true;$('#writing-output').innerHTML=loading('正在核对数据并绘图…');try{const result=await api('/api/charts',{method:'POST',body:form});$('#writing-output').innerHTML=`<img class="chart-preview" src="${result.image}" alt="CSV 数据图表"><div class="chart-caption">${esc(result.caption)}</div><div class="btn-row"><a class="btn small" href="${result.image}" download>PNG ↓</a><a class="btn small" href="${result.svg}" download>SVG ↓</a><a class="btn small" href="${result.script}">Python 脚本 ↓</a><a class="btn small" href="${result.csv}">原始 CSV ↓</a></div><div class="data-preview"><table><thead><tr>${result.columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${result.preview.map(r=>`<tr>${result.columns.map(c=>`<td>${esc(r[c])}</td>`).join('')}</tr>`).join('')}</tbody></table><p class="muted">前 ${result.preview.length} 行 / 共 ${result.row_count} 行。</p></div>`;}catch(error){fail(error,$('#writing-output'));}finally{if($('#chart-submit'))$('#chart-submit').disabled=false;}};return;}
  $('#writing-form-panel').innerHTML=`<form id="writing-form"><label for="writing-idea">${kind==='translate'?'待翻译的学术文本':'研究想法与输出要求'}</label><textarea class="field" id="writing-idea" placeholder="${kind==='translate'?'粘贴中文摘要或英文论文段落…':'描述研究问题、研究对象与希望输出的结构…'}" maxlength="20000" required></textarea>${kind==='translate'?'<label for="translation-target">目标语言</label><select class="field" id="translation-target"><option>英文</option><option>中文</option></select>':`<label>引用范围</label><div class="muted">${readyPapers().length} 篇已解析论文。生成时使用文献库中的真实条目；References 元信息仍需人工核对。</div>`}${kind==='survey'?'<label style="display:block;margin-top:16px"><input id="check-latest" type="checkbox"> 联网核查最近研究</label><p class="muted">最多核查 3 个方向，会向 arXiv 发送主题关键词；摘要线索不能代替全文核验。</p>':''}<button class="btn primary" type="submit" id="writing-submit" style="width:100%;margin-top:22px">${kind==='translate'?'翻译文本':'生成草稿'} ↗</button><p class="muted" style="margin-top:15px">${kind==='survey'?'Future Work 缺少后续证据时必须标记待核实；不声称已穷尽最新研究。':'请核对草稿中的引用、数据和结论。'}</p></form>`;
  $('#writing-form').onsubmit=async e=>{e.preventDefault();$('#writing-submit').disabled=true;$('#writing-output').innerHTML=loading('正在根据资料组织输出…');try{const text=$('#writing-idea').value;const result=kind==='translate'?await post('/api/translate',{text,target:$('#translation-target').value}):await post('/api/writing',{kind,idea:text,paper_ids:[...state.selected],check_latest:$('#check-latest')?.checked||false});state.writingOutput=result.content;$('#writing-output').innerHTML=`<div class="output-content">${esc(result.content)}</div><div class="btn-row" style="margin-top:20px"><button class="btn" data-action="export-writing">导出 Markdown ↓</button></div>${result.papers?`<details style="margin-top:20px"><summary class="muted">引用条目核对表</summary>${result.papers.map(p=>`<p class="muted">[P:${p.id}] ${esc(p.title)} ${p.arxiv_id?`· arXiv:${esc(p.arxiv_id)}`:''}</p>`).join('')}</details>`:''}${result.future_work&&result.future_work.items.length?`<details style="margin-top:20px" open><summary class="muted">Future Work 核验（${result.future_work.items.length} 条）</summary><div class="future-list">${result.future_work.items.map(f=>`<div class="future-item"><div class="future-head"><strong>${esc(f.verdict)}</strong><span class="muted">${esc(f.title)} · p.${f.page}</span></div><p>${esc(f.statement)}</p>${f.quote?`<p class="future-quote">“${esc(f.quote)}” — ${esc(f.ref)}</p>`:''}${f.reason?`<p class="muted">${esc(f.reason)}</p>`:''}${f.latest_search?`<div class="notice info"><strong>最近研究线索</strong>${f.latest_search.error?`<p>${esc(f.latest_search.error)}</p>`:f.latest_search.candidates.map(c=>`<p><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)} ↗</a><br>${esc(c.published.slice(0,10))} · 摘要线索，需核读全文${c.is_later?'':' · 原论文时间未确定'}</p>`).join('')||'<p>本次未返回候选，不能据此推断尚无人研究。</p>'}</div>`:''}</div>`).join('')}</div><p class="muted">${esc(result.future_work.note)}</p></details>`:''}`;}catch(error){fail(error,$('#writing-output'));}finally{if($('#writing-submit'))$('#writing-submit').disabled=false;}};
}

function embeddingConfigHTML(info={}){
  if(!info.configured)return `<p>当前使用 BM25 + TF-IDF 检索。</p>${info.cached_chunks?`<p>已保留 ${Number(info.cached_chunks).toLocaleString()} 条文献向量；接入兼容的向量服务并验证后可用于语义检索。</p>`:'<p>尚未配置向量服务。</p>'}`;
  const indexed=info.indexed_chunks||0,total=info.total_chunks||0;
  const message=info.error?'索引需要处理':info.indexing?'正在后台建立语义索引':indexed===total?'语义索引已就绪':'部分文献等待建立索引';
  return `<p><strong>${esc(info.model)}</strong> · ${info.provider==='local'?'本地运行，无需向量 API 密钥':'远程向量服务'}${info.runtime?.device?' · '+esc(info.runtime.device):''}</p><p>${message}：${indexed.toLocaleString()} / ${total.toLocaleString()} 个文本块</p><progress class="embedding-progress" value="${indexed}" max="${Math.max(1,total)}" aria-label="语义索引完成进度"></progress>${info.error?`<div class="error-box">${esc(info.error)}</div>`:''}<p class="muted">问答结合语义与关键词检索。尚未索引的文献仍可按关键词搜索。</p><div class="btn-row"><button class="btn small" data-action="embedding-refresh">刷新进度</button><button class="btn small" data-action="embedding-reindex" ${info.indexing?'disabled':''}>补建语义索引</button></div>`;
}
function rerankerConfigHTML(info={}){
  if(!info.configured)return '<p>精排未启用，使用混合召回排序。</p>';
  const runtime=info.runtime||{},label=info.error?'精排暂不可用，检索将回退':runtime.state==='ready'?'精排已就绪':runtime.state==='loading'?'正在加载精排模型':'已启用，首次检索时加载';
  return `<p><strong>${esc(info.model)}</strong>${runtime.device?' · '+esc(runtime.device):''}</p><p>${label} · 最多 ${info.candidates} 条候选</p>${runtime.last_ms!=null?`<p class="muted">最近精排 ${runtime.last_candidates} 条候选，用时 ${Number(runtime.last_ms).toLocaleString()} ms（首次包含加载时间）。</p>`:''}${info.error?`<div class="error-box">${esc(info.error)}</div>`:''}<p class="muted">先召回，再精排。原文位置与跨论文证据仍保留。</p>`;
}
function updateRerankerConfig(info){state.status.reranker=info;const panel=$('#reranker-config'),signature=JSON.stringify(info);if(panel&&panel.dataset.signature!==signature){panel.innerHTML=rerankerConfigHTML(info);panel.dataset.signature=signature;}}
function updateEmbeddingConfig(info){state.status.embedding=info;const panel=$('#embedding-config'),signature=JSON.stringify(info);if(panel&&panel.dataset.signature!==signature){panel.innerHTML=embeddingConfigHTML(info);panel.dataset.signature=signature;}}
async function refreshEmbeddingConfig(){const [embedding,ranking]=await Promise.all([api('/api/embeddings/status'),api('/api/reranker/status')]);updateEmbeddingConfig(embedding);updateRerankerConfig(ranking);}
async function showConfig(){
  state.status=await api('/api/status');
  modal(`<h2>模型与运行配置</h2><p>回答模型：${state.status.llm_configured?esc(state.status.model):'尚未启用'}。</p><h3>文献语义检索</h3><section id="embedding-config">${embeddingConfigHTML(state.status.embedding)}</section><h3 style="margin-top:24px">证据精排</h3><section id="reranker-config">${rerankerConfigHTML(state.status.reranker)}</section><details style="margin-top:24px"><summary>后端配置方法</summary><p>模型密钥只保存在后端 .env。更改配置后重启后端。</p><pre>EMBEDDING_PROVIDER=local\nEMBEDDING_MODEL=BAAI/bge-m3\nEMBEDDING_LOCAL_PATH=.local/models/bge-m3\nEMBEDDING_DEVICE=auto</pre><p>首次安装本地向量模型：安装 requirements-embedding.txt，然后运行 scripts/download_bge_m3.py。程序会自动为已有和新导入文献补建索引。</p></details><div class="modal-actions"><button class="btn primary" data-action="close-modal">知道了</button></div>`);
  $('#embedding-config').dataset.signature=JSON.stringify(state.status.embedding);
  $('#reranker-config').dataset.signature=JSON.stringify(state.status.reranker);
}

const actions={
  'reference-search':el=>{state.discoverQuery=el.dataset.query;state.discoverHasSearched=false;state.discoverResults=[];closeModal();location.hash='discover';},
  'reference-import':async()=>{const labels=$$('.reference-select:checked').map(el=>el.value);if(!labels.length||labels.length>5){toast('请选择 1–5 条有 arXiv 编号的文献');return;}const job=await post('/api/papers/'+state.referencePaper+'/references/import',{labels});state.referenceImportJob=job.job_id;await showReferenceImport();},
  'paper-metadata':el=>showPaperMetadata(el.dataset.id),
  'structure-source':el=>{const bbox=JSON.parse(el.dataset.bbox||'null');closeModal();return readPaper(el.dataset.id,Number(el.dataset.page),bbox);},
  'embedding-refresh':()=>refreshEmbeddingConfig(),
  'embedding-reindex':async()=>updateEmbeddingConfig(await api('/api/embeddings/reindex',{method:'POST'})),
  'discover-retry':()=>searchDiscovery(),
  'edit-paper':()=>{const p=state.papers.find(p=>p.id===state.reader);modal(`<h2>核对文献信息</h2><p>元数据缺失或自动识别不准确时，请以原文为准修正。</p><label class="muted" for="edit-title">论文标题</label><input class="field" id="edit-title" value="${esc(p.title)}" maxlength="300"><label class="muted" for="edit-authors">作者 / 机构说明</label><input class="field" id="edit-authors" value="${esc(p.authors)}" maxlength="1000"><div class="modal-actions"><button class="btn" data-action="close-modal">取消</button><button class="btn primary" id="save-paper">保存</button></div>`);$('#save-paper').onclick=async()=>{try{await api('/api/papers/'+p.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:$('#edit-title').value,authors:$('#edit-authors').value})});closeModal();await refresh();route();toast('文献信息已更新');}catch(e){fail(e);}};},
  upload:()=>$('#pdf-input').click(),
  filter:el=>{state.filter=el.dataset.filter;renderLibrary();},
  read:el=>readPaper(el.dataset.id),
  delete:el=>{const p=state.papers.find(p=>p.id===el.dataset.id);modal(`<h2>删除这篇文献？</h2><p>${esc(p.title)}<br>将删除当前环境中的文件、索引和译文。</p><div class="modal-actions"><button class="btn" data-action="close-modal">取消</button><button class="btn danger" id="confirm-delete">删除文献</button></div>`);$('#confirm-delete').onclick=async()=>{try{await api('/api/papers/'+p.id,{method:'DELETE'});closeModal();await refresh();route();toast('已删除文献');}catch(e){fail(e);}};},
  'close-modal':closeModal,
  'scope-all':()=>{state.selected=state.selected.size?new Set():new Set(readyPapers().map(p=>p.id));renderAsk();},
  suggest:el=>{$('#question').value=el.dataset.text;$('#question').focus();},
  source:el=>{const s=state.chat[Number(el.dataset.message)].result.sources[Number(el.dataset.source)];return readPaper(s.paper_id,s.page,s.bbox);},
  'prev-page':()=>{if(state.page>1){state.readerAnchor=null;state.page--;state.highlight=null;return route();}},
  'next-page':()=>{const p=state.papers.find(p=>p.id===state.reader);if(state.page<p.page_count){state.readerAnchor=null;state.page++;state.highlight=null;return route();}},
  'goto-page':el=>{state.readerAnchor=null;state.page=Number(el.dataset.page);state.highlight=null;return route();},
  'visual-page':()=>showVisualPage(),
  'refresh-structure':el=>refreshReaderStructure(el.dataset.id||state.reader),
  'visual-batch':el=>showVisualBatch(el.dataset.id||state.reader),
  'visual-results':el=>showVisualResults(el.dataset.id||state.reader),
  'cached-visual':el=>showCachedVisual(Number(el.dataset.page),el.dataset.id||state.reader),
  'reference-source':el=>visitReference(state.referenceEntries[Number(el.dataset.index)]),
  'citation-occurrence':el=>visitCitation(state.referenceEntries[Number(el.dataset.index)].occurrences[Number(el.dataset.occurrence)]),
  'reference-back':()=>returnReaderReference(),
  'translate-page':async el=>{el.disabled=true;try{const result=await post(`/api/papers/${state.reader}/translate`,{page:state.page});$('#page-text').textContent=result.content;$('#text-label').textContent='TRANSLATION / 中文译文';state.translated=true;}finally{if(el.isConnected)el.disabled=false;}},
  'original-text':()=>state.readerPage&&renderOriginalText(state.readerPage),
  'translate-selection':async()=>{const text=window.getSelection().toString().trim();if(text.length<2){toast('请先在右侧原文文本中选中一段内容');return;}modal('<h2>选段翻译</h2>'+loading('翻译所选文本…'));try{const result=await post('/api/translate',{text:text.slice(0,20000),target:'中文'});modal(`<h2>选段翻译</h2><div class="output-content">${esc(result.content)}</div><div class="modal-actions"><button class="btn" data-action="close-modal">关闭</button></div>`);}catch(e){fail(e,$('#modal-content'));}},
  'translate-all':()=>{const p=state.papers.find(p=>p.id===state.reader);modal(`<h2>逐页翻译全文</h2><p>将翻译 ${p.page_count} 页；模型调用可能产生费用。已缓存页面会复用，任务在后端逐页执行。</p><div class="modal-actions"><button class="btn" data-action="close-modal">取消</button><button class="btn primary" id="confirm-translate-all">开始全文翻译</button></div>`);$('#confirm-translate-all').onclick=async()=>{try{await post(`/api/papers/${state.reader}/translate-all`,{});closeModal();toast('全文翻译已加入后台任务，可在文献库查看进度');}catch(e){fail(e);}};},
  references:el=>references(null,el.closest('#modal-content')?state.referenceOrigin:null), 'reference-number':el=>references(el.dataset.labels?JSON.parse(el.dataset.labels):el.dataset.number,captureReaderPosition(el.id)),
  diff:()=>{if(readyPapers().length<2){toast('请先导入至少两个已解析的论文版本');return;}const options=readyPapers().map(p=>`<option value="${p.id}">${esc(p.title)}${p.version?' v'+p.version:''}</option>`).join('');modal(`<h2>论文版本对比</h2><p>比较两个 PDF 的已提取正文，保留差异原文供核对。</p><div class="diff-grid"><select class="field" id="diff-left">${options}</select><select class="field" id="diff-right">${options}</select></div><div class="modal-actions"><button class="btn" data-action="close-modal">取消</button><button class="btn primary" id="run-diff">开始对比</button></div><div id="diff-output"></div>`);$('#diff-right').selectedIndex=1;$('#run-diff').onclick=async()=>{const left=$('#diff-left').value,right=$('#diff-right').value;if(left===right){toast('请选择两个不同版本');return;}$('#diff-output').innerHTML=loading('正在比较正文…');try{const result=await post('/api/diff',{left,right});$('#diff-output').innerHTML=`<p>正文行匹配相似度 ${(result.similarity*100).toFixed(1)}% · ${result.change_count} 处差异${result.truncated?'（仅显示前 80 处）':''}</p>${result.changes.map(c=>`<p class="muted">${esc(c.kind)}</p><div class="diff-grid"><div class="diff-block">${esc(c.before)||'（无）'}</div><div class="diff-block">${esc(c.after)||'（无）'}</div></div>`).join('')}`;}catch(e){fail(e,$('#diff-output'));}};},
  'import-arxiv':async el=>{el.disabled=true;try{const p=state.discoverResults[Number(el.dataset.index)];await post('/api/discover/import',{arxiv_id:p.id});await refresh();toast('论文已加入解析队列');}catch(e){if(e.status===409)toast('文献库已有相似论文，请在文献库核对版本');else throw e;}finally{if(el.isConnected)el.disabled=false;}},
  feedback:async el=>{const p=state.discoverResults[Number(el.dataset.index)];p.feedback=p.feedback===Number(el.dataset.value)?0:Number(el.dataset.value);await post('/api/feedback',{arxiv_id:p.id,value:p.feedback});renderResults();},
  'watch-add':()=>{modal(`<h2>追踪一个研究主题</h2><p>定期检索 arXiv。仅在本地服务运行期间执行，不会自动下载全部 PDF。</p><label class="muted" for="watch-query">英文主题关键词</label><input class="field" id="watch-query" placeholder="例如 efficient attention"><label class="muted" for="watch-hours">刷新间隔（小时，至少 1）</label><input class="field" id="watch-hours" type="number" min="1" max="720" value="24"><div class="modal-actions"><button class="btn" data-action="close-modal">取消</button><button class="btn primary" id="save-watch">添加追踪</button></div>`);$('#save-watch').onclick=async()=>{try{await post('/api/watches',{query:$('#watch-query').value,hours:Number($('#watch-hours').value)});closeModal();renderWatches();toast('主题已添加，服务将在下次检查时刷新');}catch(e){fail(e);}};},
  'watch-refresh':async el=>{el.disabled=true;try{const w=await post('/api/watches/'+el.dataset.id+'/refresh',{});await renderWatches();toast(w.last_error||'主题已刷新');}finally{if(el.isConnected)el.disabled=false;}},
  'watch-results':async el=>{if(state.discoverBusy){toast('请等待当前检索完成');return;}const all=await api('/api/watches'),watch=all.find(w=>w.id===el.dataset.id);state.discoverResults=JSON.parse(watch.results);state.discoverError=null;state.discoverHasSearched=true;state.discoverQuery=watch.query;state.discoverSort='newest';state.discoverField='all';if($('#discover-sort'))$('#discover-sort').value='newest';if($('#discover-field'))$('#discover-field').value='all';if($('#discover-query'))$('#discover-query').value=watch.query;renderResults();},
  'watch-delete':async el=>{await api('/api/watches/'+el.dataset.id,{method:'DELETE'});renderWatches();},
  'graph-refresh':()=>route(),
  'graph-zoom':el=>{state.graphScale=Math.min(1.8,Math.max(.6,state.graphScale+Number(el.dataset.zoom)));const g=$('#graph-content');if(g)g.setAttribute('transform',`translate(${400*(1-state.graphScale)} ${260*(1-state.graphScale)}) scale(${state.graphScale})`);},
  'graph-reset':()=>{state.graphScale=1;$('#graph-content')?.removeAttribute('transform');},
  'graph-node':el=>{const n=state.graph.nodes.find(n=>n.id===el.dataset.id),edges=state.graph.edges.filter(e=>e.source===n.id||e.target===n.id);modal(`<h2>${esc(n.title)}</h2><p>局域 PageRank ${n.rank} · 被引 ${n.in_degree} · 引出 ${n.out_degree}</p>${edges.map(e=>`<div class="notice info" style="margin-bottom:10px">${esc(state.graph.nodes.find(n=>n.id===e.source).title)} → ${esc(state.graph.nodes.find(n=>n.id===e.target).title)}<br>匹配方式：References 中规范化题名匹配<br><button class="btn small" data-action="edge-source" data-id="${e.source}" data-page="${e.page}">核对第 ${e.page} 页 ↗</button></div>`).join('')||'<p>未匹配到库内引用，可查看原文参考文献。</p>'}<div class="modal-actions"><button class="btn" data-action="close-modal">关闭</button><button class="btn primary" data-action="node-read" data-id="${n.id}">阅读论文 ↗</button></div>`);},
  'node-read':el=>{closeModal();return readPaper(el.dataset.id);},
  'edge-source':el=>{closeModal();return readPaper(el.dataset.id,Number(el.dataset.page));},
  survey:()=>{state.writingKind='survey';state.writingOutput='';location.hash='writing';},
  'writing-tab':el=>{state.writingKind=el.dataset.kind;state.writingOutput='';renderWriting();},
  'export-writing':()=>download(state.writingOutput,'researchdock-'+state.writingKind+'.md'),
};
document.addEventListener('click',async event=>{const el=event.target.closest('[data-action]');if(!el)return;const action=actions[el.dataset.action];if(action){try{await action(el);}catch(error){fail(error);}}});
document.addEventListener('change',event=>{if(event.target.dataset.scope){if(event.target.checked)state.selected.add(event.target.dataset.scope);else state.selected.delete(event.target.dataset.scope);}});
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();if(state.view!=='library')location.hash='library';setTimeout(()=>$('#library-search')?.focus(),50);}if(e.altKey&&e.key==='ArrowLeft'&&state.view==='reader'&&readerBackStack.length){e.preventDefault();returnReaderReference();}});
$('#settings').onclick=()=>showConfig().catch(error=>fail(error));
$('#help').onclick=()=>modal(`<h2>使用说明</h2><p>① 在文献库导入 PDF，等待后台解析。<br>② 在证据问答中选择论文并提问，点击出处返回原文。<br>③ 对照阅读支持逐页翻译与参考文献定位。<br>④ 从 arXiv 发现文献，在引用图谱中核对连接。<br>⑤ 用文献生成提纲、综述，或上传 CSV 绘图。</p><div class="notice">公式与图表可在阅读页按页识别，结果需核对；双语阅读目前按滚动比例联动。可在侧边栏查看“模型与运行配置”。</div><div class="modal-actions"><button class="btn primary" data-action="close-modal">关闭</button></div>`);
$('#mobile-menu').onclick=()=>$('.sidebar').classList.toggle('open');
$('#pdf-input').onchange=e=>uploadFiles(e.target.files);
$('#modal').addEventListener('click',e=>{if(e.target===$('#modal'))closeModal();});
window.addEventListener('hashchange',route);
function renderLogin(){
  const next=location.pathname+location.search+location.hash;
  location.replace('/login?next='+encodeURIComponent(next));
}

async function init(){try{state.auth=await api('/api/auth/status');if(state.auth.enabled&&!state.auth.authenticated){renderLogin();return;}if(state.auth.enabled&&!$('#logout-button')){const button=document.createElement('button');button.id='logout-button';button.className='btn small';button.textContent='退出';button.onclick=async()=>{await post('/api/auth/logout',{});state.chat=[];state.papers=[];state.selected.clear();state.auth.authenticated=false;button.remove();renderLogin();};$('.top-actions').append(button);}await refresh();await route();}catch(error){fail(error,$('#main'));}}
init();
setInterval(async()=>{if(document.hidden)return;try{if($('#modal').open&&$('#embedding-config'))await refreshEmbeddingConfig();if($('#modal').open&&$('#reference-import-progress')&&state.referenceImportJob)await showReferenceImport();const wasProcessing=state.papers.some(p=>p.status==='processing');if(wasProcessing||state.view==='library'){await refresh();if(state.view==='library'){if($('#stats'))$('#stats').innerHTML=stats();if($('#paper-count'))$('#paper-count').textContent=state.papers.length+' 篇论文';if($('#library-rows'))$('#library-rows').innerHTML=libraryRows();await renderJobs();}}}catch{}},3500);
