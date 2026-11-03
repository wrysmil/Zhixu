from pathlib import Path

p=Path('/Users/mima0000/Documents/学习-001/do-project/dontforget/design-demo/index.html')
s=p.read_text()
assert s==Path(__file__).with_name('index-before.html').read_text(), '文件已变更，请先合并'

s=s.replace('grid-template-columns:232px minmax(0,1fr)', 'grid-template-columns:252px minmax(0,1fr)')
s=s.replace('.new-page{width:min(720px,100%);margin:auto;padding:52px 28px 40px}', '.new-page{width:min(860px,100%);margin:0 auto;padding:clamp(32px,7vh,72px) 32px 48px}')
s=s.replace('.greeting{margin-bottom:24px}', '.greeting{text-align:center;margin-bottom:16px}')
s=s.replace('.quick-actions{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 44px}', '.quick-actions{display:flex;flex-wrap:wrap;gap:8px;margin:0 88px 16px 0;min-height:30px}')
s=s.replace('margin-bottom:32px}.thread', 'margin-bottom:16px}.thread')
s=s.replace('border-radius:6px;padding:6px 10px;font-size:12px;color:var(--ink-2)}\n.quick-actions', 'border-radius:18px;padding:5px 11px;font-size:12px;color:var(--ink-2)}\n.quick-actions')
css='''
/* Workspace groups and a conversation-first composer, based on the supplied reference. */
.sidebar-content{flex:1;min-height:0;overflow:auto;padding-bottom:12px}
.sidebar-content .sessions{overflow:visible;flex:none;padding-bottom:0}
.sidebar-content .sect{padding-top:18px}
.sect .iconbtn{width:22px;height:22px}
.workspace-group{padding:0 8px;margin:3px 0 10px}
.workspace-heading{display:flex;align-items:center;gap:1px}
.workspace-toggle{height:32px;width:22px;flex:none;display:grid;place-items:center;color:var(--ink-3);border-radius:5px}
.workspace-toggle:hover{background:var(--surface-3)}
.workspace-toggle[aria-expanded="true"] svg{transform:rotate(90deg)}
.workspace-link{display:flex;align-items:center;gap:8px;flex:1;min-width:0;text-align:left;padding:6px 7px;border-radius:6px;color:var(--ink-2);font-size:13px}
.workspace-link span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.workspace-link small{font-size:11px;color:var(--ink-3)}
.workspace-link:hover,.workspace-link.selected{background:var(--surface-3)}
.workspace-link.selected{color:var(--accent);font-weight:550}
.workspace-sessions{padding-left:25px}
.workspace-sessions .session{padding:7px 9px}
.workspace-sessions .session>svg{display:none}
.workspace-empty{padding:7px 10px;color:var(--ink-3);font-size:11px}
.context-workspace{color:var(--ink-3);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:180px}
.context-divider{color:var(--line-strong);font-size:12px}
.intent-tabs{display:flex;align-items:center;justify-content:center;gap:3px;width:max-content;max-width:100%;border:1px solid var(--line);border-radius:20px;padding:3px;background:var(--surface-2);margin:18px auto 62px}
.intent-tabs button{display:flex;align-items:center;gap:6px;border-radius:17px;font-size:12px;color:var(--ink-2);padding:5px 15px}
.intent-tabs button.active{background:var(--surface);color:var(--ink);box-shadow:0 1px 3px #17212d0c;font-weight:550}
.composer-shell{position:relative;background:var(--surface-2);border-radius:18px;flex:none}
.composer-shell .composer{position:relative;z-index:1;border-radius:17px;padding:16px;border-color:var(--line-strong);box-shadow:0 3px 10px #17212d05}
.composer-shell .composer textarea{min-height:76px}
.composer-shell .composer .bar{gap:8px}
.composer-shell .model-select{margin-left:auto}
.composer-shell .send{margin-left:2px}
.composer-mascot{position:absolute;right:16px;top:-66px;width:86px;height:80px;pointer-events:none;z-index:0}
.composer-mascot img{width:100%;height:100%;object-fit:contain;display:block}
[data-theme="dark"] .composer-mascot img,[data-theme="dark"] .brand .mark img{filter:brightness(1.9)}
.brand .mark img{width:20px;height:20px;object-fit:contain;display:block}
.composer-foot{display:flex;align-items:center;gap:16px;min-height:40px;padding:6px 16px;flex-wrap:wrap;font-size:12px;color:var(--ink-3)}
.workspace-picker{display:flex;align-items:center;gap:5px;min-width:0;max-width:100%;color:var(--ink-2)}
.workspace-picker select{border:0;border-radius:4px;background:transparent;padding:3px 3px;font-size:12px;max-width:210px;min-width:0;cursor:pointer;text-overflow:ellipsis}
.workspace-picker select:disabled{opacity:1;color:var(--ink-2);cursor:default}
.composer-foot .memory-scope{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--accent);padding:3px 0}
.recent-section{margin-top:36px}
.recent-section .recent-row{padding:11px 2px}
.recent-empty{font-size:12px;color:var(--ink-3);padding:18px 0}
.threadfoot{padding-top:44px}
dialog.workspace-dialog{width:min(400px,calc(100vw - 32px));padding:24px;border:1px solid var(--line);border-radius:12px;color:var(--ink);background:var(--surface);box-shadow:var(--shadow-lg)}
.workspace-dialog::backdrop{background:#12192255}
.workspace-dialog h2{font-size:18px;margin:0 0 8px}
.workspace-dialog p{font-size:12px;color:var(--ink-3);margin:0 0 18px}
.workspace-dialog label{display:block;font-size:13px;margin-bottom:7px}
.workspace-dialog .dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}
.workspace-error{color:var(--danger);font-size:12px;min-height:20px;margin-top:6px}
@media(max-width:760px){
  .new-page{padding:32px 20px}.greeting h1{font-size:23px}.greeting p{max-width:290px;margin:0 auto}
  .intent-tabs{margin-bottom:54px}.intent-tabs button{padding:5px 13px}
  .quick-actions{gap:6px;margin-right:64px}.quick-actions button{font-size:11px;padding:5px 9px}
  .composer-mascot{width:66px;height:64px;right:10px;top:-52px}
  .composer-shell .composer{padding:12px}.composer-shell .composer .bar{gap:4px}
  .composer-shell .model-select{max-width:150px}.composer-foot{gap:10px;padding:6px 12px}
  .workspace-picker select{max-width:155px}.context-workspace{max-width:100px}
  .threadfoot{padding-top:36px}.recent-section{margin-top:28px}
}
@media(max-width:380px){.new-page{padding:28px 16px}.composer-shell .model-select{max-width:132px}.composer-foot{gap:8px}.workspace-picker select{max-width:130px}.composer-foot .memory-scope{font-size:11px}.context-workspace,.context-divider{display:none}}
'''
s=s.replace('</style>',css+'\n</style>',1)
s=s.replace('<div class="toast" id="toast" role="status"></div>', '''<div class="toast" id="toast" role="status"></div>
<dialog class="workspace-dialog" id="workspaceDialog" aria-labelledby="workspaceDialogTitle">
  <form id="workspaceForm">
    <h2 id="workspaceDialogTitle">新建工作空间</h2>
    <p>把同一件事的对话与记忆放在一起。演示空间不会创建本地文件夹。</p>
    <label for="workspaceName">空间名称</label>
    <input class="input" id="workspaceName" name="name" maxlength="32" placeholder="例如：工作项目、学习笔记" required autocomplete="off" aria-describedby="workspaceError" />
    <div class="workspace-error" id="workspaceError" role="status"></div>
    <div class="dialog-actions"><button class="btn" id="workspaceCancel" type="button">取消</button><button class="btn primary" id="workspaceCreate" type="submit">创建空间</button></div>
  </form>
</dialog>''')
s=s.replace('const SESSIONS = [', '''const WORKSPACES = [
  {id:'project',name:'Dont Forget'},
  {id:'life',name:'个人生活'}
];
const SESSIONS = [''')
for old,new in [
 ("active:true}","active:true, workspaceId:'project'}"),
 ("m:'今天 · 42 条消息'}", "m:'今天 · 42 条消息', workspaceId:'project'}"),
 ("m:'昨天 · 27 条消息'}", "m:'昨天 · 27 条消息', workspaceId:'project'}"),
 ("m:'10月30日 · 15 条消息'}", "m:'10月30日 · 15 条消息', workspaceId:'project'}"),
 ("m:'10月28日 · 9 条消息'}", "m:'10月28日 · 9 条消息', workspaceId:'life'}")
]:
    assert old in s,old
    s=s.replace(old,new,1)
