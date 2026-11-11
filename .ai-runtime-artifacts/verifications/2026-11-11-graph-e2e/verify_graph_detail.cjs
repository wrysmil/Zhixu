/* eslint-disable */
const path = require("path");
const os = require("os");
const { chromium } = require(path.resolve(__dirname, "../../../webui/node_modules/playwright-core"));

const OUT = __dirname;
const BASE = "http://127.0.0.1:8775";
const SECRET = "zhixu-graph-e2e-secret";
const CHROME = path.join(
  process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"),
  "ms-playwright",
  "chromium-1243",
  "chrome-win64",
  "chrome.exe",
);

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, locale: "zh-CN" });
  try {
    await page.goto(`${BASE}/#/?bootstrapSecret=${SECRET}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);
    await page.goto(`${BASE}/#/knowledge`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="knowledge-page"]', { timeout: 30000 });
    await page.waitForTimeout(1200);
    await page
      .locator('[data-testid="knowledge-kb-list"] li')
      .filter({ hasText: "Graph E2E" })
      .first()
      .click();
    await page.waitForTimeout(1000);
    await page.getByRole("tab", { name: /Graph|图谱/ }).first().click();
    await page.waitForTimeout(1200);
    await page
      .locator('[data-testid="graph-entity-card"]')
      .filter({ hasText: "灰度发布流程" })
      .first()
      .click();
    await page.locator("[data-graph-node]").first().waitFor({ state: "visible", timeout: 30000 });
    await page.getByText(/Neighborhood|一跳子图/).first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(800);
    const nodes = await page.locator("[data-graph-node]").count();
    await page.screenshot({ path: path.join(OUT, "graph-detail.png"), fullPage: false });
    console.log(`DETAIL nodes=${nodes}`);
  } catch (error) {
    console.log(`DETAIL FAIL ${error}`);
  } finally {
    await browser.close();
  }
})();
