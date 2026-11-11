import { chromium } from 'file:///D:/studyspace/project/Zhixu/webui/node_modules/playwright-core/index.mjs';
const url='http://127.0.0.1:5186/@fs/D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience/.ai-runtime-artifacts/verifications/knowledge-experience/fixture.html';
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const ctx=await browser.newContext({viewport:{width:1440,height:900}});
const page=await ctx.newPage();
page.on('pageerror',e=>console.log('PAGEERROR',String(e).slice(0,200)));
await page.goto(url+'#/knowledge',{waitUntil:'domcontentloaded'});
await page.getByRole('banner').waitFor();
await page.locator('[data-testid="knowledge-page"]').waitFor();
await page.waitForTimeout(700);
const switched=await page.evaluate(async()=>{
  try{ const m=await import('/src/i18n/index.ts'); await m.setAppLanguage('en'); return {ok:true, lang:document.documentElement.lang}; }
  catch(e){ return {ok:false, err:String(e).slice(0,300)}; }
});
console.log('SWITCH', JSON.stringify(switched));
await page.waitForTimeout(600);
console.log('TEXT', JSON.stringify((await page.evaluate(()=>document.body.innerText)).slice(0,500)));
await browser.close();