for i in range(1,8):
    s=s.replace("{id:'m"+str(i)+"', type:", "{id:'m"+str(i)+"', workspaceId:'project', type:")
s=s.replace("  sessionId: null,", "  sessionId: null,\n  workspaceId: 'project',\n  collapsedWorkspaces: [],\n  intent: 'remember',")
s=s.replace('  state.sessionId=index;', "  state.sessionId=index;\n  state.workspaceId=session.workspaceId||null;\n  state.collapsedWorkspaces=state.collapsedWorkspaces.filter(id=>id!==state.workspaceId);")
s=s.replace('function toolbar(title){', '''function currentWorkspace(){return WORKSPACES.find(w=>w.id===state.workspaceId);}
function workspaceName(){return currentWorkspace()?.name||'独立对话';}
function selectWorkspace(id){
  state.workspaceId=WORKSPACES.some(w=>w.id===id)?id:null;
  state.collapsedWorkspaces=state.collapsedWorkspaces.filter(x=>x!==state.workspaceId);
  if(state.page==='new')render();else navTo('new');
}
function workspacePicker(mode){
  return `<label class="workspace-picker">${icon('folder',14)}<select id="workspaceSelect" aria-label="工作空间" ${mode==='chat'?'disabled title="已有会话的工作空间已绑定"':''}><option value="" ${!state.workspaceId?'selected':''}>独立对话 · 不指定空间</option>${WORKSPACES.map(w=>`<option value="${esc(w.id)}" ${state.workspaceId===w.id?'selected':''}>${esc(w.name)}</option>`).join('')}</select></label>`;
}
function openWorkspaceDialog(){
  $('#workspaceForm').reset();$('#workspaceError').textContent='';$('#workspaceDialog').showModal();$('#workspaceName').focus();
}
function toolbar(title){''')
s=s.replace('<span class="main-title">${esc(title)}</span>', '<span class="main-title">${esc(title)}</span>${[\'new\',\'chat\'].includes(state.page)?`<span class="context-divider">/</span><span class="context-workspace">${esc(workspaceName())}</span>`:\'\'}')
s=s.replace('  return `<div class="composer">', '''  return `<div class="composer-shell">
    <div class="composer-mascot" aria-hidden="true"><img src="assets/dontforget-elephant-logo-v1.png" alt="" width="86" height="80" draggable="false" /></div>
    <div class="composer">''',1)
