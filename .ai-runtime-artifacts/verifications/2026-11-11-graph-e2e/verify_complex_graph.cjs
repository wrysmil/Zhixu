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
  page.on("response", async (response) => {
    if (response.url().includes("/graph/subgraph")) {
      try {
        const body = await response.json();
        console.log(`SUBGRAPH API nodes=${body.nodes?.length} edges=${body.edges?.length}`);
      } catch (error) {
        console.log(`SUBGRAPH API parse failed ${error}`);
      }
    }
  });
  try {
    await page.goto(`${BASE}/#/?bootstrapSecret=${SECRET}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);
    await page.goto(`${BASE}/#/knowledge`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="knowledge-page"]', { timeout: 30000 });
    await page.waitForTimeout(1200);
    await page
      .locator('[data-testid="knowledge-kb-list"] li')
      .filter({ hasText: "Graph Complex" })
      .first()
      .click();
    await page.waitForTimeout(1000);
    await page.getByRole("tab", { name: /Graph|图谱/ }).first().click();
    await page.waitForTimeout(1200);

    const search = page.locator('input[placeholder="搜索实体"]').first();
    await search.fill("值班二号");
    await page.waitForTimeout(900);
    const card = page
      .locator('[data-testid="graph-entity-card"]')
      .filter({ hasText: "值班二号" })
      .first();
    await card.click();
    await page.locator("[data-graph-node]").first().waitFor({ state: "visible", timeout: 30000 });
    await page.waitForTimeout(1200);
    const nodes = await page.locator("[data-graph-node]").count();
    const graphLines = page.locator('svg[role="img"] line');
    const lines = await graphLines.count();
    const attrs = lines
      ? await graphLines.first().evaluate((el) => ({
          x1: el.getAttribute("x1"),
          y1: el.getAttribute("y1"),
          x2: el.getAttribute("x2"),
          y2: el.getAttribute("y2"),
          stroke: el.getAttribute("stroke"),
          computed: getComputedStyle(el).stroke,
          width: getComputedStyle(el).strokeWidth,
        }))
      : null;
    console.log(`COMPLEX nodes=${nodes} graphLines=${lines} first=${JSON.stringify(attrs)}`);
    await page.getByText(/Neighborhood|一跳子图/).first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(OUT, "graph-complex-page.png"), fullPage: false });
    await page
      .locator('svg[role="img"]')
      .first()
      .screenshot({ path: path.join(OUT, "graph-complex.png") });
  } catch (error) {
    console.log(`COMPLEX FAIL ${error}`);
  } finally {
    await browser.close();
  }
})();
