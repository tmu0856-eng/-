/* 登录凭据只发往同源认证接口，不写入 URL、浏览器存储或日志。 */
(() => {
  const form=document.querySelector('#login-form');
  const user=document.querySelector('#login-user');
  const password=document.querySelector('#login-password');
  const button=document.querySelector('#login-submit');
  const feedback=document.querySelector('#login-feedback');
  const toggle=document.querySelector('#toggle-password');
  const help=document.querySelector('#account-help');
  const label=button.querySelector('.button-label');
  let busy=false;
  // 仅允许回到本站工作台，拒绝外站、登录页循环及任意静态文件路径。
  function destination(){
    try{
      const target=new URL(new URLSearchParams(location.search).get('next')||'/'+(location.hash||'#library'),location.origin);
      if(target.origin===location.origin&&target.pathname==='/')return target.pathname+target.search+target.hash;
    }catch{}
    return '/#library';
  }
  function message(text,error=false){
    feedback.textContent=text;
    feedback.setAttribute('role',error?'alert':'status');
  }
  function loading(active){
    busy=active;button.disabled=active;form.setAttribute('aria-busy',String(active));
    label.textContent=active?'正在登录…':'进入研究空间';
    button.querySelector('.submit-arrow').hidden=active;
    button.querySelector('.button-spinner').hidden=!active;
  }
  toggle.onclick=()=>{
    const visible=password.type==='password';
    password.type=visible?'text':'password';
    toggle.setAttribute('aria-pressed',String(visible));
    toggle.setAttribute('aria-label',visible?'隐藏密码':'显示密码');
  };
  help.onclick=()=>{
    const open=help.getAttribute('aria-expanded')!=='true';
    help.setAttribute('aria-expanded',String(open));document.querySelector('#account-help-text').hidden=!open;
  };
  for(const input of [user,password])input.addEventListener('input',()=>{
    input.removeAttribute('aria-invalid');if(!busy)message('');
  });
  for(const event of ['keydown','keyup'])password.addEventListener(event,e=>{
    document.querySelector('#caps-lock').hidden=!e.getModifierState('CapsLock');
  });
  password.addEventListener('blur',()=>{document.querySelector('#caps-lock').hidden=true;});
  async function authRequest(path,options={}){
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),20000);
    try{
      const response=await fetch(path,{...options,credentials:'same-origin',cache:'no-store',signal:controller.signal});
      let value;try{value=await response.json();}catch{throw new Error('服务暂时未响应，请稍后重试。');}
      if(!response.ok){
        const error=new Error(response.status===401?'账号或密码不正确，请重新输入。':response.status===429?'尝试次数较多，请一分钟后重试。':'登录服务暂时不可用，请稍后重试。');
        error.status=response.status;throw error;
      }
      return value;
    }catch(error){
      if(error.name==='AbortError')throw new Error('连接超时，请检查网络后重试。');
      if(error instanceof TypeError)throw new Error('暂时无法连接服务，请检查网络后重试。');
      throw error;
    }finally{clearTimeout(timer);}
  }
  form.onsubmit=async event=>{
    event.preventDefault();if(busy||!form.reportValidity())return;
    if(!user.value.trim()){user.setCustomValidity('请输入访问账号');user.reportValidity();user.setCustomValidity('');return;}
    loading(true);message('');
    try{
      await authRequest('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:user.value.trim(),password:password.value})});
      password.value='';label.textContent='登录成功，正在进入…';location.replace(destination());
    }catch(error){
      message(error.message,true);
      if(error.status===401){for(const input of [user,password])input.setAttribute('aria-invalid','true');password.focus();password.select();}
      loading(false);
    }
  };
  // 已登录或本地未启用访问控制时直接进入工作台，避免重复登录。
  loading(true);label.textContent='正在连接…';
  authRequest('/api/auth/status').then(auth=>{
    if(!auth.enabled||auth.authenticated){location.replace(destination());return;}
    loading(false);
  }).catch(error=>{loading(false);message(error.message,true);});
})();
