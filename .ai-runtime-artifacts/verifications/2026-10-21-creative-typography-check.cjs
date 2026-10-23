const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const bootstrapUrl = process.env.ZHIXU_BOOTSTRAP_URL;
if (!bootstrapUrl) throw new Error("ZHIXU_BOOTSTRAP_URL is required");
const outDir = path.resolve(".ai-runtime-artifacts/verifications/2026-10-21-creative-converge");
fs.mkdirSync(outDir, { recursive: true });

async function openFirstArticle(page) {
  await page.goto(bootstrapUrl, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    window.location.hash = "#/creative";
  });
  await page.waitForSelector('[data-testid^="open-article-"]', { timeout: 15000 });
  await page.locator('[data-testid^="open-article-"]').first().click();
  await page.waitForSelector('[data-testid="article-editor"]', { timeout: 15000 });
  await page.waitForTimeout(1200);
}

async function measureDesktop(page) {
  const metrics = await page.evaluate(() => {
    const style = (el) => (el ? getComputedStyle(el) : null);
    const rect = (el) => {
      if (!el) return null;
      const box = el.getBoundingClientRect();
      return { x: Math.round(box.x), w: Math.round(box.width), right: Math.round(box.right) };
    };
    const panel = document.querySelector('[data-testid="writing-chat-panel"]');
    const body = document.querySelector('[data-testid="article-body-input"]');
    const markdown = panel?.querySelector(".markdown-content");
    const bubble = panel?.querySelector("p[data-user-message-text]");
    const input = document.querySelector('[data-testid="writing-chat-input"]');
    return {
      bodyFontSize: style(body).fontSize,
      bodyLineHeight: style(body).lineHeight,
      panelWidth: rect(panel)?.w,
      panelFontSize: style(panel).fontSize,
      markdownFontSize: markdown ? style(markdown).fontSize : null,
      markdownLineHeight: markdown ? style(markdown).lineHeight : null,
      bubbleFontSize: bubble ? style(bubble).fontSize : null,
      bubbleLineHeight: bubble ? style(bubble).lineHeight : null,
      chatInputFontSize: style(input).fontSize,
      docScrollWidth: document.documentElement.scrollWidth,
      innerWidth,
      collapseButtons: [...panel.querySelectorAll("header button")].map((el) => el.getAttribute("aria-label")),
    };
  });
  assert.equal(metrics.bodyFontSize, "13px", "article body is 13px");
  assert.ok(parseFloat(metrics.bodyLineHeight) >= 20, `article body rhythm (${metrics.bodyLineHeight})`);
  assert.ok(metrics.panelWidth <= 480, `side panel stays narrow (${metrics.panelWidth})`);
  assert.equal(metrics.markdownFontSize, "12px", "side-chat markdown is 12px");
  assert.ok(parseFloat(metrics.markdownLineHeight) >= 19, `side-chat line-height (${metrics.markdownLineHeight})`);
  if (metrics.bubbleFontSize) assert.equal(metrics.bubbleFontSize, "12px", "user bubble is 12px");
  assert.equal(metrics.chatInputFontSize, "12px", "chat input matches the message scale");
  assert.equal(metrics.docScrollWidth, metrics.innerWidth, "no horizontal page overflow");
  return metrics;
}

async function measurePreview(page) {
  await page.getByRole("button", { name: /^(预览|Preview)$/ }).click();
  await page.waitForSelector('[data-testid="article-preview-pane"]', { timeout: 10000 });
  await page.waitForTimeout(400);
  const preview = await page.evaluate(() => {
    const pane = document.querySelector('[data-testid="article-preview-pane"]');
    const style = getComputedStyle(pane);
    return { fontSize: style.fontSize, lineHeight: style.lineHeight };
  });
  assert.equal(preview.fontSize, "13px", "article preview is 13px");
  await page.getByRole("button", { name: /^(编辑|Edit)$/ }).click();
  return preview;
}

