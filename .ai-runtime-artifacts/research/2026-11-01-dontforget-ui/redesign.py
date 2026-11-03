from pathlib import Path
import re

target = Path('/Users/mima0000/Documents/学习-001/do-project/dontforget/design-demo/index.html')
html = target.read_text()
original = Path(__file__).with_name('index-before.html').read_text()
assert html == original, '页面在编辑前发生变化，请先合并'

css = '''
:root {
  color-scheme: light;
  --paper:#ffffff; --surface:#ffffff; --surface-2:#f7f8f9; --surface-3:#eef0f2;
  --ink:#24282e; --ink-2:#505963; --ink-3:#69737e;
  --line:#e6e9ec; --line-strong:#cdd3d9;
  --accent:#286453; --accent-2:#e9f2ee; --ok:#286453; --ok-2:#e9f2ee;
  --warn:#956512; --danger:#b44242; --shadow:none; --shadow-lg:0 16px 48px #17212d20;
  --r-sm:6px; --r-md:8px; --r-lg:12px;
  --ui:14px; --small:13px; --tiny:12px; --mini:11px;
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
}
[data-theme="dark"] {
  color-scheme: dark;
  --paper:#1c1e22; --surface:#22252a; --surface-2:#191b1f; --surface-3:#2c3036;
  --ink:#e6e9ed; --ink-2:#bdc3cc; --ink-3:#929ca9;
  --line:#32363e; --line-strong:#484f59; --accent:#91c7b0; --accent-2:#273e35;
  --ok:#91c7b0; --ok-2:#273e35; --danger:#ee9292; --shadow-lg:0 16px 48px #0005;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--paper);color:var(--ink);font:var(--ui)/1.6 var(--font);-webkit-font-smoothing:antialiased}
button,input,textarea,select{font:inherit;color:inherit}
button{background:none;border:0;cursor:pointer}
button:disabled{opacity:.35;cursor:default}
a{color:inherit}
button,select,a,input,textarea{touch-action:manipulation}
:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
[hidden]{display:none!important}
::selection{background:var(--accent-2)}
svg{flex-shrink:0;vertical-align:middle}
.serif{font-family:var(--font);font-weight:600}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:var(--tiny)}
.app{height:100vh;height:100dvh;display:grid;grid-template-rows:44px minmax(0,1fr);overflow:hidden}
.titlebar{display:flex;align-items:center;gap:16px;padding:0 16px;border-bottom:1px solid var(--line);background:var(--surface-2)}
.lights{display:flex;gap:7px;flex:none}
.light{width:10px;height:10px;border-radius:50%;display:block}
.light.r{background:#eb6e65}.light.y{background:#efbf51}.light.g{background:#60bf70}
.brand{display:flex;gap:8px;align-items:center;font-size:12px;font-weight:650;white-space:nowrap}
.brand .mark{display:grid;place-items:center;color:var(--accent)}
.tb-spacer{flex:1}
.workspace-name{font-size:12px;color:var(--ink-3)}
.demo-label{font-size:11px;color:var(--ink-3);border:1px solid var(--line-strong);padding:1px 6px;border-radius:4px}
.iconbtn{width:30px;height:30px;border-radius:6px;display:inline-grid;place-items:center;color:var(--ink-2);flex:none}
.iconbtn:hover{background:var(--surface-3);color:var(--ink)}
.app>.body{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:0;position:relative}
.app>.body.with-rail{grid-template-columns:232px minmax(0,1fr) 296px}
.sidebar{background:var(--surface-2);border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0;overflow:hidden;padding-top:16px}
.sidebar-caption{padding:0 18px 12px;display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600;color:var(--ink-2)}
.workspace-icon{display:grid;place-items:center;width:26px;height:26px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--accent)}
.newchat{margin:0 12px 8px;min-height:36px;border-radius:6px;display:flex;align-items:center;gap:8px;padding:0 12px;background:var(--accent);color:var(--paper);font-size:13px;font-weight:550}
[data-theme="dark"] .newchat{color:#142a22}
.newchat:hover{filter:brightness(.94)}
.newchat .shortcut{margin-left:auto;font-size:11px;opacity:.7}
.nav{padding:2px 8px;display:flex;flex-direction:column;gap:2px}
.nav a,.nav button{display:flex;align-items:center;gap:9px;min-height:34px;padding:0 10px;border-radius:6px;color:var(--ink-2);text-decoration:none;font-size:13px;text-align:left;width:100%}
.nav a:hover,.nav button:hover{background:var(--surface-3)}
.nav a.active{background:var(--surface-3);color:var(--ink);font-weight:550}
.nav a.active svg{color:var(--accent)}
.nav .badge{margin-left:auto;color:var(--ink-3);font-size:11px;font-weight:400}
.sect{display:flex;justify-content:space-between;align-items:center;padding:20px 18px 7px;font-size:11px;color:var(--ink-3);font-weight:500}
.sessions{overflow:auto;padding:0 8px 8px;flex:1;min-height:0}
.session{width:100%;text-align:left;padding:8px 10px;border-radius:6px;margin:1px 0;display:flex;align-items:center;gap:9px;color:var(--ink-3)}
.session:hover{background:var(--surface-3)}
.session.active{background:var(--surface-3);color:var(--accent)}
.session .t{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--ink-2)}
.session.active .t{color:var(--ink);font-weight:550}
.session .m{display:none}
.session-group{font-size:11px;color:var(--ink-3);padding:10px 10px 4px}
.sidebottom{border-top:1px solid var(--line);margin-top:auto;padding:8px 0}
.sidefoot{padding:10px 18px 4px;display:flex;align-items:center;gap:6px;font-size:11px;color:var(--ink-3)}
.main{display:flex;flex-direction:column;min-height:0;min-width:0;overflow:hidden;background:var(--paper)}
.mainbar{display:flex;align-items:center;gap:8px;min-height:54px;padding:8px 28px;border-bottom:1px solid var(--line);flex:none}
.mainbar h1{font-size:13px;font-weight:550;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mainbar .spacer{flex:1}
.mainbar .btn{font-size:12px}
.scroll{overflow:auto;min-height:0;flex:1;overscroll-behavior:contain}
.page{padding:32px;max-width:1040px;margin:0 auto;width:100%}
.page.wide{max-width:none}
.h1{font-family:var(--font);font-size:24px;font-weight:600;line-height:1.4;letter-spacing:-.025em;margin:0 0 8px}
.h2{font-family:var(--font);font-size:15px;font-weight:600;line-height:1.5;margin:0}
.sub{color:var(--ink-3);font-size:13px;margin:0}
.eyebrow{font-size:11px;color:var(--ink-3);margin-bottom:8px;font-weight:500}
.row{display:flex;align-items:center;gap:10px;min-width:0}
.row.between{justify-content:space-between}
.grow{flex:1;min-width:0}
.muted{color:var(--ink-3)}.small{font-size:13px}.tiny{font-size:12px}
.btn{min-height:32px;padding:4px 11px;border-radius:6px;border:1px solid var(--line-strong);background:var(--surface);font-size:13px;display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap}
.btn:hover{background:var(--surface-3)}
.btn.primary{background:var(--accent);color:var(--paper);border-color:var(--accent)}
[data-theme="dark"] .btn.primary{color:#142a22}
.btn.ghost{border-color:transparent;background:transparent;color:var(--ink-2)}
.btn.ghost:hover{background:var(--surface-3)}
.btn.danger{color:var(--danger)}.btn.sm{min-height:28px;padding:3px 8px;font-size:12px}
.card,.tile{background:var(--surface);border:1px solid var(--line);border-radius:8px;box-shadow:none}
.pad{padding:16px}.sep{height:1px;background:var(--line);margin:16px 0}
.chip{display:inline-flex;align-items:center;gap:6px;min-height:27px;padding:3px 9px;border-radius:6px;border:1px solid var(--line);font-size:12px;color:var(--ink-2)}
.chip.on{background:var(--accent-2);border-color:var(--accent-2);color:var(--accent)}
.tag{font-size:11px;color:var(--ink-3);background:var(--surface-3);border-radius:4px;padding:2px 6px}
.tag.ok,.tag.acc{color:var(--accent);background:var(--accent-2)}
.new-scroll{display:flex;flex-direction:column}
.new-page{width:min(720px,100%);margin:auto;padding:52px 28px 40px}
.greeting{margin-bottom:24px}
.greeting h1{font-size:26px;line-height:1.4;letter-spacing:-.025em;font-weight:600;margin:0 0 8px}
.greeting p{margin:0;color:var(--ink-3);font-size:13px}
.composer{border:1px solid var(--line-strong);border-radius:12px;background:var(--surface);padding:12px;transition:border-color .15s}
.composer:focus-within{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-2)}
.composer textarea{width:100%;resize:none;border:0;outline:0;background:transparent;font-size:14px;line-height:1.7;min-height:64px;max-height:180px;padding:2px 4px;display:block}
.composer textarea::placeholder{color:var(--ink-3)}
.composer .bar{display:flex;align-items:center;gap:6px;padding-top:8px;flex-wrap:wrap}
.send{margin-left:auto;width:30px;height:30px;border-radius:7px;display:grid;place-items:center;background:var(--accent);color:var(--paper)}
[data-theme="dark"] .send{color:#142a22}
.send svg{transform:rotate(-45deg)}
.pill{display:inline-flex;align-items:center;gap:6px;min-height:28px;padding:3px 7px;border-radius:5px;border:0;background:transparent;font-size:12px;color:var(--ink-2)}
.pill:hover{background:var(--surface-3)}
.pill.memory-on{color:var(--accent)}
.model-select{border:0;background:transparent;max-width:172px;font-size:12px;padding:4px;border-radius:4px;cursor:pointer}
.composer-hint{font-size:11px;color:var(--ink-3);text-align:right;margin-top:8px}
.quick-actions{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 44px}
.quick-actions button{display:flex;align-items:center;gap:7px;border:1px solid var(--line);border-radius:6px;padding:6px 10px;font-size:12px;color:var(--ink-2)}
.quick-actions button:hover{background:var(--surface-2);border-color:var(--line-strong)}
.recent-heading{display:flex;align-items:center;justify-content:space-between;font-size:12px;color:var(--ink-3);padding-bottom:10px;border-bottom:1px solid var(--line)}
.recent-heading h2{font-size:12px;font-weight:500;margin:0}
.recent-row{display:flex;align-items:center;gap:12px;width:100%;padding:13px 2px;border-bottom:1px solid var(--line);font-size:13px;text-align:left}
.recent-row:hover{background:var(--surface-2)}
.recent-row>svg{color:var(--ink-3)}
.recent-row .recent-title{flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.recent-row time{font-size:11px;color:var(--ink-3)}
.attachment-list{display:flex;gap:6px;flex-wrap:wrap}
.attachment-item{display:flex;align-items:center;gap:5px;max-width:100%;font-size:12px;padding:4px 7px;margin-bottom:7px;border:1px solid var(--line);border-radius:5px;overflow-wrap:anywhere}
.attachment-item button{color:var(--ink-3);padding:0 3px}
.thread{padding:32px 28px;max-width:800px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:28px}
.msg{display:flex;gap:12px;min-width:0}
.msg .who{width:28px;height:28px;border-radius:7px;display:grid;place-items:center;flex:none;font-size:11px;font-weight:600}
.msg.user .who{background:var(--surface-3);color:var(--ink-2)}
.msg.ai .who{background:var(--accent-2);color:var(--accent)}
.msg .content{min-width:0;flex:1;overflow-wrap:anywhere}
.msg .name{font-size:12px;color:var(--ink-3);margin:3px 0 8px}
.msg.user .bubble{background:var(--surface-2);border-radius:8px;padding:10px 14px;display:inline-block;white-space:pre-wrap}
.msg.ai .prose{font-size:14px;line-height:1.85}
.msg.ai .prose p{margin:0 0 12px}.msg.ai .prose ul{margin:0 0 12px;padding-left:20px}
.msg.ai .prose li{margin:5px 0}
.tool{border:1px solid var(--line);border-radius:6px;background:var(--surface-2);padding:8px 10px;font-size:12px;color:var(--ink-2);margin:10px 0}
.tool summary{display:flex;align-items:center;gap:7px;cursor:pointer;list-style:none}
.tool summary::-webkit-details-marker{display:none}
.tool summary::after{content:'⌄';margin-left:auto;color:var(--ink-3)}
.tool .tool-detail{margin-top:8px;font-size:12px;color:var(--ink-3)}
.tool .ok{color:var(--ok)}
.memref{display:inline-flex;align-items:center;gap:5px;padding:1px 6px;border-radius:4px;background:var(--accent-2);color:var(--accent);font-size:12px}
.threadfoot{padding:0 28px 18px;width:100%;max-width:800px;margin:0 auto;flex:none}
.rail{border-left:1px solid var(--line);background:var(--surface-2);display:flex;flex-direction:column;min-height:0;min-width:0}
.rail .head{padding:16px;border-bottom:1px solid var(--line)}
.rail .scroll{padding:16px}
.railitem{display:block;width:100%;text-align:left;border:1px solid var(--line);border-radius:7px;background:var(--surface);padding:12px;margin-bottom:10px}
.railitem:hover{border-color:var(--line-strong)}
.railitem .t{font-size:13px}.railitem .meta{font-size:11px;color:var(--ink-3);margin-top:8px}
.conf{height:3px;background:var(--surface-3);margin-top:10px;border-radius:2px;overflow:hidden}.conf i{display:block;height:100%;background:var(--accent)}
.memwrap{display:grid;grid-template-columns:minmax(220px,300px) minmax(0,1fr);min-height:0;flex:1;overflow:hidden}
.memlist{border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0;min-width:0}
.memlist .tools{padding:16px 12px;border-bottom:1px solid var(--line)}
.input{width:100%;height:34px;border-radius:6px;border:1px solid var(--line-strong);background:var(--surface);padding:0 10px;font-size:13px;min-width:0}
.memlist .items{overflow:auto;padding:8px}
.memrow{width:100%;text-align:left;padding:12px 10px;border-radius:6px;margin-bottom:3px;border:1px solid transparent}
.memrow:hover{background:var(--surface-2)}.memrow.active{background:var(--accent-2);border-color:var(--accent-2)}
.memrow .t{font-size:13px}.memrow .m{display:flex;gap:6px;align-items:center;margin-top:7px;font-size:11px;color:var(--ink-3)}
.memdetail{padding:28px;overflow:auto;min-width:0;overflow-wrap:anywhere}
.memdetail .body{font-size:14px;line-height:1.8;max-width:70ch}
.grid{display:grid;gap:12px}.grid.c3{grid-template-columns:repeat(3,minmax(0,1fr))}.grid.c2{grid-template-columns:repeat(2,minmax(0,1fr))}
.tile{padding:16px}.tile:hover{border-color:var(--line-strong)}
.tile .ico{width:30px;height:30px;border-radius:6px;background:var(--surface-2);display:grid;place-items:center;color:var(--accent);margin-bottom:12px;flex:none}
.tile h3{margin:0 0 5px;font-size:13px;font-weight:600}.tile p{margin:0;font-size:12px;color:var(--ink-2);line-height:1.6}
.table{width:100%;border-collapse:collapse;font-size:13px}.table th{text-align:left;font-size:11px;color:var(--ink-3);font-weight:500;padding:10px 12px;border-bottom:1px solid var(--line)}
.table td{padding:14px 12px;border-bottom:1px solid var(--line)}.table tr:hover td{background:var(--surface-2)}
.switch{display:inline-block;width:32px;height:19px;border-radius:20px;background:var(--line-strong);position:relative;flex:none;cursor:pointer;vertical-align:middle}
.switch i{position:absolute;top:2px;left:2px;width:15px;height:15px;border-radius:50%;background:#fff;transition:transform .15s}
.switch.on{background:var(--accent)}.switch.on i{transform:translateX(13px)}
.settings{display:grid;grid-template-columns:168px minmax(0,1fr);min-height:0;flex:1;overflow:hidden}
.setsub{border-right:1px solid var(--line);padding:16px 8px;overflow:auto}
.setsub button{display:flex;width:100%;min-height:34px;align-items:center;gap:8px;padding:0 10px;border-radius:6px;color:var(--ink-2);font-size:13px}
.setsub button:hover,.setsub button.active{background:var(--surface-3);color:var(--ink)}
.setbody{padding:28px;overflow:auto;max-width:820px;min-width:0}
.field{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 0;border-bottom:1px solid var(--line)}
.field .lbl b{display:block;font-size:13px;font-weight:550}.field .lbl span{display:block;font-size:12px;color:var(--ink-3);margin-top:4px}
.seg{display:flex;border:1px solid var(--line-strong);border-radius:6px;overflow:hidden;flex-wrap:wrap;flex:none;max-width:100%}
.seg button{min-height:28px;padding:3px 9px;font-size:12px;color:var(--ink-2)}
.seg button+button{border-left:1px solid var(--line)}.seg button.on{background:var(--surface-3);color:var(--ink)}
.wb{display:grid;grid-template-columns:190px minmax(0,1fr);min-height:0;flex:1;overflow:hidden}
.tree{border-right:1px solid var(--line);padding:12px 8px;font-size:12px;overflow:auto}
.tree .f{display:flex;gap:7px;align-items:center;height:28px;padding:0 8px;border-radius:5px;color:var(--ink-2)}
.tree .f:hover{background:var(--surface-3)}.tree .f.dir{font-weight:600}.tree .f.ind{padding-left:22px}
.wbmain{display:flex;flex-direction:column;min-height:0;min-width:0}
.tabs{display:flex;border-bottom:1px solid var(--line);padding:6px 8px 0;overflow:auto;flex:none}
.tab{padding:6px 10px;font-size:12px;color:var(--ink-3);white-space:nowrap}.tab.on{background:var(--surface-2);color:var(--ink);border-bottom:2px solid var(--accent)}
.term{flex:1;background:#1b1e23;color:#d7dde4;font:12px/1.8 ui-monospace,monospace;padding:20px;overflow:auto}.term .p{color:#91c7b0}.term .c{color:#e0c585}.term .d{color:#8e99a8}
.overlay{position:fixed;inset:0;background:#12192255;display:none;align-items:flex-start;justify-content:center;padding-top:14vh;z-index:50}
.overlay.on{display:flex}.palette{width:min(600px,calc(100vw - 32px));background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow-lg);overflow:hidden}
.palette .in{display:flex;gap:10px;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line)}
.palette input{border:0;outline:0;background:transparent;flex:1;font-size:14px;min-width:0}
.palette .list{padding:6px;max-height:52vh;overflow:auto}
.palette .item{display:flex;gap:10px;align-items:center;width:100%;text-align:left;padding:10px;border-radius:6px;font-size:13px;color:var(--ink-2)}
.palette .item:hover,.palette .item.sel{background:var(--surface-3);color:var(--ink)}.palette .item .k{margin-left:auto;font-size:11px;color:var(--ink-3)}
.palette-footer{padding:8px 16px;border-top:1px solid var(--line);font-size:11px;color:var(--ink-3)}
.connect{height:100%;display:grid;place-items:center;padding:20px}.connect .card{width:min(390px,100%);padding:28px}
.connect .mark{color:var(--accent);margin-bottom:16px}.connect h1{font-size:24px;margin:0 0 6px}.connect p{color:var(--ink-3);font-size:13px;margin:0 0 20px}
.connect input{width:100%;height:38px;border-radius:6px;border:1px solid var(--line-strong);background:var(--surface);padding:0 12px;margin-bottom:12px}.connect .btn{width:100%;height:36px}
.toast{position:fixed;bottom:24px;left:50%;transform:translate(-50%,10px);background:var(--ink);color:var(--paper);padding:10px 16px;border-radius:7px;font-size:13px;opacity:0;pointer-events:none;transition:.15s;z-index:60;max-width:calc(100vw - 24px)}
.toast.on{opacity:1;transform:translate(-50%,0)}
.sidebar-shade{display:none}
body.sidebar-collapsed .app>.body,body.sidebar-collapsed .app>.body.with-rail{grid-template-columns:minmax(0,1fr)}
body.sidebar-collapsed .sidebar{display:none}
body.sidebar-collapsed .app>.body.with-rail{grid-template-columns:minmax(0,1fr) 296px}
@media(min-width:761px){.mobile-only{display:none!important}}
@media(max-width:1120px){.app>.body.with-rail{grid-template-columns:232px minmax(0,1fr)}.rail:not([hidden]){position:absolute;right:0;top:0;bottom:0;width:296px;z-index:15;box-shadow:-12px 0 30px #17212d0d}.field{align-items:flex-start;flex-direction:column}.grid.c3{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:760px){
  .app>.body,.app>.body.with-rail{grid-template-columns:minmax(0,1fr)!important}
  .sidebar{display:none}.sidebar-open .sidebar{display:flex!important;position:absolute;top:0;bottom:0;left:0;width:248px;z-index:30}
  .sidebar-open .sidebar-shade{display:block;position:absolute;inset:0;background:#12192255;z-index:25}
  .workspace-name{display:none}.titlebar{gap:12px;padding:0 12px}.lights{gap:5px}.light{width:9px;height:9px}
  .mainbar{padding:8px 16px;min-height:50px}.new-page{padding:32px 20px}.greeting h1{font-size:23px}
  .quick-actions{margin-bottom:32px}.thread{padding:24px 20px}.threadfoot{padding:0 16px 12px}.page{padding:24px 20px}
  .memwrap{display:flex;flex-direction:column;overflow:auto}.memlist{flex:none;border-right:0;max-height:280px;border-bottom:1px solid var(--line)}.memlist .items{min-height:0}.memdetail{overflow:visible;padding:20px;flex:none}
  .settings{grid-template-columns:128px minmax(0,1fr)}.setbody{padding:20px 16px}.grid.c2,.grid.c3{grid-template-columns:minmax(0,1fr)}
  .wb{grid-template-columns:130px minmax(0,1fr)}.rail:not([hidden]){width:min(320px,100%)}
  .row.between{flex-wrap:wrap}.table{font-size:12px}.table td,.table th{padding:10px 6px}.table .mono{font-size:11px}
  .seg{flex-wrap:wrap}.setbody .h1{font-size:22px!important}.memdetail .h1{font-size:22px!important}
}
@media(max-width:380px){.settings{grid-template-columns:100px minmax(0,1fr)}.setsub{padding:12px 4px}.setbody{padding:16px 12px}.mainbar{padding:8px 12px}.new-page{padding:28px 16px}.model-select{max-width:135px}.pill{padding:3px 5px}.composer .bar{gap:3px}.demo-label{font-size:10px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;scroll-behavior:auto!important}}
'''
html = re.sub(r'<style>.*?</style>', '<style>\n' + css + '</style>', html, count=1, flags=re.S)
html = re.sub(r'<link[^>]*>', '', html)
html = html.replace('<title>Dont Forget — Design Demo</title>', '<title>Dont Forget · 个人 AI 工作台</title>')
start = html.index('  <header class="titlebar">')
end = html.index('\n</div>\n\n<div class="overlay"', start)
html = html[:start] + '''  <header class="titlebar">
    <div class="lights" aria-hidden="true"><i class="light r"></i><i class="light y"></i><i class="light g"></i></div>
    <div class="brand"><span class="mark" id="brandMark"></span>Dont Forget</div>
    <div class="tb-spacer"></div><span class="workspace-name">个人工作区</span>
    <span class="demo-label">交互原型</span>
    <button class="iconbtn" id="themeBtn" title="切换主题" aria-label="切换主题"></button>
  </header>
  <div class="body" id="body">
    <button class="sidebar-shade" id="sidebarShade" aria-label="关闭导航"></button>
    <aside class="sidebar" id="sidebar" aria-label="工作区导航"></aside>
    <main class="main" id="main"></main>
    <aside class="rail" id="rail" aria-label="上下文详情" hidden></aside>
  </div>''' + html[end:]
