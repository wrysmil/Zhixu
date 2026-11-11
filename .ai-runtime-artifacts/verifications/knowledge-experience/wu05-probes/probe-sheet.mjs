import { chromium } from 'file:///D:/studyspace/project/Zhixu/webui/node_modules/playwright-core/index.mjs';
const url='http://127.0.0.1:5186/@fs/D:/studyspace/project/.harness-worktrees/Zhixu/wt-2026-11-11-knowledge-experience/.ai-runtime-artifacts/verifications/knowledge-experience/fixture.html';
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:900}});
const page=await context.newPage();
page.on('pageerror',e=>console.log('PAGEERROR',String(e)));
await page.goto('about:blank'); await page.goto(url+'#/knowledge-eval',{waitUntil:'domcontentloaded'});
await page.getByRole('banner').waitFor();
await page.locator('[data-testid="knowledge-eval-page"]').waitFor();
await page.waitForTimeout(900);
const newRun=page.getByRole('button',{name:'新建评测',exact:true});
await newRun.focus(); await page.keyboard.press('Enter');
await page.getByRole('dialog').waitFor();
await page.waitForTimeout(400);
const opened=await page.evaluate(()=>{
  const d=document.querySelector('[role=dialog]');
  const cs=getComputedStyle(d);
  return {text:d.innerText.slice(0,80), state:d.getAttribute('data-state'), side:d.getAttribute('data-side'), animName:cs.animationName, animDur:cs.animationDuration, animFill:cs.animationFillMode, cls:d.className, hasSurface:d.classList.contains('knowledge-experience-surface'), hasMotion:d.classList.contains('knowledge-motion'), bodyLocked:document.body.hasAttribute('data-scroll-locked')};
});
console.log('OPENED', JSON.stringify(opened,null,1));
await page.keyboard.press('Escape');
await page.waitForTimeout(200);
const after200=await page.evaluate(()=>{
  const d=document.querySelector('[role=dialog]');
  return {present:!!d, state:d?.getAttribute('data-state')??null, animName:d?getComputedStyle(d).animationName:null, bodyLocked:document.body.hasAttribute('data-scroll-locked'), overlayCount:document.querySelectorAll('.knowledge-overlay').length};
});
console.log('AFTER_ESC_200ms', JSON.stringify(after200));
await page.waitForTimeout(1500);
const after1700=await page.evaluate(()=>{
  const d=document.querySelector('[role=dialog]');
  return {present:!!d, state:d?.getAttribute('data-state')??null, bodyLocked:document.body.hasAttribute('data-scroll-locked'), overlayCount:document.querySelectorAll('.knowledge-overlay').length, focus:document.activeElement?.textContent?.slice(0,40)};
});
console.log('AFTER_ESC_1700ms', JSON.stringify(after1700));
await browser.close();