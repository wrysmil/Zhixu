const { chromium } = require('/Users/mima0000/.npm/_npx/705bc6b22212b352/node_modules/playwright');
const path = require('node:path');
const fs = require('node:fs');
const output = __dirname;
const root = path.resolve(__dirname, '../..');
const url = 'http://127.0.0.1:5173/@fs' + root + '/.ai-runtime-artifacts/verifications/2026-11-02-chat-activity-preview.html';
const tag = process.argv[2] || 'now';
(async () => {
  const browser = await chromium.launch({ executablePath: '/Users/mima0000/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell' });
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
    page.on('pageerror', e => console.log('PAGEERROR', e.message));
    page.on('console', m => { if (m.type() === 'error') console.log('CONSOLE', m.text()); });
    await page.goto(url);
    const shot = async (name) => { await page.screenshot({ path: path.join(output, `2026-11-02-activity-${tag}-${name}.png`) }); console.log('shot', name); };
    const modes = [['waiting','开始等待'],['thinking','展开思考'],['text','正文暂停'],['file','准备写文件'],['done','完成']];
    for (const [key, name] of modes) {
      await page.getByRole('button', { name, exact: true }).click();
      await page.waitForTimeout(600);
      await shot(key);
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
