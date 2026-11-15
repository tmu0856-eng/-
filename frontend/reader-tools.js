/* 阅读位置、引用锚点与结构识别工具；所有文献内容均按文本转义。 */
const readerReferenceCache = new Map();
const readerBackStack = [];

function clearReaderNavigation() {
  readerBackStack.length=0;
  state.readerAnchor=null;
}

async function loadReaderReferences(pid) {
  if(!readerReferenceCache.has(pid)) {
    if(readerReferenceCache.size>=6)readerReferenceCache.delete(readerReferenceCache.keys().next().value);
    readerReferenceCache.set(pid,api(`/api/papers/${pid}/references`).catch(error=>{readerReferenceCache.delete(pid);throw error;}));
  }
  return readerReferenceCache.get(pid);
}

function captureReaderPosition(focusId='') {
  return {pid:state.reader,page:state.page,highlight:state.highlight,focusId,
    windowY:window.scrollY,pdfScroll:$('#pdf-pane')?.scrollTop||0,textScroll:$('#text-pane')?.scrollTop||0,
    tocScroll:$('.toc')?.scrollTop||0,translated:state.translated,
    translation:state.translated?$('#page-text')?.textContent:null};
}

function readerToolsHTML(p) {
  const coverage=p.metadata.structure_coverage;
  return `<div class="reader-tools"><button class="btn small" data-action="refresh-structure">更新结构清单</button><button class="btn small" data-action="visual-batch">批量识别公式与图表</button><button class="btn small" data-action="visual-results">查看已识别结果</button>${readerBackStack.length?'<button class="btn small primary" data-action="reference-back">← 返回原引用位置</button>':''}<span class="muted">${coverage?`章节 ${coverage.heading_count} · 附录 ${coverage.appendix_count} · 公式候选 ${coverage.formula_candidates}`:'旧文献可更新结构清单，无需重新导入。'}</span></div>`;
}

function originalPageHTML(pg) {
  const markers=new Map();
  for(const ref of state.readerReferences||[])for(const item of ref.occurrences||[])if(item.page===pg.page)markers.set(item.id,item);
  return pg.blocks.map((block,bi)=>{
    const anchor=state.readerAnchor;
    if(anchor?.kind==='reference'&&anchor.block_index===bi&&anchor.start!=null) {
      const start=Math.max(0,anchor.start),end=Math.min(block.text.length,anchor.end??block.text.length);
      return `<div class="text-block" id="text-block-${bi}">${esc(block.text.slice(0,start))}<mark id="reader-reference-target">${esc(block.text.slice(start,end))}</mark>${esc(block.text.slice(end))}</div>`;
    }
    let html='',offset=0;
    const items=[...markers.values()].filter(item=>item.block_index===bi).sort((a,b)=>a.start-b.start);
    for(const item of items) {
      if(item.start<offset||item.end>block.text.length)continue;
      html+=esc(block.text.slice(offset,item.start))+`<button class="ref-button" id="${esc(item.id)}" data-action="reference-number" data-labels="${esc(JSON.stringify(item.labels))}">${esc(block.text.slice(item.start,item.end))}</button>`;
      offset=item.end;
    }
    return `<div class="text-block" id="text-block-${bi}">${html+esc(block.text.slice(offset))}</div>`;
  }).join('')||'此页没有提取到文本，请核对原文页图。';
}