async function measureNarrow(page) {
  const metrics = await page.evaluate(() => {
    const rect = (el) => {
      if (!el) return null;
      const box = el.getBoundingClientRect();
      return {
        x: Math.round(box.x),
        y: Math.round(box.y),
        w: Math.round(box.width),
        right: Math.round(box.right),
        bottom: Math.round(box.bottom),
      };
    };
    const visibleText = (el) => el && el.getClientRects().length > 0;
    const back = document.querySelector('[data-testid="article-editor"] button[aria-label="Back"]');
    const title = document.querySelector('[data-testid="article-editor-title"]');
    const toolbarLabels = [
      ...document.querySelectorAll('[data-testid="materials-toggle"] span'),
      ...document.querySelectorAll('[data-testid="outline-toggle"] span'),
      ...document.querySelectorAll('[data-testid="partner-toggle"] span'),
      ...document.querySelectorAll('[data-testid="article-editor"] button[aria-label*="Markdown"] span'),
    ];
    return {
      innerWidth,
      back: rect(back),
      title: rect(title),
      titleVisible: visibleText(title),
      toolbarLabelVisible: toolbarLabels.map((el) => visibleText(el)),
      partnerToggle: rect(document.querySelector('[data-testid="partner-toggle"]')),
      partnerPanel: rect(document.querySelector('[data-testid="writing-chat-panel"]')),
      docScrollWidth: document.documentElement.scrollWidth,
    };
  });
  assert.ok(metrics.partnerToggle, "partner toggle is measurable");
  assert.ok(metrics.partnerToggle.right <= metrics.innerWidth, "partner toggle stays inside viewport");
  assert.ok(metrics.back && metrics.back.w > 0, "back button keeps its own box");
  assert.ok(metrics.titleVisible, "article title stays visible");
  assert.equal(metrics.docScrollWidth, metrics.innerWidth, "no horizontal page overflow");
  return metrics;
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const errors = [];
  try {
    const desktop = await browser.newContext({ viewport: { width: 1160, height: 824 }, locale: "zh-CN" });
    const page = await desktop.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    await openFirstArticle(page);
    const desktopMetrics = await measureDesktop(page);
    const previewMetrics = await measurePreview(page);
    await page.screenshot({ path: path.join(outDir, "creative-editor-1160-v2.png") });

    const collapse = page.locator('[data-testid="writing-chat-panel"] header button[aria-label*="收起"], [data-testid="writing-chat-panel"] header button[aria-label*="Collapse"]');
    assert.equal(await collapse.count(), 1, "panel header exposes a collapse control");
    await collapse.first().click();
    await page.waitForTimeout(300);
    assert.equal(await page.locator('[data-testid="writing-chat-panel"]').count(), 0, "collapse hides the panel");
    await page.locator('[data-testid="partner-toggle"]').click();
    await page.waitForTimeout(600);
    assert.equal(await page.locator('[data-testid="writing-chat-panel"]').count(), 1, "toolbar toggle reopens the panel");

    const narrow = await browser.newContext({ viewport: { width: 528, height: 800 }, locale: "zh-CN" });
    const narrowPage = await narrow.newPage();
    narrowPage.on("pageerror", (error) => errors.push(error.message));
    narrowPage.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    await openFirstArticle(narrowPage);
    const narrowMetrics = await measureNarrow(narrowPage);
    assert.deepEqual(
      narrowMetrics.toolbarLabelVisible,
      new Array(narrowMetrics.toolbarLabelVisible.length).fill(false),
      "toolbar labels collapse to icons on narrow screens",
    );
    await narrowPage.screenshot({ path: path.join(outDir, "creative-editor-528-v2.png") });

    await narrowPage.locator('[data-testid="partner-toggle"]').click();
    await narrowPage.waitForTimeout(1200);
    assert.equal(await narrowPage.locator('[data-testid="writing-chat-panel"]').count(), 1, "narrow overlay opens");
    const narrowPanel = await narrowPage.evaluate(() => {
      const panel = document.querySelector('[data-testid="writing-chat-panel"]');
      const input = document.querySelector('[data-testid="writing-chat-input"]');
      const box = panel.getBoundingClientRect();
      return {
        panel: { x: Math.round(box.x), w: Math.round(box.width), right: Math.round(box.right) },
        inputVisible: Boolean(input?.offsetParent),
        markdownFontSize: panel.querySelector(".markdown-content")
          ? getComputedStyle(panel.querySelector(".markdown-content")).fontSize
          : null,
      };
    });
    assert.ok(narrowPanel.inputVisible, "narrow overlay input is usable");
    if (narrowPanel.markdownFontSize) assert.equal(narrowPanel.markdownFontSize, "12px", "narrow chat markdown is 12px");
    await narrowPage.screenshot({ path: path.join(outDir, "creative-chat-open-528-v2.png") });
    const narrowCollapse = narrowPage.locator('[data-testid="writing-chat-panel"] header button[aria-label*="收起"], [data-testid="writing-chat-panel"] header button[aria-label*="Collapse"]');
    assert.equal(await narrowCollapse.count(), 1, "narrow overlay exposes a collapse control");
    await narrowCollapse.first().click();
    await narrowPage.waitForTimeout(300);
    assert.equal(await narrowPage.locator('[data-testid="writing-chat-panel"]').count(), 0, "narrow overlay can be closed");

    console.log(JSON.stringify({ desktopMetrics, previewMetrics, narrowMetrics, narrowPanel, consoleErrors: errors }, null, 2));
    assert.deepEqual(errors, [], "no console errors");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
