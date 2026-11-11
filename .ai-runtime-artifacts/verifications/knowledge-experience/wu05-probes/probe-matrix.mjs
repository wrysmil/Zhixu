import { chromium } from 'file:///D:/studyspace/project/Zhixu/webui/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
const url='http://127.0.0.1:5186/@fs/D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience/.ai-runtime-artifacts/verifications/knowledge-experience/fixture.html';
const results=[];
const log=(name,status,evidence)=>{results.push({name,status,evidence});console.log(JSON.stringify(results.at(-1)));};
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});

// ---------- A/B: 矩阵（深浅 × 1440/1280/390 × reduce/no-preference） ----------
for(const locale of ['zh-CN','en']){
 const ctx=await browser.newContext({viewport:{width:1440,height:900}});
 if(locale!=='zh-CN') await ctx.addInitScript(l=>localStorage.setItem('zhixu.locale',l),locale);
 const page=await ctx.newPage();
 page.setDefaultTimeout(8000);
 const open=async(sec='knowledge')=>{await page.goto('about:blank');await page.goto(`${url}#/${sec}`,{waitUntil:'domcontentloaded'});await page.getByRole('banner').waitFor();await page.locator(`[data-testid="${sec==='knowledge'?'knowledge-page':'knowledge-eval-page'}"]`).waitFor();await page.waitForTimeout(500);};
 for(const dark of [false,true]) for(const [width,height] of [[1440,900],[1280,800],[390,844]]) for(const reduced of ['no-preference','reduce']){
  const key=`${locale}-${dark?'dark':'light'}-${width}x${height}-${reduced}`;
  try{
   await page.setViewportSize({width,height});
   await page.emulateMedia({reducedMotion:reduced,colorScheme:dark?'dark':'light'});
   await open();
   if(dark) await page.getByRole('button',{name:'切换深色',exact:true}).click();
   const stages=[];
   for(const stage of ['libraries','documents','document-sheet','eval-runs','eval-report','eval-case']){
    if(stage==='documents'){await page.locator('[data-kb-id="kb-product"]').click();await page.waitForTimeout(500);}
    if(stage==='document-sheet'){await page.getByRole('button',{name:'部署与回滚操作指南',exact:true}).click();await page.getByRole('dialog').waitFor();}
    if(stage==='eval-runs'){await page.goto(`${url}#/knowledge-eval`,{waitUntil:'domcontentloaded'});await page.locator('[data-testid="knowledge-eval-page"]').waitFor();await page.waitForTimeout(400);}
    if(stage==='eval-report'){await page.getByTestId('knowledge-eval-run-run-candidate').getByRole('button',{name:locale==='en'?'View report':'查看报告'}).click();await page.getByTestId('knowledge-eval-case-case-null::base').waitFor();}
    if(stage==='eval-case'){await page.getByTestId('knowledge-eval-case-case-null::base').getByRole('button').click();await page.getByRole('dialog').waitFor();}
    await page.waitForTimeout(350);
    const s=await page.evaluate(()=>{
      const d=document.querySelector('[role=dialog]');
      const cs=d?getComputedStyle(d):null;
      return {client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,dark:document.documentElement.classList.contains('dark'),dialogAnim:cs?cs.animationName:null,dialogDur:cs?cs.animationDuration:null,dialogHasSurface:!!d&&d.classList.contains('knowledge-experience-surface'),overlayAnim:(()=>{const o=document.querySelector('.knowledge-overlay');return o?getComputedStyle(o).animationName:null;})()};
    });
    stages.push({stage,...s});
    if(stage==='document-sheet'||stage==='eval-case'){await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});await page.waitForTimeout(250);}
   }
   const overflow=stages.filter(s=>s.scroll>s.client+1);
   log(key, overflow.length?'未通过':'通过',{overflowStages:overflow.map(o=>o.stage),stages});
  }catch(e){log(key,'未通过',{error:String(e).split('\n')[0]});}
 }
 await ctx.close();
}
fs.writeFileSync(process.env.TEMP+'/wu05/probe-matrix.json',JSON.stringify(results,null,1));
await browser.close();