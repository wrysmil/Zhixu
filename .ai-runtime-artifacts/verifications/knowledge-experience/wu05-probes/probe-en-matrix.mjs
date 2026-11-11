import { chromium } from 'file:///D:/studyspace/project/Zhixu/webui/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
const url='http://127.0.0.1:5186/@fs/D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience/.ai-runtime-artifacts/verifications/knowledge-experience/fixture.html';
const results=[];const log=(n,s,e)=>{results.push({name:n,status:s,evidence:e});console.log(JSON.stringify({name:n,status:s,...(typeof e==='string'?{error:e}:e)}));};
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const ctx=await browser.newContext({viewport:{width:1440,height:900}});
const page=await ctx.newPage(); page.setDefaultTimeout(8000);
const open=async(sec='knowledge')=>{
  await page.goto('about:blank'); await page.goto(`${url}#/${sec}`,{waitUntil:'domcontentloaded'});
  await page.getByRole('banner').waitFor(); await page.locator(`[data-testid="${sec==='knowledge'?'knowledge-page':'knowledge-eval-page'}"]`).waitFor();
  await page.evaluate(async()=>{const m=await import('/src/i18n/index.ts');await m.setAppLanguage('en');});
  await page.waitForTimeout(500);
};
for(const dark of [false,true]) for(const [width,height] of [[1440,900],[1280,800],[390,844]]) for(const reduced of ['no-preference','reduce']){
  const key=`en-${dark?'dark':'light'}-${width}x${height}-${reduced}`;
  try{
    await page.setViewportSize({width,height});
    await page.emulateMedia({reducedMotion:reduced,colorScheme:dark?'dark':'light'});
    await open();
    if(dark) await page.getByRole('button',{name:'切换深色',exact:true}).click();
    const stages=[];
    for(const stage of ['libraries','documents','document-sheet','eval-runs','eval-report','eval-case']){
      if(stage==='documents'){await page.locator('[data-kb-id="kb-product"]').click();await page.waitForTimeout(500);}
      if(stage==='document-sheet'){await page.getByRole('button',{name:'部署与回滚操作指南',exact:true}).click();await page.getByRole('dialog').waitFor();}
      if(stage==='eval-runs'){await page.goto(`${url}#/knowledge-eval`,{waitUntil:'domcontentloaded'});await page.locator('[data-testid="knowledge-eval-page"]').waitFor();await page.evaluate(async()=>{const m=await import('/src/i18n/index.ts');await m.setAppLanguage('en');});await page.waitForTimeout(400);}
      if(stage==='eval-report'){await page.getByTestId('knowledge-eval-run-run-candidate').getByRole('button',{name:'View report'}).click();await page.getByTestId('knowledge-eval-case-case-null::base').waitFor();}
      if(stage==='eval-case'){await page.getByTestId('knowledge-eval-case-case-null::base').getByRole('button').click();await page.getByRole('dialog').waitFor();}
      await page.waitForTimeout(350);
      const s=await page.evaluate(()=>{
        const d=document.querySelector('[role=dialog]');const cs=d?getComputedStyle(d):null;
        const clipped=[];
        for(const el of document.querySelectorAll('body *')){
          if(el.children.length>0)continue;
          const t=(el.textContent||'').trim(); if(!t||t.length>60)continue;
          const st=getComputedStyle(el);
          if(st.overflowX==='auto'||st.overflowX==='scroll'||st.textOverflow==='ellipsis')continue;
          if(el.scrollWidth>el.clientWidth+2)clipped.push({t:t.slice(0,40),sw:el.scrollWidth,cw:el.clientWidth});
        }
        return {client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,dark:document.documentElement.classList.contains('dark'),reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,dialogAnim:cs?cs.animationName:null,dialogDur:cs?cs.animationDuration:null,dialogSurface:!!d&&d.classList.contains('knowledge-experience-surface'),clipped:clipped.slice(0,6)};
      });
      stages.push({stage,...s});
      if(stage==='document-sheet'||stage==='eval-case'){await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});await page.waitForTimeout(250);}
    }
    const overflow=stages.filter(s=>s.scroll>s.client+1);
    const clipped=stages.flatMap(s=>s.clipped.map(c=>({stage:s.stage,...c})));
    log(key,(overflow.length||clipped.length)?'未通过':'通过',{overflow:overflow.map(o=>o.stage),clipped,stages:stages.map(s=>({stage:s.stage,client:s.client,scroll:s.scroll,dark:s.dark,reduced:s.reduced,dialogAnim:s.dialogAnim,dialogDur:s.dialogDur,dialogSurface:s.dialogSurface}))});
  }catch(e){log(key,'未通过',{error:String(e).split('\n')[0]});}
}
fs.writeFileSync(process.env.TEMP+'/wu05/probe-en-matrix.json',JSON.stringify(results,null,1));
await browser.close();