html = html.replace('<div class="palette">', '<div class="palette" role="dialog" aria-modal="true" aria-label="搜索会话、记忆和功能">')
html = html.replace('<span id="i-search2"></span><input id="paletteInput" placeholder="输入命令或搜索…" />', '<span id="i-search2"></span><input id="paletteInput" aria-label="搜索会话、记忆和功能" placeholder="搜索会话、记忆和功能…" /><button class="iconbtn" id="paletteClose" aria-label="关闭搜索">×</button>')
html = html.replace('<div class="list" id="paletteList"></div>', '<div class="list" id="paletteList"></div><div class="palette-footer">↑ ↓ 选择　Enter 打开　Esc 关闭</div>')
html = html.replace('<div class="toast" id="toast">', '<div class="toast" id="toast" role="status">')
html = html.replace("  page: 'new',", "  page: 'new',\n  sessionId: null,\n  conversations: {},\n  railOpen: false,\n  memoryEnabled: true,\n  model: 'claude-opus-4.5',\n  draft: '',\n  attachments: [],")

def replace_function(name, next_name, body):
    global html
    start = html.index('function ' + name + '(')
    end = html.index('function ' + next_name + '(', start)
    html = html[:start] + body.strip() + '\n\n' + html[end:]

replace_function('navTo', 'renderSidebar', '''
function navTo(page){
  if(page==='new'){state.sessionId=null;state.draft='';state.attachments=[];}
  document.body.classList.remove('sidebar-open');
  if(location.hash==='#/'+page) render();
  else location.hash='#/'+page;
}
function openSession(index){
  const session=SESSIONS[index];
  if(!session) return;
  state.sessionId=index;
  if(!state.conversations[index]){
    state.conversations[index]=index===0 ? state.initialChat.map(m=>({...m})) : [
      {who:'user',text:session.t},
      {who:'ai',html:`<p>这是「${esc(session.t)}」的示例会话。可以继续输入，查看对话交互。</p><p class="tiny muted">当前为界面原型，未接入模型或真实历史记录。</p>`}
    ];
  }
  state.chat=state.conversations[index];state.draft='';state.attachments=[];
  navTo('chat');
}
function toggleSidebar(){
  if(matchMedia('(max-width:760px)').matches) document.body.classList.toggle('sidebar-open');
  else document.body.classList.toggle('sidebar-collapsed');
}
function toolbar(title){
  return `<header class="mainbar"><button class="iconbtn" data-sidebar aria-label="展开或收起导航" title="展开或收起导航">${icon('folder',16)}</button><h1>${esc(title)}</h1><span class="spacer"></span>${['new','chat'].includes(state.page)?`<button class="btn ghost sm" data-rail aria-expanded="${state.railOpen}">${icon('brain',14)} 记忆${state.railOpen?' · 收起':''}</button>`:''}</header>`;
}
function attachmentMarkup(){
  return state.attachments.map((name,i)=>`<span class="attachment-item">${icon('file',12)} ${esc(name)} <button data-remove-file="${i}" aria-label="移除 ${esc(name)}">×</button></span>`).join('');
}
function composer(mode){
  return `<div class="composer">
    <div class="attachment-list" id="attachmentList">${attachmentMarkup()}</div>
    <textarea id="${mode}Input" aria-label="${mode==='new'?'新对话消息':'继续对话'}" placeholder="${mode==='new'?'发送消息，或告诉我需要记住的事…':'继续对话…'}" rows="2">${esc(state.draft)}</textarea>
    <div class="bar">
      <button class="iconbtn" data-attach title="添加附件" aria-label="添加附件">${icon('plus',17)}</button>
      <input type="file" id="fileInput" multiple hidden />
      <select class="model-select" id="modelSelect" aria-label="对话模型（演示）">${['claude-opus-4.5','claude-sonnet-4.5','gpt-5.1'].map(m=>`<option ${state.model===m?'selected':''}>${m}</option>`).join('')}</select>
      <button class="pill ${state.memoryEnabled?'memory-on':''}" id="memoryToggle" aria-pressed="${state.memoryEnabled}" title="切换本次对话记忆">${icon('brain',13)} 记忆${state.memoryEnabled?'开启':'关闭'}</button>
      <button class="send" id="${mode}Send" aria-label="发送消息" title="发送消息" ${state.draft.trim()?'':'disabled'}>${icon('send',15)}</button>
    </div>
  </div>`;
}
function memoryRail(){
  return `<div class="head"><div class="row between"><h2 class="h2">${state.page==='chat'?'相关记忆':'记忆'}</h2><button class="iconbtn" data-rail aria-label="关闭记忆侧栏">×</button></div><p class="sub" style="margin-top:8px">${state.memoryEnabled?'以下为示例记忆，可查看来源和详情。':'本次对话已关闭记忆。'}</p></div>
    <div class="scroll">${state.memoryEnabled?MEMORIES.slice(0,4).map(m=>`<button class="railitem" data-open-memory="${m.id}"><span class="tag acc">${m.type}</span><div class="t" style="margin-top:8px">${esc(m.title)}</div><div class="meta">${esc(m.src)}</div></button>`).join(''):''}<button class="btn sm" data-go="memory">查看全部记忆 ${icon('arrow',12)}</button></div>`;
}
''')
replace_function('renderSidebar', 'pageNew', '''
function renderSidebar(){
  let lastGroup='';
  const sessions=SESSIONS.map((s,i)=>{
    const group=s.m.split(' · ')[0];
    const heading=group!==lastGroup?`<div class="session-group">${esc(group)}</div>`:'';lastGroup=group;
    return heading+`<button class="session ${state.page==='chat'&&state.sessionId===i?'active':''}" data-session="${i}" title="${esc(s.t)}" ${state.page==='chat'&&state.sessionId===i?'aria-current="page"':''}>${icon('chat',14)}<span class="t">${esc(s.t)}</span></button>`;
  }).join('');
  $('#sidebar').innerHTML=`
    <div class="sidebar-caption"><span class="workspace-icon">${icon('folder',14)}</span>个人工作区</div>
    <button class="newchat" data-go="new">${icon('plus',15)} 新对话</button>
    <nav class="nav" aria-label="常用功能">
      <button id="paletteBtn">${icon('search',16)} 搜索<span class="badge">⌘ K</span></button>
      <a href="#/memory" class="${state.page==='memory'?'active':''}">${icon('brain',16)} 记忆库<span class="badge">${MEMORIES.length}</span></a>
      <a href="#/automations" class="${state.page==='automations'?'active':''}">${icon('clock',16)} 自动化</a>
    </nav>
    <div class="sect">最近会话</div><div class="sessions">${sessions}</div>
    <div class="sidebottom"><nav class="nav" aria-label="配置与工具">
      <a href="#/agents" class="${['agents','skills','identity'].includes(state.page)?'active':''}">${icon('bot',16)} 助手与工具</a>
      <a href="#/settings" class="${state.page==='settings'?'active':''}">${icon('gear',16)} 设置</a>
    </nav><div class="sidefoot">${icon('lock',12)} 演示数据 · 未连接服务</div></div>`;
  $('#paletteBtn').onclick=openPalette;
}
''')
replace_function('pageNew', 'pageChat', '''
function pageNew(){
  return {main:`<div class="scroll new-scroll"><div class="new-page">
    <div class="greeting"><h1>有什么需要一起处理？</h1><p>聊工作、整理想法，也可以继续之前的事。</p></div>
    ${composer('new')}
    <div class="composer-hint">Enter 发送 · Shift + Enter 换行</div>
    <div class="quick-actions">
      ${[['clock','回顾这周的决策'],['file','整理会议要点'],['brain','记住一个新偏好']].map(([ico,t])=>`<button data-prompt="${esc(t)}">${icon(ico,13)}${esc(t)}</button>`).join('')}
    </div>
    <section aria-label="最近会话"><div class="recent-heading"><h2>继续最近的对话</h2><span>最近更新</span></div>
      ${SESSIONS.slice(0,3).map((s,i)=>`<button class="recent-row" data-session="${i}">${icon('chat',15)}<span class="recent-title">${esc(s.t)}</span><time>${esc(s.m.split(' · ')[0])}</time>${icon('chevron',12)}</button>`).join('')}
    </section>
  </div></div>`,rail:state.railOpen?memoryRail():null};
}
''')
replace_function('pageChat', 'pageMemory', '''
function pageChat(){
  const msgs=state.chat.map(m=>m.who==='user'?`
    <article class="msg user"><div class="who" aria-hidden="true">我</div><div class="content"><div class="name">你</div><div class="bubble">${esc(m.text)}</div>${m.files?.length?`<div class="tiny muted" style="margin-top:8px">${icon('file',12)} ${m.files.map(esc).join('、')} · 附件演示</div>`:''}</div></article>`:`
    <article class="msg ai"><div class="who" aria-hidden="true">${icon('spark',15)}</div><div class="content"><div class="name">Dont Forget <span class="tag">示例回复</span></div><div class="prose">${m.html||esc(m.text)}</div></div></article>`).join('');
  return {main:`<div class="scroll"><div class="thread" id="thread">${msgs}</div></div><div class="threadfoot">${composer('chat')}<div class="composer-hint">交互原型，未调用模型 · Enter 发送，Shift + Enter 换行</div></div>`,rail:state.railOpen?memoryRail():null};
}
''')
# Reword page headings into normal destinations instead of promotional copy.
for old,new in {
    '你的助手如何理解你':'个人偏好与身份',
    '能力按需打开':'技能',
    '让助手自己记得':'自动化',
    '<h1 class="h1">Agents</h1>':'<h1 class="h1">助手</h1>',
    '标题 Fraunces · 正文 Inter':'系统字体 · 跟随操作系统',
    '默认按你的偏好设为紧凑。':'当前使用标准桌面密度。',
    '桌面端通过本地 WebSocket 连接，数据不出本机。':'正式桌面端将连接本地服务；模型的数据处理方式取决于所选提供商。',
    '127.0.0.1:8765 · 已连接':'127.0.0.1:8765 · 示例地址，未连接',
    '<span class="tag ok">运行中</span>':'<span class="tag">演示</span>',
    '<div class="tag ok">已同步</div>':'<div class="tag">示例</div>',
    '955 passed</span> in 7.33s':'示例输出：955 passed</span> in 7.33s',
}.items(): html=html.replace(old,new)
html=html.replace('<div class="switch ', '<div class="switch ')
html=html.replace('<input class="input" id="memSearch" placeholder="搜索记忆…" />','<input class="input" id="memSearch" aria-label="搜索记忆" placeholder="搜索记忆…" />')
html=html.replace("const sections = {appearance:'外观',models:'模型',voice:'语音',browser:'浏览器',channels:'通道',runtime:'运行时',advanced:'高级'};", "const sections = {appearance:'外观',models:'模型服务',tools:'MCP 与工具',channels:'连接',advanced:'高级'};")
html=html.replace("  } else if(state.settings==='channels'){", "  } else if(state.settings==='tools'){\n    content=`<h1 class=\"h1\">MCP 与工具</h1><p class=\"sub\" style=\"margin-bottom:20px\">按需配置外部工具。当前原型未连接 MCP 服务。</p><div class=\"card pad\"><div class=\"row between\"><div><b>未添加 MCP 服务</b><p class=\"sub\">连接后，助手可在对话中调用相应工具。</p></div><span class=\"tag\">未连接</span></div></div>`;\n  } else if(state.settings==='channels'){")
html=html.replace("  const out = PAGES[state.page]();\n  $('#main').innerHTML = out.main;", "  if(state.page==='chat'&&state.sessionId===null){state.sessionId=0;state.chat=state.conversations[0]||state.initialChat.map(m=>({...m}));state.conversations[0]=state.chat;}\n  const out = PAGES[state.page]();\n  const titles={new:'新对话',chat:SESSIONS[state.sessionId]?.t||'对话',memory:'记忆库',identity:'个人偏好',agents:'助手与工具',skills:'技能',automations:'自动化',workbench:'开发工具',settings:'设置',connect:'连接'};\n  const subnav=['agents','skills','identity','workbench'].includes(state.page)?`<nav class=\"tabs\" aria-label=\"助手与工具\">${[['agents','助手'],['skills','技能'],['identity','个人偏好'],['workbench','开发工具']].map(([k,t])=>`<button class=\"tab ${state.page===k?'on':''}\" data-go=\"${k}\">${t}</button>`).join('')}<button class=\"tab\" data-mcp>MCP 设置</button></nav>`:'';\n  $('#main').innerHTML = toolbar(titles[state.page])+subnav+out.main;")