async function positionReader(version) {
  const img=$('#page-image');
  if(img)await img.decode().catch(()=>{});
  if(version!==renderVersion)return;
  const anchor=state.readerAnchor,snapshot=anchor?.snapshot;
  if(state.highlight) {
    const pg=state.readerPage,[x0,y0,x1,y1]=state.highlight;
    $('#pdf-page').insertAdjacentHTML('beforeend',`<div class="anchor-box" style="left:${x0/pg.width*100}%;top:${y0/pg.height*100}%;width:${(x1-x0)/pg.width*100}%;height:${(y1-y0)/pg.height*100}%"></div>`);
  }
  state.restoringReader=true;
  if(snapshot) {
    if(snapshot.translated&&snapshot.translation!=null) {
      $('#page-text').textContent=snapshot.translation;
      $('#text-label').textContent='TRANSLATION / 中文译文';state.translated=true;
    }
    $('#pdf-pane').scrollTop=snapshot.pdfScroll;$('#text-pane').scrollTop=snapshot.textScroll;
    $('.toc').scrollTop=snapshot.tocScroll;
    window.scrollTo({top:snapshot.windowY,behavior:'instant'});
    const focus=snapshot.focusId&&document.getElementById(snapshot.focusId);
    if(focus){focus.classList.add('citation-active');focus.focus({preventScroll:true});}
  } else {
    const box=$('.anchor-box'),pdf=$('#pdf-pane');
    if(box)pdf.scrollTop+=box.getBoundingClientRect().top-pdf.getBoundingClientRect().top-100;
    const target=anchor?.kind==='reference'?$('#reader-reference-target'):anchor?.id?document.getElementById(anchor.id):null;
    if(target) {
      const text=$('#text-pane');text.scrollTop+=target.getBoundingClientRect().top-text.getBoundingClientRect().top-text.clientHeight*.35;
      target.classList.add('citation-active');if(target.tagName==='BUTTON')target.focus({preventScroll:true});
    }
  }
  await new Promise(requestAnimationFrame);
  state.restoringReader=false;
}

async function readerReferences(numbers=null,origin=null) {
  const pid=state.reader;
  state.referencePaper=pid;state.referenceOrigin=origin||captureReaderPosition();
  modal('<h2>参考文献</h2>'+loading('定位原文引用与条目…'));
  const refs=await loadReaderReferences(pid);state.referenceEntries=refs;
  const wanted=numbers?(Array.isArray(numbers)?numbers:[String(numbers)]):null;
  const selected=refs.map((r,index)=>({r,index})).filter(({r})=>!wanted||wanted.includes(r.label));
  modal(`<h2>${wanted?'引用 ['+esc(wanted.join(', '))+']':'参考文献'}</h2><p>可定位参考条目原文，也可跳到正文中具体的引用位置。批量下载最多选择 5 篇。</p><div class="reference-list">${selected.map(({r,index})=>`<section class="reference-entry"><label><input class="reference-select" type="checkbox" value="${esc(r.label)}" ${r.can_import?'':'disabled'}> <strong>${esc(r.label||'无编号条目')}</strong></label><p>${esc(r.text)}</p>${r.ambiguous_label?'<p class="notice">文献中存在重复编号；以下位置为候选匹配，请结合所属章节核对。</p>':''}<div class="btn-row"><button class="btn small" data-action="reference-source" data-index="${index}">查看第 ${r.page} 页原文 ↗</button>${r.local_paper_id?`<button class="btn small" data-action="node-read" data-id="${esc(r.local_paper_id)}">库中已有 · 阅读 ↗</button>`:r.url?`<a class="btn small" href="${esc(r.url)}" target="_blank" rel="noopener">arXiv 原文 ↗</a>`:`<button class="btn small" data-action="reference-search" data-query="${esc(r.text.slice(0,280))}">检索条目 ↗</button>`}</div>${r.occurrences?.length?`<details class="citation-occurrences"><summary>正文引用位置 · ${r.occurrences.length}${r.occurrences_limited?'（已限制显示数量）':''} 处</summary>${r.occurrences.map((item,oi)=>`<button class="citation-location" data-action="citation-occurrence" data-index="${index}" data-occurrence="${oi}"><strong>第 ${item.page} 页 ${esc(item.marker)}</strong><span>${esc(item.excerpt)}</span></button>`).join('')}</details>`:'<p class="muted">未定位到可可靠匹配的数字引用，可能使用作者年份制或未识别的编号格式。</p>'}</section>`).join('')||'<p>没有可靠匹配的参考条目，请核对原文编号。</p>'}</div><div class="modal-actions">${wanted?'<button class="btn" data-action="references">查看全部条目</button>':''}<button class="btn" data-action="close-modal">返回阅读</button><button class="btn primary" data-action="reference-import">下载所选参考文献</button></div>`);
}

async function visitReference(entry) {
  if(!entry)return;
  readerBackStack.push(state.referenceOrigin||captureReaderPosition());
  if(readerBackStack.length>20)readerBackStack.shift();
  const pid=state.referencePaper;closeModal();
  await readPaper(pid,entry.page,entry.source?.bbox,{...entry.source,kind:'reference',preserveTrail:true});
}

