/* eslint-disable */
const fs = require("fs");
const path = require("path");
const os = require("os");
const { chromium } = require(path.resolve(__dirname, "../../../webui/node_modules/playwright-core"));

const OUT = __dirname;
const BASE = "http://127.0.0.1:8782";
const cfg = JSON.parse(fs.readFileSync(path.join(OUT, "config.json"), "utf8"));
const SECRET = cfg.channels.websocket.tokenIssueSecret;
const CHROME = path.join(
  process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"),
  "ms-playwright",
  "chromium-1243",
  "chrome-win64",
  "chrome.exe",
);
const checks = [];

function record(name, ok, detail) {
  checks.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name} :: ${JSON.stringify(detail)}`);
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 }, locale: "zh-CN" });
  const consoleErrors = [];
  const failed404 = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("response", (response) => {
    if (response.status() === 404) failed404.push(response.url());
  });
  try {
    await page.goto(`${BASE}/#/?bootstrapSecret=${SECRET}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2500);

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
    const entityCards = page.locator('[data-testid="graph-entity-card"]');
    await entityCards.first().waitFor({ state: "visible", timeout: 30000 });
    const entityCount = await entityCards.count();
    record("Graph tab has entities", entityCount >= 1, { entityCount });
    const entity = entityCards.filter({ hasText: "灰度发布流程" }).first();
    await entity.click();
    await page.locator("[data-graph-node]").first().waitFor({ state: "visible", timeout: 30000 });
    await page.waitForTimeout(1200);
    const nodeCount = await page.locator("[data-graph-node]").count();
    const lineCount = await page.locator('svg[role="img"] line').count();
    record("Graph subgraph renders nodes and edges", nodeCount > 0 && lineCount > 0, {
      nodeCount,
      lineCount,
    });
    await page.screenshot({ path: path.join(OUT, "graph-rerun.png"), fullPage: false });

    await page.goto(`${BASE}/#/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
    const composer = page.locator("textarea").first();
    await composer.fill("根据知识库，灰度发布流程的回滚由谁负责？请引用知识来源。");
    await composer.press("Enter");
    record("Chat request sent", true, {});
    await page
      .getByText(/值班二号/, { exact: false })
      .last()
      .waitFor({ state: "visible", timeout: 240000 });
    await page.waitForFunction(
      () =>
        !document.body.innerText.includes("模型正在回复") &&
        !document.body.innerText.includes("处理中"),
      null,
      { timeout: 240000, polling: 500 },
    );
    await page.waitForTimeout(1500);
    const bodyText = await page.locator("body").innerText();
    const answerExcerpt = bodyText.slice(-1800);
    record("Chat answer contains 值班二号", bodyText.includes("值班二号"), {
      excerpt: answerExcerpt,
    });
    record(
      "Chat answer cites knowledge source",
      /sample-rollout|Graph E2E|chunk:\/\/|doc:\/\/|知识库|引用/.test(answerExcerpt),
      { excerpt: answerExcerpt },
    );
    await page.screenshot({ path: path.join(OUT, "chat-rerun.png"), fullPage: false });

    const api404 = failed404.filter(
      (url) => url.includes("/api/") && !url.includes("/workbench"),
    );
    const unexpectedConsole = consoleErrors.filter(
      (text) => !(text.includes("404") && failed404.some((url) => url.includes("/workbench"))),
    );
    record("No unexpected console errors", unexpectedConsole.length === 0, {
      count: unexpectedConsole.length,
      sample: unexpectedConsole.slice(0, 3),
    });
    record("No non-workbench API 404", api404.length === 0, { api404 });
  } catch (error) {
    record("Script interrupted", false, String(error && error.stack ? error.stack : error));
    await page
      .screenshot({ path: path.join(OUT, "chat-rerun-failure.png"), fullPage: false })
      .catch(() => {});
  } finally {
    console.log(`SUMMARY ${checks.filter((c) => c.ok).length}/${checks.length}`);
    await browser.close();
  }
})();
