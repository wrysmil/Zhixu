/* eslint-disable */
const path = require("path");
const { chromium } = require("/Users/mima0000/.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core");

const CHROME = path.join(
  process.env.HOME,
  "Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
);
const OUT = __dirname;

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, locale: "zh-CN" });
  await page.goto("http://127.0.0.1:8765/#/knowledge", { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="knowledge-stat-vectors"]');
  await page.waitForTimeout(2500);
  const tiles = await page.evaluate(() => {
    const read = (id) => document.querySelector(`[data-testid="${id}"]`)?.innerText.replace(/\s+/g, " ").trim() ?? null;
    return {
      kbs: read("knowledge-stat-kbs"),
      docs: read("knowledge-stat-docs"),
      chunks: read("knowledge-stat-chunks"),
      pending: read("knowledge-stat-pending"),
      vectors: read("knowledge-stat-vectors"),
    };
  });
  console.log(JSON.stringify(tiles));
  await page.screenshot({ path: path.join(OUT, "step-16-vector-tile-fixed.png"), fullPage: false });
  await browser.close();
})();