async function visitCitation(item) {
  if(!item)return;
  const original=readerBackStack.at(-1);
  if(original?.pid===state.referencePaper&&original.focusId===item.id){closeModal();return returnReaderReference();}
  readerBackStack.push(state.referenceOrigin||captureReaderPosition());
  if(readerBackStack.length>20)readerBackStack.shift();
  const pid=state.referencePaper;closeModal();
  await readPaper(pid,item.page,item.bbox,{...item,kind:'citation',preserveTrail:true});
}

async function returnReaderReference() {
  const snapshot=readerBackStack.pop();if(!snapshot)return;
  await readPaper(snapshot.pid,snapshot.page,snapshot.highlight,{snapshot,preserveTrail:true});
}

function structureSummaryHTML(meta,pid=state.reader) {
  const coverage=meta.structure_coverage;
  return `<section class="structure-summary"><h3>全文结构覆盖</h3><p>${coverage?`已检查 ${coverage.pages} 页 · 作者 ${coverage.author_count} 位 · 章节 ${coverage.heading_count} 项 · 附录 ${coverage.appendix_count} 项 · 公式候选 ${coverage.formula_candidates} 处`:'当前记录尚未建立完整结构清单。'}</p><div class="btn-row"><button class="btn small" data-action="refresh-structure" data-id="${esc(pid)}">更新结构清单</button><button class="btn small" data-action="visual-batch" data-id="${esc(pid)}">批量识别公式与图表</button><button class="btn small" data-action="visual-results" data-id="${esc(pid)}">查看识别结果</button></div><details><summary>附录结构 · ${(meta.appendices||[]).length} 项</summary>${(meta.appendices||[]).map(item=>`<p>${esc(item.title)} <button class="btn small" data-action="structure-source" data-id="${esc(pid)}" data-page="${item.page}" data-bbox="${esc(JSON.stringify(item.bbox||null))}">第 ${item.page} 页 ↗</button></p>`).join('')||'<p>未可靠识别独立附录标题，请核对原文。</p>'}</details><details><summary>公式候选 · ${(meta.formulas||[]).length} 处</summary><p class="muted">候选保留原始文本和位置，不等于已还原 LaTeX；识别结果可从上方入口查看。</p>${(meta.formulas||[]).slice(0,60).map(item=>`<p><button class="btn small" data-action="structure-source" data-id="${esc(pid)}" data-page="${item.page}" data-bbox="${esc(JSON.stringify(item.bbox))}">第 ${item.page} 页 ↗</button> ${esc(item.text.slice(0,220))}</p>`).join('')||'<p>未发现可靠公式候选，可在阅读页指定当前页识别。</p>'}</details></section>`;
}

