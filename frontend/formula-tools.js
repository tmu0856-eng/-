/* 公式排版、原图对照与人工修正；模型转录和修改结果分别保留。 */
function renderFormulaMath(latex,target) {
  target.replaceChildren();
  try {
    if(!window.katex)throw new Error('排版组件未加载');
    let expression=latex.trim();
    if(expression.startsWith('$$')&&expression.endsWith('$$'))expression=expression.slice(2,-2);
    else if((expression.startsWith('\\[')&&expression.endsWith('\\]'))||(expression.startsWith('\\(')&&expression.endsWith('\\)')))expression=expression.slice(2,-2);
    if(!expression.trim())throw new Error('公式为空');
    window.katex.render(expression,target,{displayMode:true,throwOnError:true,trust:false,
      strict:'warn',maxSize:15,maxExpand:300,macros:{},output:'htmlAndMathml'});
    target.classList.remove('formula-invalid');
    return true;
  }catch(error) {
    target.classList.add('formula-invalid');
    target.textContent='暂时无法排版，请检查 LaTeX。原始代码和页面图片仍可查看。';
    return false;
  }
}

function showVisualPageResult(value,pid,openIndex=null,scrolls=null) {
  const page=value.page;
  modal(`<h2>第 ${page} 页公式与图表</h2><p class="notice">${esc(value.note)}</p><div class="formula-workspace"><section class="formula-source-pane"><h3>原始 PDF 页面</h3><a href="/api/papers/${encodeURIComponent(pid)}/pages/${page}/image" target="_blank" rel="noopener">打开大图 ↗</a><img class="formula-source-image" src="/api/papers/${encodeURIComponent(pid)}/pages/${page}/image" alt="原始 PDF 第 ${page} 页，供公式核对"></section><section class="formula-content-pane"><h3>公式 · ${value.formulas.length}</h3>${value.formulas.map((item,index)=>`<article class="formula-card" data-formula-index="${index}"><div class="formula-card-heading"><strong>${esc(item.label||'公式 '+(index+1))}</strong><span class="muted">${item.edited?'已手动修改':'模型转录'}</span></div><div class="formula-preview" data-preview="${index}" aria-label="公式排版"></div>${item.uncertainty?`<p class="muted">转录提示：${esc(item.uncertainty)}</p>`:''}<details ${openIndex===index?'open':''}><summary>编辑 LaTeX 与编号</summary><form data-formula-form="${index}"><label>公式编号<input class="field" name="label" maxlength="120" value="${esc(item.label||'')}" aria-label="公式 ${index+1} 编号"></label><label>LaTeX<textarea class="field formula-input" name="latex" rows="4" maxlength="8000" required spellcheck="false" aria-label="公式 ${index+1} LaTeX">${esc(item.latex)}</textarea></label><div class="btn-row"><button class="btn small primary" type="submit">保存修改</button><button class="btn small" type="button" data-restore-formula="${index}">恢复模型转录</button></div><p class="formula-save-status muted" role="status">修改时实时预览，保存后再次打开仍可查看；不会重新调用模型。</p></form></details><details><summary>查看模型原始转录</summary><pre class="visual-latex">${esc(item.original_latex??item.latex)}</pre></details></article>`).join('')||'<p>当前页尚无公式转录。</p>'}<h3>表格 · ${value.tables.length}</h3>${value.tables.map(item=>`<p>${esc(item.caption)}</p><div class="visual-table-scroll"><table><tbody>${item.rows.map(row=>`<tr>${row.map(cell=>`<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`).join('')||'<p>当前页未返回表格。</p>'}<h3>图与图注 · ${value.figures.length}</h3>${value.figures.map(item=>`<p><strong>${esc(item.caption)}</strong><br>${esc(item.description)}</p>`).join('')||'<p>当前页未返回图注。</p>'}</section></div><div class="modal-actions"><button class="btn" data-action="visual-results" data-id="${esc(pid)}">已识别结果</button><button class="btn" id="formula-reader-source">回到阅读页</button><button class="btn primary" data-action="close-modal">关闭</button></div>`);
  $('#modal').classList.add('formula-dialog');
  const content=$('#modal-content');
  for(const [index,item] of value.formulas.entries()) {
    const preview=content.querySelector(`[data-preview="${index}"]`);
    renderFormulaMath(item.latex,preview);
    const form=content.querySelector(`[data-formula-form="${index}"]`);
    const input=form.elements.latex,status=form.querySelector('.formula-save-status');
    let timer;
    input.oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>{
      if(!input.isConnected)return;
      const valid=renderFormulaMath(input.value,preview);
      status.textContent=valid?'预览已更新，尚未保存。':'LaTeX 暂不能排版，请核对后修改。';
    },120);};
    form.querySelector('[data-restore-formula]').onclick=()=>{
      input.value=item.original_latex??item.latex;
      form.elements.label.value=item.original_label??item.label??'';
      renderFormulaMath(input.value,preview);
      status.textContent='已恢复为模型原始转录，点击“保存修改”后生效。';
    };
    form.onsubmit=async event=>{
      event.preventDefault();
      if(!input.value.trim()){status.textContent='公式不能为空。';return;}
      const button=form.querySelector('[type="submit"]');
      if(button.disabled)return;
      const latex=input.value,label=form.elements.label.value;
      button.disabled=true;status.textContent='正在保存…';
      try {
        const updated=await api(`/api/papers/${pid}/visual-cache/${page}/formulas/${index}`,{
          method:'PUT',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({latex,label,revision:value.revision??0})});
        if(!form.isConnected)return;
        // 原位更新版本和已保存状态，保留其他公式尚未保存的草稿。
        Object.assign(value,updated);
        const saved=updated.formulas[index];
        const heading=form.closest('.formula-card').querySelector('.formula-card-heading');
        heading.querySelector('strong').textContent=saved.label||'公式 '+(index+1);
        heading.querySelector('.muted').textContent=saved.edited?'已手动修改':'模型转录';
        const pending=input.value!==latex||form.elements.label.value!==label;
        status.textContent=pending?'提交的版本已保存；当前输入还有未保存的修改。':'修改已保存。原始模型转录仍保留，可随时恢复。';
        toast('公式修改已保存');
      }catch(error) {
        if(!status.isConnected)return;
        status.textContent=error.message;
        if(error.status===409) {
          const reload=document.createElement('button');reload.type='button';reload.className='btn small';reload.textContent='重新加载本页';
          reload.onclick=async()=>showVisualPageResult(await api(`/api/papers/${pid}/visual-cache/${page}`),pid,index);
          status.append(' 请先复制需要保留的草稿。 ',reload);
        }
      }finally {if(button.isConnected)button.disabled=false;}
    };
  }
  $('#formula-reader-source').onclick=async()=>{closeModal();await readPaper(pid,page);};
  if(scrolls) {
    $('.formula-source-pane').scrollTop=scrolls.source;
    $('.formula-content-pane').scrollTop=scrolls.content;
    content.scrollTop=scrolls.modal;
  }
}
