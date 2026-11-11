import { chromium } from '../../../webui/node_modules/playwright-core/index.mjs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';

const dir = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(dir, 'acceptance');
await fs.mkdir(out, {recursive:true});
const url = 'http://127.0.0.1:5186/@fs/D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience/.ai-runtime-artifacts/verifications/knowledge-experience/fixture.html';
const mode = process.argv[2] ?? 'probe';
const results = [];
const save = async () => fs.writeFile(path.join(out, `${mode}-results.json`), JSON.stringify(results,null,2));
if(mode==='stability') {
  const cwd=path.resolve(dir,'../../../webui');
  for(let i=1;i<=3;i++) {
    const start=Date.now();
    const args=['node_modules/vitest/vitest.mjs','run','--config','../.ai-runtime-artifacts/verifications/knowledge-experience/vite.config.ts',...['knowledge-page','knowledge-eval-page','knowledge-experience','knowledge-graph','knowledge-graph-view'].map(n=>`src/tests/${n}.test.tsx`)];
    const child=spawn(process.execPath,args,{cwd,env:{...process.env,ESBUILD_BINARY_PATH:path.join(dir,'esbuild.exe')},windowsHide:true});
    let log=''; child.stdout.on('data',b=>log+=b);child.stderr.on('data',b=>log+=b);
    const code=await new Promise(resolve=>child.on('exit',resolve));
    await fs.writeFile(path.join(out,`stability-${i}.log`),log);
    results.push({round:i,code,durationMs:Date.now()-start,summary:log.replace(/\u001b\[[0-9;]*m/g,'').split('\n').filter(l=>/Test Files|Tests |Duration|failed|Error/.test(l))});
    await save(); console.log(JSON.stringify(results.at(-1)));
  }
  process.exit(results.every(r=>r.code===0)?0:1);
}
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:900}, ...(mode==='video'?{recordVideo:{dir:out,size:{width:1440,height:900}}}:{})});
const page=await context.newPage();
page.setDefaultTimeout(7000);
const errors=[],network=[];
page.on('pageerror',e=>errors.push(String(e)));
page.on('request',r=>network.push({method:r.method(),url:r.url()}));
await context.route('**/*',route=>{
  const u=new URL(route.request().url());
  return u.origin==='http://127.0.0.1:5186'?route.continue():route.abort('blockedbyclient');
});
const pause=ms=>page.waitForTimeout(ms);
async function open(section='knowledge') { await page.goto(`${url}#/${section}`,{waitUntil:'domcontentloaded'}); await page.getByRole('banner').waitFor(); await page.locator(`[data-testid="${section==='knowledge'?'knowledge-page':'knowledge-eval-page'}"]`).waitFor(); await pause(500); }
async function snap(name) { await page.screenshot({path:path.join(out,`${name}.png`),fullPage:true}); }
async function state() { return page.evaluate(()=>({title:document.title,text:document.body.innerText,focus:{tag:document.activeElement?.tagName,text:document.activeElement?.textContent?.slice(0,100),connected:document.activeElement?.isConnected},dialogs:[...document.querySelectorAll('[role=dialog]')].map(e=>e.textContent),overflow:{client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth},audit:window.__knowledgeFixture.audit})); }
async function check(name,fn) { try {const evidence=await fn();results.push({name,status:'通过',evidence});}catch(e){results.push({name,status:'未通过',error:String(e)});await snap(`failure-${results.length}`);} await save();console.log(JSON.stringify(results.at(-1))); }
const library=()=>page.locator('[data-kb-id="kb-product"]');
const doc=()=>page.getByRole('button',{name:'部署与回滚操作指南',exact:true});
const sheet=()=>page.getByRole('dialog');
async function enterLibrary() {await library().click();await doc().waitFor();}
async function closeSheet() {await page.keyboard.press('Escape');await sheet().waitFor({state:'hidden'});await pause(260);}
try {
 if(mode==='probe') {await open();await enterLibrary();console.log(JSON.stringify(await state()));await open('knowledge-eval');console.log(JSON.stringify(await state()));}
 if(mode==='matrix') {
  for(const dark of [false,true])for(const [width,height] of [[1440,900],[1280,800],[390,844]])for(const reducedMotion of ['no-preference','reduce']) {
   const key=`${dark?'dark':'light'}-${width}x${height}-${reducedMotion}`;
   await page.setViewportSize({width,height});await page.emulateMedia({reducedMotion,colorScheme:dark?'dark':'light'});
   await check(key,async()=>{
    await open();if(dark)await page.getByRole('button',{name:'切换深色',exact:true}).click();
    const states=[];
    for(const stage of ['libraries','documents','document-sheet','eval-runs','eval-report','eval-case']) {
     if(stage==='documents')await enterLibrary();
     if(stage==='document-sheet'){await doc().click();await sheet().waitFor();}
     if(stage==='eval-runs'){await closeSheet();await page.getByRole('link',{name:'检索评测',exact:true}).click();await page.getByTestId('knowledge-eval-run-run-candidate').waitFor();}
     if(stage==='eval-report'){await page.getByTestId('knowledge-eval-run-run-candidate').getByRole('button',{name:'查看报告'}).click();await page.getByTestId('knowledge-eval-case-case-null::base').waitFor();}
     if(stage==='eval-case'){await page.getByTestId('knowledge-eval-case-case-null::base').getByRole('button').click();await sheet().waitFor();}
     await pause(300);await snap(`${key}-${stage}`);
     const current=await page.evaluate(()=>({stage:null,client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,dark:document.documentElement.classList.contains('dark')}));
     states.push({...current,stage});
    }
    await closeSheet();
    assert(states.every(s=>s.scroll<=s.client+1),JSON.stringify(states));
    return states;
   });
  }
 }
 if(mode==='behavior') {
  await check('摘要指针移入保持、Esc关闭、焦点打开详情',async()=>{
   await open();await library().hover();const tip=page.locator('.knowledge-preview').first();await tip.waitFor();
   const b=await tip.boundingBox();assert(b);await page.mouse.move(b.x+b.width/2,b.y+b.height/2,{steps:12});
   const visible=[];for(let i=0;i<10;i++){visible.push(await tip.isVisible());await pause(50);}assert(visible.every(Boolean));
   await page.keyboard.press('Escape');await tip.waitFor({state:'hidden'});
   await library().focus();await page.keyboard.press('Enter');await doc().waitFor();return {visible};
  });
  await check('文档Sheet焦点进入、关闭回原触发器及20次开关',async()=>{
   await open();await enterLibrary();const samples=[];
   for(let i=0;i<20;i++){
    await doc().focus();await page.keyboard.press('Enter');await sheet().waitFor();await pause(100);
    const focused=await sheet().evaluate(e=>e.contains(document.activeElement));
    await closeSheet();const after=await page.evaluate(()=>({connected:document.activeElement?.isConnected,focus:document.activeElement?.textContent,locked:document.body.hasAttribute('data-scroll-locked'),overflow:getComputedStyle(document.body).overflow,pointer:getComputedStyle(document.body).pointerEvents,overlays:document.querySelectorAll('[data-knowledge-overlay],[data-radix-dialog-overlay]').length}));
    samples.push({i,focused,...after});
   }
   assert(samples.every(s=>s.focused&&s.connected&&s.focus==='部署与回滚操作指南'&&!s.locked&&s.pointer!=='none'&&s.overlays===0),JSON.stringify(samples));return samples;
  });
  await check('添加资料Sheet键盘与本地文件/服务器路径',async()=>{
   await open();await enterLibrary();const add=page.getByRole('button',{name:'添加资料',exact:true});await add.focus();await page.keyboard.press('Enter');await sheet().waitFor();
   assert(await sheet().evaluate(e=>e.contains(document.activeElement)));
   await page.locator('input[type=file]').setInputFiles({name:'fixture-upload.txt',mimeType:'text/plain',buffer:Buffer.from('测试数据：隔离导入。')});await pause(600);
   if(await sheet().isVisible())await closeSheet();
   await add.click();await page.getByRole('tab',{name:'服务器路径'}).click();await sheet().locator('input').fill('/fixture-only/example.txt');await sheet().getByRole('button',{name:'添加',exact:true}).click();await pause(600);
   const audit=await page.evaluate(()=>window.__knowledgeFixture.audit.filter(e=>e.kind==='mutation'));
   assert(audit.some(e=>e.method==='knowledge.doc.add'));assert(audit.some(e=>e.method==='knowledge.doc.add_file'));
   if(await sheet().isVisible())await closeSheet();return audit;
  });
  await check('空库、筛选为空、请求失败可区分',async()=>{
   await open();await page.locator('[data-kb-id="kb-empty"]').click();await pause(350);const empty=(await state()).text;await snap('state-empty-library');
   await page.getByRole('button',{name:'返回知识库',exact:true}).click();await page.getByRole('textbox',{name:'搜索知识库名称'}).fill('无匹配测试');const filtered=(await state()).text;await snap('state-filter-empty');
   await open();await enterLibrary();await page.evaluate(()=>{window.__knowledgeFixture.controls.failNext='/search';});await page.getByRole('tab',{name:'检索预览'}).click();await page.getByRole('textbox').fill('回滚');await page.getByRole('button',{name:'搜索',exact:true}).click();await page.getByRole('alert').waitFor();const failed=(await state()).text;await snap('state-request-failure');
   assert(empty!==filtered&&filtered!==failed&&failed.includes('FIXTURE_INJECTED_FAILURE'));return {empty,filtered,failed};
  });
  await check('处理中无虚假百分比，失败原因可见且重试',async()=>{
   await open();await enterLibrary();const processing=page.getByRole('row').filter({hasText:'正在处理的测试文档'});assert(!/%/.test(await processing.innerText()));
   const failed=page.getByRole('row').filter({hasText:'无法解析的测试附件'});assert((await failed.innerText()).includes('无法解析'));
   await failed.getByRole('button',{name:'重建索引',exact:true}).click();await pause(400);
   const audit=await page.evaluate(()=>window.__knowledgeFixture.audit.filter(e=>e.kind==='mutation'));assert(audit.some(e=>e.method==='knowledge.doc.reindex'));return audit;
  });
  await check('快速切换Tab10次请求计数',async()=>{
   await open();await enterLibrary();const before=await page.evaluate(()=>window.__knowledgeFixture.audit.length);
   for(let i=0;i<10;i++)await page.getByRole('tab',{name:i%2?'文档':'检索预览',exact:true}).click();
   await pause(450);const requests=await page.evaluate(n=>window.__knowledgeFixture.audit.slice(n),before);
   assert.equal(await page.getByRole('tab',{name:'文档',exact:true}).getAttribute('aria-selected'),'true');assert.equal(requests.length,0);return requests;
  });
  await check('快速切换知识库10次请求计数与最终归属',async()=>{
   await open();await page.evaluate(()=>{window.__knowledgeFixture.controls.delayMs=120;});const before=await page.evaluate(()=>window.__knowledgeFixture.audit.length);
   for(let i=0;i<10;i++){await page.locator(`[data-kb-id="${i%2?'kb-team':'kb-product'}"]`).click();await page.getByRole('button',{name:'返回知识库',exact:true}).click();}
   await pause(450);await page.locator('[data-kb-id="kb-team"]').click();await pause(450);
   const requests=await page.evaluate(n=>window.__knowledgeFixture.audit.slice(n),before);const text=(await state()).text;
   assert(text.includes('团队协作约定')&&!text.includes('无法解析的测试附件'));const docRequests=requests.filter(r=>r.path.endsWith('/docs'));assert(docRequests.length<=22,JSON.stringify(docRequests));return {requests,text};
  });
  await check('图谱节点选择与展开关联',async()=>{
   await open();await enterLibrary();await page.getByRole('tab',{name:'图谱',exact:true}).click();await page.getByTestId('graph-entity-card').first().click();await page.locator('[data-graph-node]').first().waitFor();
   const node=page.locator('[data-graph-node]').nth(1);await node.focus();await page.keyboard.press('Enter');assert.equal(await node.getAttribute('aria-pressed'),'true');await page.getByRole('button',{name:'展开关联',exact:true}).click();await pause(350);
   await snap('graph-selected-expanded');const audit=await page.evaluate(()=>window.__knowledgeFixture.audit.filter(r=>r.path.includes('/graph/')));assert(audit.filter(r=>r.path.endsWith('/subgraph')).length>=2);return audit;
  });
  await check('评测运行、题集与单题Sheet键盘焦点',async()=>{
   await open('knowledge-eval');const newRun=page.getByRole('button',{name:'新建评测',exact:true});await newRun.focus();await page.keyboard.press('Enter');await sheet().waitFor();assert(await sheet().evaluate(e=>e.contains(document.activeElement)));await snap('eval-run-sheet');await closeSheet();assert(await newRun.evaluate(e=>e===document.activeElement));
   await page.getByRole('tab',{name:'数据集',exact:true}).click();const ds=page.getByRole('button',{name:'部署检索验收题集',exact:true});await ds.focus();await page.keyboard.press('Enter');await sheet().waitFor();assert(await sheet().evaluate(e=>e.contains(document.activeElement)));await snap('eval-dataset-sheet');await closeSheet();assert(await ds.evaluate(e=>e===document.activeElement));
   await page.getByRole('tab',{name:'运行',exact:true}).click();await page.getByTestId('knowledge-eval-run-run-candidate').getByRole('button',{name:'查看报告'}).click();const target=page.getByTestId('knowledge-eval-case-case-null::base').getByRole('button');await target.focus();await page.keyboard.press('Enter');await sheet().waitFor();assert(await sheet().evaluate(e=>e.contains(document.activeElement)));await snap('eval-null-case-sheet');const text=await sheet().innerText();assert(text.includes('未评分')&&!text.includes('相关性评分: 0'));await closeSheet();assert(await target.evaluate(e=>e===document.activeElement));return text;
  });
  await check('评测null与expect/judge分组不混算',async()=>{
   await open('knowledge-eval');await page.getByTestId('knowledge-eval-run-run-candidate').getByRole('button',{name:'查看报告'}).click();await page.getByTestId('knowledge-eval-case-case-null::base').waitFor();const row=await page.getByTestId('knowledge-eval-case-case-null::base').innerText();assert(row.includes('未评分')&&!row.includes('0.000'));
   const text=(await state()).text;assert(text.includes('关键词标注')&&text.includes('模型判定'));await snap('eval-grouped-report');return {row,text};
  });
 }
 if(mode==='video') {
  await open();await pause(1800);await library().hover();await pause(1800);await page.keyboard.press('Escape');await pause(1000);await enterLibrary();await pause(2200);await doc().click();await sheet().waitFor();await pause(2500);await closeSheet();await pause(1500);await page.getByRole('link',{name:'检索评测',exact:true}).click();await pause(2000);await page.getByTestId('knowledge-eval-run-run-candidate').getByRole('button',{name:'查看报告'}).click();await pause(2200);await page.getByTestId('knowledge-eval-case-case-null::base').getByRole('button').click();await pause(2800);await closeSheet();await pause(2000);results.push({name:'关键路径录屏',status:'已录制',elapsed:'见video文件元数据'});await save();
 }
} finally {
 await fs.writeFile(path.join(out,`${mode}-runtime.json`),JSON.stringify({errors,network,final:await state().catch(()=>null)},null,2));
 if(mode==='video'){const video=page.video();await context.close();await video.saveAs(path.join(out,'key-interactions.webm'));}else await context.close();
 await browser.close();
}