# Replace send/suggestion handlers with reusable, accurately labelled demo interaction.
start=html.index('  // suggestions', html.index('function wire()'))
end=html.index('  // memory filters',start)
html=html[:start]+'''
  document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>navTo(b.dataset.go));
  document.querySelectorAll('[data-session]').forEach(b=>b.onclick=()=>openSession(+b.dataset.session));
  document.querySelectorAll('[data-sidebar]').forEach(b=>b.onclick=toggleSidebar);
  document.querySelectorAll('[data-rail]').forEach(b=>b.onclick=()=>{state.railOpen=!state.railOpen;render();});
  document.querySelectorAll('[data-open-memory]').forEach(b=>b.onclick=()=>{state.memoryId=b.dataset.openMemory;navTo('memory');});
  document.querySelectorAll('[data-mcp]').forEach(b=>b.onclick=()=>{state.settings='tools';navTo('settings');});
  document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{
    state.draft=b.dataset.prompt+'：';const input=$('#newInput');input.value=state.draft;$('#newSend').disabled=false;input.focus();
  });
  const input=$('#newInput')||$('#chatInput');
  const send=$('#newSend')||$('#chatSend');
  if(input){
    input.oninput=()=>{state.draft=input.value;send.disabled=!state.draft.trim();};
    input.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();sendMessage();}};
    send.onclick=sendMessage;
  }
  const select=$('#modelSelect');if(select) select.onchange=()=>{state.model=select.value;};
  const memoryToggle=$('#memoryToggle');if(memoryToggle) memoryToggle.onclick=()=>{state.memoryEnabled=!state.memoryEnabled;render();};
  const fileInput=$('#fileInput');
  document.querySelectorAll('[data-attach]').forEach(b=>b.onclick=()=>fileInput.click());
  if(fileInput) fileInput.onchange=()=>{
    state.attachments=[...state.attachments,...Array.from(fileInput.files,f=>f.name)];
    render();toast('已添加附件名称（原型不读取或上传文件）');
  };
  document.querySelectorAll('[data-remove-file]').forEach(b=>b.onclick=()=>{state.attachments.splice(+b.dataset.removeFile,1);render();});
''' + html[end:]
html=html.replace("    el.classList.toggle('on'); toast(state.skillsOn.includes(n)", "    el.classList.toggle('on'); el.setAttribute('aria-checked',el.classList.contains('on')); toast(state.skillsOn.includes(n)")
html=html.replace("    el.classList.toggle('on'); toast(state.automationsOn.includes(n)", "    el.classList.toggle('on'); el.setAttribute('aria-checked',el.classList.contains('on')); toast(state.automationsOn.includes(n)")
html=html.replace("  const cb = $('#compileBtn');", "  document.querySelectorAll('.switch').forEach(el=>{el.setAttribute('role','switch');el.setAttribute('tabindex','0');el.setAttribute('aria-checked',el.classList.contains('on'));el.setAttribute('aria-label',el.dataset.skill||el.dataset.auto||el.closest('.field')?.querySelector('b')?.textContent||'切换选项');el.onkeydown=e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();el.click();el.setAttribute('aria-checked',el.classList.contains('on'));}};});\n  const cb = $('#compileBtn');")