async function refreshReaderStructure(pid=state.reader) {
  const job=await post(`/api/papers/${pid}/structure-refresh`,{});
  modal('<h2>更新结构清单</h2><div id="structure-progress">'+loading('正在逐页检查章节、附录和公式候选…')+'</div><div class="modal-actions"><button class="btn" data-action="close-modal">后台继续</button></div>');
  for(let i=0;i<180;i++) {
    const task=(await api('/api/jobs')).find(j=>j.id===job.job_id);
    if(!task)throw new Error('暂未找到结构任务，请在文献库查看任务状态。');
    if($('#structure-progress'))$('#structure-progress').textContent=`${task.message} · ${task.done}/${task.total}`;
    if(task.status==='failed')throw new Error(task.message);
    if(task.status==='done') {
      readerReferenceCache.delete(pid);toast('结构清单已更新');
      if(state.reader===pid&&state.view==='reader'){closeModal();await route();await showPaperMetadata(pid);}
      return;
    }
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
  toast('结构任务仍在后台运行，可在文献库查看进度。');
}

async function showVisualBatch(pid=state.reader) {
  if(state.visualBatchJob) {
    const active=await api('/api/visual-jobs/'+state.visualBatchJob);
    if(active.paper_id===pid&&['queued','running'].includes(active.status))return showVisualBatchProgress(active.id,pid);
  }
  const progress=await api(`/api/papers/${pid}/visual-status`);
  const pages=progress.pending_pages.slice(0,progress.batch_limit);
  modal(`<h2>批量识别公式与图表</h2><p>全文候选页 ${progress.candidate_pages.length} 页，已缓存 ${progress.cached_pages.length} 页。每批最多 ${progress.batch_limit} 页，成功页面自动跳过。</p><p>${pages.length?'本次识别页码：'+pages.join('、'):'没有待识别的候选页，可先更新结构清单，或使用阅读页的单页识别。'}</p><p class="notice">会将这些页的图片发送至配置的模型服务并产生调用费用。结果需核对原图，不自动作为问答证据。</p><div class="modal-actions"><button class="btn" data-action="close-modal">取消</button><button class="btn primary" id="confirm-visual-batch" ${pages.length?'':'disabled'}>开始识别 ${pages.length} 页</button></div>`);
  $('#confirm-visual-batch').onclick=async()=>{try{const job=await post(`/api/papers/${pid}/visual-batch`,{pages});state.visualBatchJob=job.job_id;await showVisualBatchProgress(job.job_id,pid);}catch(error){fail(error,$('#modal-content'));}};
}

async function showVisualBatchProgress(jid,pid=state.reader) {
  modal(`<h2>批量识别进度</h2><div id="visual-batch-progress"></div><div class="modal-actions"><button class="btn" data-action="close-modal">后台继续</button><button class="btn" data-action="visual-results" data-id="${esc(pid)}">查看已识别结果</button><button class="btn" id="cancel-visual-batch">取消后续页面</button></div>`);
  $('#cancel-visual-batch').onclick=async()=>{await post(`/api/visual-jobs/${jid}/cancel`,{});toast('已请求取消后续页面，当前请求完成后停止。');};
  const poll=async()=>{
    if(!$('#visual-batch-progress'))return;
    try {
      const job=await api('/api/visual-jobs/'+jid);
      const labels={queued:'排队中',running:'正在识别',done:'已完成',partial:'部分页面失败，可重试',failed:'识别未完成',cancelled:'已取消'};
      if(!$('#visual-batch-progress'))return;
      $('#visual-batch-progress').innerHTML=`<p>${esc(labels[job.status]||job.status)} · ${job.done}/${job.total}</p>${job.error?`<p class="error-box">${esc(job.error)}</p>`:''}${job.results.map(item=>`<p>第 ${item.page} 页：${item.status==='done'?`公式 ${item.formulas}、表格 ${item.tables}${item.cached?' · 缓存':''}`:esc(item.error)}</p>`).join('')}`;
      if(['queued','running'].includes(job.status))setTimeout(poll,1800);
      else $('#cancel-visual-batch').disabled=true;
    }catch(error){fail(error,$('#visual-batch-progress'));}
  };
  await poll();
}

async function showVisualResults(pid=state.reader) {
  const progress=await api(`/api/papers/${pid}/visual-status`);
  modal(`<h2>已识别的公式与图表</h2><p>${esc(progress.note)}</p><p>缓存页 ${progress.cached_pages.length} 页，公式 ${progress.formula_count} 条。</p><div class="btn-row">${progress.cached_pages.map(page=>`<button class="btn small" data-action="cached-visual" data-id="${esc(pid)}" data-page="${page}">第 ${page} 页</button>`).join('')||'<p>尚无识别结果。</p>'}</div><div class="modal-actions"><button class="btn" data-action="visual-batch" data-id="${esc(pid)}">继续批量识别</button><button class="btn" data-action="close-modal">关闭</button></div>`);
}

async function showCachedVisual(page,pid=state.reader) {
  const value=await api(`/api/papers/${pid}/visual-cache/${page}`);
  modal(`<h2>第 ${page} 页识别结果</h2><p class="notice">${esc(value.note)}</p><h3>公式 · ${value.formulas.length}</h3>${value.formulas.map(item=>`<p>${esc(item.label)}</p><pre class="visual-latex">${esc(item.latex)}</pre><p class="muted">${esc(item.uncertainty)}</p>`).join('')||'<p>当前页未返回公式。</p>'}<h3>表格 · ${value.tables.length}</h3>${value.tables.map(item=>`<p>${esc(item.caption)}</p><table>${item.rows.map(row=>`<tr>${row.map(cell=>`<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</table>`).join('')}<h3>图与图注 · ${value.figures.length}</h3>${value.figures.map(item=>`<p>${esc(item.caption)}<br>${esc(item.description)}</p>`).join('')}<div class="modal-actions"><button class="btn" data-action="visual-results" data-id="${esc(pid)}">返回结果列表</button><button class="btn primary" id="cached-visual-source">核对原图</button><button class="btn" data-action="close-modal">关闭</button></div>`);
  $('#cached-visual-source').onclick=async()=>{closeModal();await readPaper(pid,page);};
}