s=s.replace('''    </div>
  </div>`;
}
function memoryRail(){''', '''    </div>
    </div><div class="composer-foot">${workspacePicker(mode)}<button class="memory-scope" data-rail title="查看当前工作空间的示例记忆">${icon('brain',13)} ${state.workspaceId?'空间记忆':'本次记忆'} ${icon('chevron',10)}</button></div>
  </div>`;
}
function memoryRail(){''',1)
start=s.index('function memoryRail(){')
end=s.index('function pageChat(){',start)
s=s[:start]+'''
function memoryRail(){
  const memories=MEMORIES.filter(m=>m.workspaceId===state.workspaceId);
  return `<div class="head"><div class="row between"><h2 class="h2">${state.page==='chat'?'相关记忆':'空间记忆'}</h2><button class="iconbtn" data-rail aria-label="关闭记忆侧栏">×</button></div><p class="sub" style="margin-top:8px">${esc(workspaceName())} · ${state.memoryEnabled?'示例记忆':'本次已关闭记忆'}</p></div>
    <div class="scroll">${state.memoryEnabled?memories.slice(0,4).map(m=>`<button class="railitem" data-open-memory="${m.id}"><span class="tag acc">${m.type}</span><div class="t" style="margin-top:8px">${esc(m.title)}</div><div class="meta">${esc(m.src)}</div></button>`).join('')||'<p class="sub">当前空间暂无记忆。正式接入后，对话中的偏好与决定会在这里归档。</p>':'<p class="sub">本次对话不会使用空间记忆。</p>'}<button class="btn sm" style="margin-top:12px" data-go="memory">查看全部记忆 ${icon('arrow',12)}</button></div>`;
}

function sessionButton(i){
  const session=SESSIONS[i];const active=state.page==='chat'&&state.sessionId===i;
  return `<button class="session ${active?'active':''}" data-session="${i}" title="${esc(session.t)}" ${active?'aria-current="page"':''}>${icon('chat',14)}<span class="t">${esc(session.t)}</span></button>`;
}
function renderSidebar(){
  const independent=state.sessionOrder.filter(i=>!SESSIONS[i].workspaceId);
  const workspaces=WORKSPACES.map(w=>{
    const collapsed=state.collapsedWorkspaces.includes(w.id);
    const ids=state.sessionOrder.filter(i=>SESSIONS[i].workspaceId===w.id);
    return `<section class="workspace-group" data-workspace-group="${esc(w.id)}">
      <div class="workspace-heading"><button class="workspace-toggle" data-workspace-toggle="${esc(w.id)}" aria-label="展开或收起 ${esc(w.name)} 的会话" aria-expanded="${!collapsed}" aria-controls="workspace-sessions-${esc(w.id)}">${icon('chevron',12)}</button><button class="workspace-link ${state.workspaceId===w.id?'selected':''}" data-workspace="${esc(w.id)}" ${state.workspaceId===w.id?'aria-current="true"':''}>${icon('folder',15)}<span>${esc(w.name)}</span><small>${ids.length}</small></button></div>
      <div class="workspace-sessions" id="workspace-sessions-${esc(w.id)}" ${collapsed?'hidden':''}>${ids.map(sessionButton).join('')||'<div class="workspace-empty">暂无对话</div>'}</div>
    </section>`;
  }).join('');
  $('#sidebar').innerHTML=`
    <div class="sidebar-caption"><span class="workspace-icon">${icon('brain',14)}</span>记忆工作台</div>
    <button class="newchat" data-go="new">${icon('plus',15)} 新对话</button>
    <nav class="nav" aria-label="常用功能">
      <button id="paletteBtn">${icon('search',16)} 搜索<span class="badge">⌘ K</span></button>
      <a href="#/memory" class="${state.page==='memory'?'active':''}">${icon('brain',16)} 记忆库<span class="badge">${MEMORIES.length}</span></a>
      <a href="#/automations" class="${state.page==='automations'?'active':''}">${icon('clock',16)} 自动化</a>
    </nav>
    <div class="sidebar-content">
      <div class="sect">独立对话 <span>${independent.length}</span></div>
      <div class="sessions">${independent.map(sessionButton).join('')||'<div class="workspace-empty">未归入空间的对话会显示在这里</div>'}</div>
      <div class="sect"><span>工作空间 <span class="muted">(${WORKSPACES.length})</span></span><button class="iconbtn" id="addWorkspace" title="新建工作空间" aria-label="新建工作空间">${icon('plus',14)}</button></div>
      ${workspaces}
    </div>
    <div class="sidebottom"><nav class="nav" aria-label="配置与工具">
      <a href="#/agents" class="${['agents','skills','identity'].includes(state.page)?'active':''}">${icon('bot',16)} 助手与工具</a>
      <a href="#/settings" class="${state.page==='settings'?'active':''}">${icon('gear',16)} 设置</a>
    </nav><div class="sidefoot">${icon('lock',12)} 演示数据 · 未连接服务</div></div>`;
  $('#paletteBtn').onclick=openPalette;$('#addWorkspace').onclick=openWorkspaceDialog;
}

function pageNew(){
  const prompts={remember:['记住一个新偏好','保存项目决定','整理会议记忆'],recall:['找回聊过的事','找回项目约定','查找我的偏好'],review:['回顾之前的决定','回顾这周的进展','整理待办与计划']};
  const recent=state.sessionOrder.filter(i=>(SESSIONS[i].workspaceId||null)===state.workspaceId).slice(0,2);
  return {main:`<div class="scroll new-scroll"><div class="new-page">
    <div class="greeting"><h1>聊过的事，不必从头说。</h1><p>记住你的偏好、决定与经历，在需要时为你找回。</p></div>
    <div class="intent-tabs" role="group" aria-label="记忆任务">${[['remember','brain','记住'],['recall','search','找回'],['review','clock','回顾']].map(([id,ico,label])=>`<button class="${state.intent===id?'active':''}" data-mode="${id}" aria-pressed="${state.intent===id}">${icon(ico,14)}${label}</button>`).join('')}</div>
    <div class="quick-actions">${prompts[state.intent].map(t=>`<button data-prompt="${esc(t)}">${esc(t)}</button>`).join('')}</div>
    ${composer('new')}
    <div class="composer-hint">Enter 发送 · Shift + Enter 换行</div>
    <section class="recent-section" aria-label="当前空间最近会话"><div class="recent-heading"><h2>从上次的对话继续</h2><span>${esc(workspaceName())}</span></div>
      ${recent.map(i=>{const session=SESSIONS[i];return `<button class="recent-row" data-session="${i}">${icon('chat',15)}<span class="recent-title">${esc(session.t)}</span><time>${esc(session.m.split(' · ')[0])}</time>${icon('chevron',12)}</button>`;}).join('')||'<p class="recent-empty">这个空间还没有对话，从第一条消息开始。</p>'}
    </section>
  </div></div>`,rail:state.railOpen?memoryRail():null};
}

''' + s[end:]
s=s.replace("m:'今天 · 新对话'}", "m:'今天 · 新对话',workspaceId:state.workspaceId}")
s=s.replace("  renderSidebar();\n  const out", "  if(state.page==='chat')state.workspaceId=SESSIONS[state.sessionId].workspaceId||null;\n  const workspaceLabel=$('.workspace-name');if(workspaceLabel)workspaceLabel.textContent=workspaceName();\n  renderSidebar();\n  const out")
s=s.replace("  document.querySelectorAll('[data-session]').forEach", '''  document.querySelectorAll('[data-workspace]').forEach(b=>b.onclick=()=>selectWorkspace(b.dataset.workspace));
  document.querySelectorAll('[data-workspace-toggle]').forEach(b=>b.onclick=()=>{const id=b.dataset.workspaceToggle;state.collapsedWorkspaces=state.collapsedWorkspaces.includes(id)?state.collapsedWorkspaces.filter(x=>x!==id):[...state.collapsedWorkspaces,id];render();});
  document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{state.intent=b.dataset.mode;render();});
  const workspaceSelect=$('#workspaceSelect');if(workspaceSelect)workspaceSelect.onchange=()=>selectWorkspace(workspaceSelect.value||null);
  document.querySelectorAll('[data-session]').forEach''',1)