insert=html.index('/* ---------------- theme ---------------- */')
html=html[:insert]+'''
function sendMessage(){
  const text=state.draft.trim();if(!text)return;
  if(state.sessionId===null){
    state.sessionId=SESSIONS.length;SESSIONS.push({t:text.length>26?text.slice(0,26)+'…':text,m:'今天 · 新对话'});
    state.chat=[];state.conversations[state.sessionId]=state.chat;
  }
  state.chat.push({who:'user',text,files:[...state.attachments]});
  state.chat.push({who:'ai',html:`<p>这是一条示例回复。你可以继续发送消息、切换会话，或打开右侧记忆栏查看来源。</p><p class="tiny muted">当前为交互原型，没有调用模型、检索记忆或保存数据。</p>`});
  state.draft='';state.attachments=[];navTo('chat');
  requestAnimationFrame(()=>{const sc=$('#main .scroll');if(sc)sc.scrollTop=sc.scrollHeight;$('#chatInput')?.focus();});
}

''' + html[insert:]
html=html.replace("  $('#themeBtn').innerHTML = icon(t==='light'?'moon':'sun',16);", "  $('#themeBtn').innerHTML = icon(t==='light'?'moon':'sun',16);\n  try{localStorage.setItem('dontforget-demo-theme',t);}catch{}")

