const { chromium } = require('/Users/mima0000/.npm/_npx/705bc6b22212b352/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const url = 'file:///Users/mima0000/Documents/学习-001/do-project/dontforget/design-demo/index.html';
const output = __dirname;
const report = { checks: [], layouts: [], errors: [] };

(async () => {
  const browser = await chromium.launch({ executablePath: '/Users/mima0000/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    page.on('pageerror', e => report.errors.push(e.message));
    const check = async (name, run) => {
      await run();
      report.checks.push(name);
      console.log('PASS', name);
    };
    await page.goto(url);
    await check('首页无常驻右栏、统计卡片或全局状态栏', async () => {
      assert.equal(await page.locator('#rail').isVisible(), false);
      assert.equal(await page.locator('.statusbar').count(), 0);
      assert.equal(await page.locator('.new-page .grid').count(), 0);
      assert.equal(await page.locator('#newSend').isDisabled(), true);
    });
    await page.screenshot({ path: path.join(output, 'after-home.png') });
    await check('快捷建议只填充草稿，切换记忆和主题后草稿保留', async () => {
      await page.locator('[data-prompt]').first().click();
      const draft = await page.locator('#newInput').inputValue();
      await page.locator('#memoryToggle').click();
      assert.equal(await page.locator('#newInput').inputValue(), draft);
      assert.equal(await page.locator('#memoryToggle').getAttribute('aria-pressed'), 'false');
      await page.locator('#themeBtn').click();
      assert.equal(await page.locator('#newInput').inputValue(), draft);
      await page.locator('#themeBtn').click();
    });
    await check('Enter 发送、Shift+Enter 换行、中文输入法 Enter 不误发', async () => {
      await page.locator('#newInput').fill('检查第一条消息');
      await page.locator('#newInput').press('Shift+Enter');
      assert.match(await page.locator('#newInput').inputValue(), /\n/);
      await page.locator('#newInput').dispatchEvent('keydown', { key: 'Enter', isComposing: true });
      assert.equal(await page.locator('#newInput').count(), 1);
      await page.locator('#newInput').press('Enter');
      await page.waitForURL('**#/chat');
      assert.match(await page.locator('.thread').innerText(), /检查第一条消息/);
      assert.match(await page.locator('.thread').innerText(), /没有调用模型/);
      await page.locator('#chatInput').fill('同一会话的第二条消息');
      await page.locator('#chatInput').press('Enter');
      assert.match(await page.locator('.thread').innerText(), /同一会话的第二条消息/);
      assert.equal(await page.locator('.msg.user').count(), 2);
    });
    await check('历史会话切换及当前新增会话分别保留内容', async () => {
      await page.locator('#sidebar [data-session="0"]').click();
      assert.match(await page.locator('.thread').innerText(), /哪些模块必须跟着搬/);
      await page.locator('#sidebar [data-session="1"]').click();
      assert.match(await page.locator('.thread').innerText(), /Dont Forget 抽离方案/);
      await page.locator('#sidebar [data-session="5"]').click();
      assert.match(await page.locator('.thread').innerText(), /同一会话的第二条消息/);
    });
    await check('记忆侧栏按需开关、关闭按钮与草稿保留', async () => {
      await page.locator('#chatInput').fill('尚未发送的草稿');
      await page.locator('[data-rail]').first().click();
      assert.equal(await page.locator('#rail').isVisible(), true);
      assert.equal(await page.locator('#chatInput').inputValue(), '尚未发送的草稿');
      await page.locator('#rail [data-rail]').click();
      assert.equal(await page.locator('#rail').isVisible(), false);
    });
    await check('附件选择仅显示文件名，可以移除', async () => {
      await page.locator('#fileInput').setInputFiles({ name: '会议记录.txt', mimeType: 'text/plain', buffer: Buffer.from('demo') });
      assert.match(await page.locator('#attachmentList').innerText(), /会议记录.txt/);
      await page.locator('[data-remove-file]').click();
      assert.equal(await page.locator('.attachment-item').count(), 0);
    });
    await check('搜索会话、记忆、空结果、Esc 焦点恢复', async () => {
      await page.locator('#paletteBtn').click();
      await page.locator('#paletteInput').fill('京都');
      assert.match(await page.locator('#paletteList').innerText(), /京都旅行计划/);
      await page.locator('#paletteInput').press('Enter');
      assert.match(await page.locator('.thread').innerText(), /京都旅行计划/);
      await page.locator('#paletteBtn').click();
      await page.locator('#paletteInput').fill('偏好高信息密度');
      await page.locator('#paletteInput').press('Enter');
      await page.waitForURL('**#/memory');
      assert.match(await page.locator('.memdetail').innerText(), /界面偏好高信息密度/);
      await page.locator('#paletteBtn').click();
      await page.locator('#paletteInput').fill('not-found-987654');
      assert.match(await page.locator('#paletteList').innerText(), /没有匹配/);
      await page.locator('#paletteInput').press('Escape');
      assert.equal(await page.locator('#overlay').isVisible(), false);
      assert.equal(await page.evaluate(() => document.activeElement.id), 'paletteBtn');
    });
    await check('记忆分类、技能键盘切换及 MCP 设置入口', async () => {
      await page.locator('[data-filter="偏好"]').click();
      assert.equal(await page.locator('.memrow').count(), 3);
      await page.goto(url + '#/skills');
      const toggle = page.locator('[data-skill="memory"]');
      await toggle.focus();
      await toggle.press('Space');
      assert.equal(await toggle.getAttribute('aria-checked'), 'false');
      await page.locator('[data-mcp]').click();
      await page.waitForURL('**#/settings');
      assert.match(await page.locator('.setbody').innerText(), /MCP 与工具/);
    });
    await check('浏览器后退到新对话后独立建会话，最近列表按活动顺序展示', async () => {
      await page.reload();
      await page.goto(url + '#/new');
      await page.locator('#sidebar [data-session="2"]').click();
      await page.waitForURL('**#/chat');
      await page.goBack();
      await page.locator('#newInput').fill('浏览器返回后创建的新会话');
      await page.locator('#newInput').press('Enter');
      await page.locator('.msg.user').filter({ hasText: '浏览器返回后创建的新会话' }).waitFor();
      assert.equal(await page.locator('#sidebar [data-session]').count(), 6);
      assert.equal(await page.locator('.msg.user').count(), 1);
      assert.equal(await page.locator('#sidebar [data-session]').first().getAttribute('data-session'), '5');
      await page.locator('.newchat').click();
      await page.locator('#newInput').waitFor();
      assert.match(await page.locator('.recent-row').first().innerText(), /浏览器返回后创建的新会话/);
    });
    await check('390px 助手详情默认收起，选择助手后支持关闭按钮与 Escape', async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(url + '#/agents');
      assert.equal(await page.locator('#rail').isVisible(), false);
      await page.locator('[data-agent="researcher"]').click();
      assert.equal(await page.locator('#rail').isVisible(), true);
      await page.locator('[data-close-agent]').click();
      assert.equal(await page.locator('#rail').isVisible(), false);
      await page.locator('[data-agent="reviewer"]').click();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#rail').isVisible(), false);
      await page.locator('[data-go="skills"]').click();
      await page.waitForURL('**#/skills');
    });
    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      for (const route of ['new', 'chat', 'memory', 'identity', 'agents', 'skills', 'automations', 'workbench', 'settings', 'connect']) {
        await page.goto(url + '#/' + route);
        const overflow = await page.evaluate(() => {
          const el = document.querySelector('#main');
          const children = [...el.querySelectorAll('*')].filter(n => getComputedStyle(n).display !== 'none' && n.getBoundingClientRect().width > 0 && n.scrollWidth > n.clientWidth + 2);
          return { document: document.documentElement.scrollWidth > innerWidth, main: el.scrollWidth > el.clientWidth + 2, children: children.slice(0, 6).map(n => ({ tag: n.tagName, class: n.className, width: n.clientWidth, scroll: n.scrollWidth })) };
        });
        report.layouts.push({ width, route, ...overflow });
        assert.equal(overflow.document, false, `${width}px ${route}: document overflow`);
        assert.equal(overflow.main, false, `${width}px ${route}: main overflow`);
      }
      console.log('PASS layout', width);
    }
    await check('320px 窄窗口可打开侧栏并切换会话', async () => {
      await page.goto(url);
      await page.locator('[data-sidebar]').click();
      assert.equal(await page.locator('#sidebar').isVisible(), true);
      await page.locator('#sidebar [data-session="0"]').click();
      assert.equal(await page.locator('#sidebar').isVisible(), false);
      await page.locator('[data-rail]').first().click();
      assert.equal(await page.locator('#rail').isVisible(), true);
      await page.locator('#rail [data-rail]').click();
    });
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.goto(url + '#/chat');
    await page.screenshot({ path: path.join(output, 'after-chat.png') });
    await page.locator('[data-rail]').first().click();
    await page.screenshot({ path: path.join(output, 'after-chat-memory.png') });
    await page.locator('#themeBtn').click();
    await page.screenshot({ path: path.join(output, 'after-dark.png') });
    await page.locator('#themeBtn').click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(url);
    await page.screenshot({ path: path.join(output, 'after-mobile.png') });
    assert.deepEqual(report.errors, []);
    report.success = true;
    console.log(`PASS ${report.checks.length} interaction checks, ${report.layouts.length} layouts, zero page errors`);
  } finally {
    fs.writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
