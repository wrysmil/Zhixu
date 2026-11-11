import { chromium } from 'file:///D:/studyspace/project/Zhixu/webui/node_modules/playwright-core/index.mjs';
const url='http://127.0.0.1:5186/@fs/D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience/.ai-runtime-artifacts/verifications/knowledge-experience/fixture.html';
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const context=await browser.newContext({viewport:{width:390,height:844}});
const page=await context.newPage();
await page.goto(url+'#/knowledge',{waitUntil:'domcontentloaded'});
await page.getByRole('banner').waitFor();
await page.locator('[data-testid="knowledge-page"]').waitFor();
await page.waitForTimeout(800);
await page.locator('[data-kb-id="kb-product"]').click();
await page.waitForTimeout(900);
const report=await page.evaluate(()=>{
  const vw=document.documentElement.clientWidth;
  const out=[];
  for(const el of document.querySelectorAll('body *')){
    const r=el.getBoundingClientRect();
    if(r.width>vw+1||r.right>vw+1){
      out.push({tag:el.tagName,cls:(el.getAttribute('class')||'').slice(0,110),w:Math.round(r.width),right:Math.round(r.right),scrollW:el.scrollWidth,clientW:el.clientWidth,overflowX:getComputedStyle(el).overflowX});
    }
  }
  return {vw, docScroll:document.documentElement.scrollWidth, offenders:out.slice(0,25)};
});
console.log(JSON.stringify(report,null,1));
await browser.close();