start=html.index("let paletteSel = 0;")
end=html.index('/* ---------------- boot ---------------- */', start)
html=html[:start]+'''
let paletteSel=0;
let paletteReturnFocus=null;
function paletteItems(q=''){
  return [
    ...SESSIONS.map((s,i)=>({label:s.t,kind:'会话',session:i})),
    ...MEMORIES.map(m=>({label:m.title,kind:'记忆',memory:m.id})),
    ...PALETTE.map(p=>({...p,kind:'功能'}))
  ].filter(p=>p.label.toLowerCase().includes(q.toLowerCase()));
}
function renderPalette(q=''){
  const items=paletteItems(q);paletteSel=Math.max(0,Math.min(paletteSel,items.length-1));
  $('#paletteList').innerHTML=items.map((p,i)=>`<button class="item ${i===paletteSel?'sel':''}" data-pal="${i}">${icon(p.kind==='记忆'?'brain':p.kind==='会话'?'chat':'chevron',14)}<span>${esc(p.label)}</span><span class="k">${p.kind}</span></button>`).join('')||'<div class="item">没有匹配的会话、记忆或功能</div>';
  $('#paletteList').querySelectorAll('[data-pal]').forEach(el=>el.onclick=()=>runPalette(items[+el.dataset.pal]));
  $('#paletteList .sel')?.scrollIntoView({block:'nearest'});
  return items;
}
function runPalette(p){
  if(!p)return;closePalette();
  if(p.session!==undefined){openSession(p.session);return;}
  if(p.memory){state.memoryId=p.memory;state.memoryFilter='全部';navTo('memory');return;}
  if(p.action==='theme'){setTheme(state.theme==='light'?'dark':'light');render();return;}
  if(p.go)navTo(p.go);
}
function openPalette(){paletteReturnFocus=document.activeElement;paletteSel=0;$('#overlay').classList.add('on');$('#paletteInput').value='';renderPalette();$('#paletteInput').focus();}
function closePalette(){$('#overlay').classList.remove('on');if(paletteReturnFocus?.isConnected)paletteReturnFocus.focus();}

''' + html[end:]
start=html.index("document.addEventListener('DOMContentLoaded'")
html=html[:start]+'''
document.addEventListener('DOMContentLoaded',()=>{
  state.initialChat=state.chat.map(m=>({...m}));
  $('#brandMark').innerHTML=icon('brain',16);
  $('#i-search2').innerHTML=icon('search',16);
  let theme='light';try{theme=localStorage.getItem('dontforget-demo-theme')||theme;}catch{}
  setTheme(theme==='dark'?'dark':'light');
  $('#themeBtn').onclick=()=>{setTheme(state.theme==='light'?'dark':'light');render();};
  $('#sidebarShade').onclick=()=>document.body.classList.remove('sidebar-open');
  $('#paletteClose').onclick=closePalette;
  $('#overlay').onclick=e=>{if(e.target.id==='overlay')closePalette();};
  $('#paletteInput').oninput=e=>{paletteSel=0;renderPalette(e.target.value.trim());};
  document.addEventListener('keydown',e=>{
    if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openPalette();return;}
    if(e.key==='Escape'){closePalette();document.body.classList.remove('sidebar-open');if(state.railOpen){state.railOpen=false;render();}return;}
    if(!$('#overlay').classList.contains('on'))return;
    const q=$('#paletteInput').value.trim();const items=paletteItems(q);
    if(e.key==='ArrowDown'){e.preventDefault();paletteSel=Math.min(paletteSel+1,items.length-1);renderPalette(q);}
    if(e.key==='ArrowUp'){e.preventDefault();paletteSel=Math.max(paletteSel-1,0);renderPalette(q);}
    if(e.key==='Enter'&&e.target.id==='paletteInput'){e.preventDefault();runPalette(items[paletteSel]);}
    if(e.key==='Tab'){
      const focusables=[...$('#overlay').querySelectorAll('input,button')];const i=focusables.indexOf(document.activeElement);
      if(e.shiftKey&&i===0){e.preventDefault();focusables.at(-1).focus();}
      else if(!e.shiftKey&&i===focusables.length-1){e.preventDefault();focusables[0].focus();}
    }
  });
  window.addEventListener('hashchange',()=>{document.body.classList.remove('sidebar-open');render();});
  render();
});
</script>
</body>
</html>
'''
target.write_text(html)
print(f'Updated {target}: {len(original)} → {len(html)} characters')
