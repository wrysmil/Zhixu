import { chromium } from 'file:///D:/studyspace/project/Zhixu/webui/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
const url='http://127.0.0.1:5186/@fs/D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience/.ai-runtime-artifacts/verifications/knowledge-experience/fixture.html';
const results=[];const log=(n,s,e)=>{results.push({name:n,status:s,evidence:e});console.log(JSON.stringify({name:n,status:s,...(typeof e==='string'?{error:e}:e)}));};
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const ctx=await browser.newContext({viewport:{width:1440,height:900}});
const page=await ctx.newPage(); page.setDefaultTimeout(8000);
const open=async(sec='knowledge')=>{await page.goto('about:blank');await page.goto(`${url}#/${sec}`,{waitUntil:'domcontentloaded'});await page.getByRole('banner').waitFor();await page.locator(`[data-testid="${sec==='knowledge'?'knowledge-page':'knowledge-eval-page'}"]`).waitFor();await page.waitForTimeout(600);};

// 1) 纯键盘：Tab 进入浮层、焦点陷阱、focus-visible 可见、Esc 关闭并归还焦点
try{
 await open();
 await page.locator('[data-kb-id="kb-product"]').click(); await page.waitForTimeout(400);
 const trigger=page.getByRole('button',{name:'部署与回滚操作指南',exact:true});
 await trigger.focus();
 const before=await page.evaluate(()=>document.activeElement?.textContent?.trim());
 await page.keyboard.press('Enter');
 await page.getByRole('dialog').waitFor();
 await page.waitForTimeout(300);
 const inside=await page.evaluate(()=>{const d=document.querySelector('[role=dialog]');return {inDialog:d.contains(document.activeElement),active:document.activeElement?.tagName+'|'+(document.activeElement?.getAttribute('aria-label')||document.activeElement?.textContent||'').trim().slice(0,40)};});
 // Shift+Tab 从第一个可聚焦元素回绕
 const first=await page.evaluate(()=>{const d=document.querySelector('[role=dialog]');const f=[...d.querySelectorAll('button,input,select,textarea,a[href],[tabindex]:not([tabindex="-1"])')].filter(e=>!e.disabled&&e.offsetParent!==null);return f.length?f[0].getAttribute('aria-label')||f[0].textContent.trim().slice(0,30):null;});
 await page.keyboard.down('Shift'); await page.keyboard.press('Tab'); await page.keyboard.up('Shift');
 const wrapped=await page.evaluate(()=>{const d=document.querySelector('[role=dialog]');return d.contains(document.activeElement);});
 // focus-visible：键盘聚焦后检查 outline
 const outline=await page.evaluate(()=>{const a=document.activeElement;const cs=getComputedStyle(a);return {tag:a.tagName,outlineStyle:cs.outlineStyle,outlineWidth:cs.outlineWidth,outlineColor:cs.outlineColor};});
 await page.keyboard.press('Escape');
 await page.getByRole('dialog').waitFor({state:'hidden'}); await page.waitForTimeout(300);
 const after=await page.evaluate(()=>({text:document.activeElement?.textContent?.trim().slice(0,40),connected:document.activeElement?.isConnected}));
 log('纯键盘：Tab进入、焦点陷阱、focus-visible、Esc归还焦点',(inside.inDialog&&wrapped&&after.connected&&after.text===before)?'通过':'未通过',{triggerText:before,inside,wrapStayedInDialog:wrapped,outline,afterEscape:after});
}catch(e){log('纯键盘：Tab进入、焦点陷阱、focus-visible、Esc归还焦点','未通过',{error:String(e).split('\n')[0]});}

// 2) 连续开关 20 次（知识页文档 Sheet）
for(const target of [
 {key:'知识页文档Sheet',sec:'knowledge',open:async()=>{await page.locator('[data-kb-id="kb-product"]').click();await page.waitForTimeout(400);await page.getByRole('button',{name:'部署与回滚操作指南',exact:true}).focus();},label:'部署与回滚操作指南'},
 {key:'评测新建Sheet',sec:'knowledge-eval',open:async()=>{await page.getByRole('button',{name:'新建评测',exact:true}).focus();},label:'新建评测'},
]){
 try{
  await open(target.sec);
  await target.open();
  const samples=[];
  for(let i=0;i<20;i++){
   await page.keyboard.press('Enter');
   await page.getByRole('dialog').waitFor(); await page.waitForTimeout(120);
   const focused=await page.evaluate(()=>{const d=document.querySelector('[role=dialog]');return d?d.contains(document.activeElement):false;});
   await page.keyboard.press('Escape');
   await page.getByRole('dialog').waitFor({state:'hidden'}); await page.waitForTimeout(220);
   const st=await page.evaluate(()=>({connected:document.activeElement?.isConnected,focus:(document.activeElement?.textContent||'').trim().slice(0,30),locked:document.body.hasAttribute('data-scroll-locked'),overflow:getComputedStyle(document.body).overflow,pointer:getComputedStyle(document.body).pointerEvents,overlays:document.querySelectorAll('.knowledge-overlay').length,dialogs:document.querySelectorAll('[role=dialog]').length}));
   samples.push({i,focused,...st});
  }
  const bad=samples.filter(s=>!s.focused||!s.connected||s.locked||s.pointer==='none'||s.overlays!==0||s.dialogs!==0||s.focus!==target.label);
  log(`连续开关20次：${target.key}`,bad.length?'未通过':'通过',{badCount:bad.length,bad:bad.slice(0,3),sampleFocus:[...new Set(samples.map(s=>s.focus))]});
 }catch(e){log(`连续开关20次：${target.key}`,'未通过',{error:String(e).split('\n')[0]});}
}
fs.writeFileSync(process.env.TEMP+'/wu05/probe-keyboard.json',JSON.stringify(results,null,1));
await browser.close();