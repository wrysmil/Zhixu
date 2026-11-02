const { chromium } = require('/Users/mima0000/.npm/_npx/705bc6b22212b352/node_modules/playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const output = __dirname;
const root = path.resolve(__dirname, '../..');
const url = 'http://127.0.0.1:5173/@fs' + root + '/.ai-runtime-artifacts/verifications/2026-11-02-chat-activity-preview.html';
const report = { checks: [], screenshots: [], errors: [] };
(async () => {
  const browser = await chromium.launch({ executablePath: '/Users/mima0000/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell' });
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
    page.on('pageerror', e => report.errors.push(e.message));
    const check = async (name, run) => { await run(); report.checks.push(name); console.log('PASS', name); };
    const shot = async (name) => { const file = `2026-11-02-chat-activity-${name}.png`; await page.screenshot({ path: path.join(output, file) }); report.screenshots.push(file); };
    await page.goto(url);
    await page.getByRole('button', { name: '开始等待', exact: true }).click();
    await check('等待模型时仍有持续运行指示，回复区靠上', async () => {
      await page.getByTestId('activity-running-indicator').waitFor({ state: 'visible' });
      await page.waitForFunction(() => {
        const s = document.querySelector('[data-testid="thread-message-region"]');
        const a = document.querySelector('[data-state="thinking"]');
        return s && a && Math.abs(a.getBoundingClientRect().top - s.getBoundingClientRect().top - 16) < 4;
      });
    });
    await shot('waiting');
    await page.getByRole('button', { name: '展开思考', exact: true }).click();
    await check('思考详情可读，外层活动区超过旧180px上限', async () => {
      await page.getByTestId('activity-step-detail').waitFor({ state: 'visible' });
      assert.ok((await page.getByTestId('agent-activity-scroll').boundingBox()).height > 240);
      assert.equal(await page.getByText('验证重复事件只创建一张工单', { exact: true }).count(), 1);
      const detail = await page.getByTestId('activity-step-detail').evaluate(el => ({ font: getComputedStyle(el).fontSize, overflow: getComputedStyle(el).overflowY }));
      assert.equal(detail.font, '14px');
      assert.equal(detail.overflow, 'visible');
    });
    await shot('thinking');
    await page.getByRole('button', { name: '正文暂停', exact: true }).click();
    await check('正文暂停后末尾仍显示运行状态', async () => {
      await page.locator('[data-assistant-footer] [role="status"]').filter({ hasText: '模型正在回复' }).waitFor({ state: 'visible' });
    });
    await shot('text');
    await page.getByRole('button', { name: '准备写文件', exact: true }).click();
    await check('文件参数未生成完也显示准备编辑', async () => {
      await page.getByText('正在准备文件编辑…', { exact: true }).waitFor({ state: 'visible' });
    });
    await shot('file');
    await page.getByRole('button', { name: '长回复', exact: true }).click();
    await check('长正文继续跟随末尾，用户上翻后不抢位置', async () => {
      await page.locator('[data-assistant-footer] [role="status"]').waitFor({ state: 'visible' });
      const scroller = page.getByTestId('thread-message-region');
      await scroller.hover();
      await page.mouse.wheel(0, -350);
      await page.waitForFunction(() => {
        const el = document.querySelector('[data-testid="thread-message-region"]');
        return el && el.scrollHeight - el.clientHeight - el.scrollTop > 100;
      });
      const previous = await scroller.evaluate(el => el.scrollTop);
      await page.waitForTimeout(1100);
      assert.ok(Math.abs(await scroller.evaluate(el => el.scrollTop) - previous) < 3);
    });
    await page.getByRole('button', { name: '新一轮', exact: true }).click();
    await page.getByRole('button', { name: '正文暂停', exact: true }).click();
    await page.getByRole('button', { name: '完成', exact: true }).click();
    await check('整轮完成后运行提示消失', async () => {
      assert.equal(await page.getByTestId('activity-running-indicator').count(), 0);
      assert.equal(await page.locator('[data-assistant-footer] [role="status"]').count(), 0);
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '新一轮', exact: true }).click();
    await page.getByRole('button', { name: '展开思考', exact: true }).click();
    await check('窄屏不横向溢出，活动详情可见', async () => {
      await page.getByTestId('activity-step-detail').waitFor({ state: 'visible' });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    });
    await shot('mobile');
    await page.getByRole('button', { name: '切换主题', exact: true }).click();
    await shot('dark');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await check('减少动态效果时停止旋转但保留指示', async () => {
      assert.equal(await page.getByTestId('activity-running-indicator').evaluate(el => getComputedStyle(el).animationName), 'none');
    });
    assert.deepEqual(report.errors, []);
  } finally {
    fs.writeFileSync(path.join(output, '2026-11-02-chat-activity-browser-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