s=s.replace("  $('#brandMark').innerHTML=icon('brain',16);", "  $('#brandMark').innerHTML='<img src=\"assets/dontforget-elephant-logo-v1.png\" alt=\"\" width=\"20\" height=\"20\" />';")
s=s.replace("  $('#workspaceCancel')", "  $('#workspaceCancel')")
s=s.replace("  $('#paletteClose').onclick=closePalette;", '''  $('#paletteClose').onclick=closePalette;
  $('#workspaceCancel').onclick=()=>$('#workspaceDialog').close();
  $('#workspaceForm').onsubmit=e=>{
    e.preventDefault();const name=$('#workspaceName').value.trim();
    if(!name){$('#workspaceError').textContent='请输入空间名称';$('#workspaceName').focus();return;}
    if(WORKSPACES.some(w=>w.name.toLocaleLowerCase()===name.toLocaleLowerCase())){$('#workspaceError').textContent='该空间名称已存在';$('#workspaceName').focus();return;}
    const id='space-'+WORKSPACES.length;WORKSPACES.push({id,name});$('#workspaceDialog').close();selectWorkspace(id);
    requestAnimationFrame(()=>$('#workspaceSelect')?.focus());
  };''')
s=s.replace("  document.addEventListener('keydown',e=>{\n    if((e.metaKey", "  document.addEventListener('keydown',e=>{\n    if($('#workspaceDialog').open)return;\n    if((e.metaKey")
p.write_text(s)
print('已更新工作空间分组、选择/创建、首页布局与小象图标。')
