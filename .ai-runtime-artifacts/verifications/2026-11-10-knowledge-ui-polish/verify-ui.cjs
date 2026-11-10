/* eslint-disable */
const path = require("path");
const { chromium } = require("/Users/mima0000/.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core");

const CHROME = path.join(
  process.env.HOME,
  "Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
);
const OUT = __dirname;
const BASE = "http://127.0.0.1:8765";
const KB_NAME = "E2E 全流程回归知识库";
const checks = [];

function record(name, ok, detail) {
  checks.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name} :: ${JSON.stringify(detail)}`);
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, locale: "zh-CN" });
  try {
    // 知识库详情
    await page.goto(`${BASE}/#/knowledge`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="knowledge-page"]');
    await page.waitForTimeout(1500);
    await page.locator('[data-testid="knowledge-kb-list"] li').filter({ hasText: KB_NAME }).first().click();
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(OUT, "knowledge-detail-after.png"), fullPage: true });
    const uploadLabel = await page.getByLabel("上传").count();
    const nativeFileVisible = await page
      .locator('[data-testid="knowledge-page"] input[type="file"]')
      .first()
      .evaluate((el) => el.getBoundingClientRect().width > 4);
    const rebuildButtonLabel = await page.getByRole("button", { name: "重建" }).count();
    record("上传改为样式化按钮（原生控件隐藏）", uploadLabel > 0 && !nativeFileVisible, {
      uploadLabel,
      nativeFileVisible,
    });
    record("重建按钮有可访问名称", rebuildButtonLabel >= 1, { rebuildButtonLabel });

    // 聊天运行态胶囊
    await page.goto(`${BASE}/#/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1200);
    await page.locator("textarea").first().fill("用一句话解释什么是灰度发布。");
    await page.locator("textarea").first().press("Enter");
    const indicator = page.locator('[data-testid="activity-running-indicator"]');
    await indicator.waitFor({ state: "visible", timeout: 30000 });
    const tone = await indicator.evaluate((el) => getComputedStyle(el).color);
    const pill = indicator.locator("xpath=ancestor::*[@role='status' or contains(@class,'rounded-full')][1]");
    const pillBg = await pill.first().evaluate((el) => getComputedStyle(el).backgroundColor);
    await page.screenshot({ path: path.join(OUT, "chat-running-after.png"), fullPage: false });
    record("运行态指示器改为中性色", !/rgb\(14, 165, 233\)|rgb\(56, 189, 248\)/.test(tone), { tone });
    record("运行态胶囊背景为中性", !/rgb\(224, 242, 254\)/.test(pillBg), { pillBg });

    // 等结束，取完成态
    await page.waitForFunction(
      () => !document.querySelector('[data-testid="activity-running-indicator"]'),
      null,
      { timeout: 120000, polling: 500 },
    );
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(OUT, "chat-done-after.png"), fullPage: false });
    record("运行结束恢复完成态", true, {});
  } catch (error) {
    record("脚本中断", false, String(error && error.stack ? error.stack : error));
    await page.screenshot({ path: path.join(OUT, "ui-polish-failure.png"), fullPage: false }).catch(() => {});
  } finally {
    console.log(`SUMMARY ${checks.filter((c) => c.ok).length}/${checks.length}`);
    await browser.close();
  }
})();
