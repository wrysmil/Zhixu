/* eslint-disable */
const fs = require("fs");
const path = require("path");
const os = require("os");
const { chromium } = require(path.resolve(__dirname, "../../../webui/node_modules/playwright-core"));

const OUT = __dirname;
const BASE = "http://127.0.0.1:8775";
const SECRET = "zhixu-graph-e2e-secret";
const CENTER = JSON.parse(fs.readFileSync(path.join(OUT, "novel-center.json"), "utf8"));
const CHROME = path.join(
  process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"),
  "ms-playwright",
  "chromium-1243",
  "chrome-win64",
  "chrome.exe",
);

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 }, locale: "zh-CN" });
  try {
    await page.goto(`${BASE}/#/?bootstrapSecret=${SECRET}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);
    await page.goto(`${BASE}/#/knowledge`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="knowledge-page"]', { timeout: 30000 });
    await page.waitForTimeout(1200);
    await page
      .locator('[data-testid="knowledge-kb-list"] li')
      .filter({ hasText: "Graph Novel" })
      .first()
      .click();
    await page.waitForTimeout(1000);
    await page.getByRole("tab", { name: /Graph|图谱/ }).first().click();
    await page.waitForTimeout(1000);
    await page.locator('input[placeholder="搜索实体"]').first().fill("孔乙己");
    await page.waitForTimeout(900);

    const card = page.locator(`[data-testid="graph-entity-card"][data-entity-id="${CENTER.id}"]`);
    await card.first().click();
    await page.locator("[data-graph-node]").first().waitFor({ state: "visible", timeout: 30000 });
    await page.waitForTimeout(1500);
    const nodes = await page.locator("[data-graph-node]").count();
    const lines = await page.locator('svg[role="img"] line').count();
    console.log(`NOVEL 1hop center=${CENTER.name} nodes=${nodes} lines=${lines}`);
    await page.getByText(/Neighborhood|一跳子图/).first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(OUT, "novel-graph-page.png"), fullPage: false });
    await page.locator('svg[role="img"]').first().screenshot({
      path: path.join(OUT, "novel-graph.png"),
    });

    const hopSelect = page.getByLabel(/Neighborhood|一跳子图/).first();
    await hopSelect.selectOption("2");
    await page.waitForTimeout(1800);
    const nodes2 = await page.locator("[data-graph-node]").count();
    const lines2 = await page.locator('svg[role="img"] line').count();
    console.log(`NOVEL 2hop nodes=${nodes2} lines=${lines2}`);
    await page.screenshot({ path: path.join(OUT, "novel-graph-2hop-page.png"), fullPage: false });
    await page.locator('svg[role="img"]').first().screenshot({
      path: path.join(OUT, "novel-graph-2hop.png"),
    });
  } catch (error) {
    console.log(`NOVEL FAIL ${error}`);
  } finally {
    await browser.close();
  }
})();
