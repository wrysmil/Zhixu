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
const checks = [];

function record(name, ok, detail) {
  checks.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name} :: ${JSON.stringify(detail)}`);
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 950 },
    locale: "zh-CN",
  });
  const consoleErrors = [];
  const failed404 = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("response", (response) => {
    if (response.status() === 404) failed404.push(response.url());
  });
  try {
    await page.goto(`${BASE}/#/?bootstrapSecret=${SECRET}`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForTimeout(2500);

    await page.goto(`${BASE}/#/knowledge`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="knowledge-page"]', { timeout: 30000 });
    await page.waitForTimeout(1500);
    const kbItem = page
      .locator('[data-testid="knowledge-kb-list"] li')
      .filter({ hasText: "Graph E2E" })
      .first();
    await kbItem.click();
    await page.waitForTimeout(1200);

    const graphTab = page.getByRole("tab", { name: /Graph|图谱/ }).first();
    await graphTab.click();
    await page.waitForTimeout(1500);
    await page.screenshot({
      path: path.join(OUT, "graph-tab.png"),
      fullPage: true,
    });

    const entity = page.getByText("灰度发布流程", { exact: true }).first();
    await entity.waitFor({ state: "visible", timeout: 30000 });
    const entityCards = page.locator('[data-testid="graph-entity-card"]');
    const entityCount = await entityCards.count();
    record("图谱 tab 显示真实实体", entityCount >= 1, { entityCount });

    await entityCards.filter({ hasText: "灰度发布流程" }).first().click();
    await page.waitForTimeout(1800);
    const neighborhood = page.getByText(/Neighborhood|一跳子图/).first();
    const hasNeighborhood = await neighborhood.isVisible().catch(() => false);
    const nodeCount = await page.locator("[data-graph-node]").count();
    record("实体详情与子图渲染", hasNeighborhood && nodeCount > 0, {
      hasNeighborhood,
      nodeCount,
    });
    await page.screenshot({
      path: path.join(OUT, "graph-detail.png"),
      fullPage: true,
    });

    // 聊天引用链路
    await page.goto(`${BASE}/#/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
    const composer = page.locator("textarea").first();
    await composer.fill("知识库里谁负责灰度发布流程的回滚？请引用知识来源。");
    await composer.press("Enter");
    record("聊天请求已发送", true, {});
    await page
      .getByText(/值班二号/, { exact: false })
      .last()
      .waitFor({ state: "visible", timeout: 180000 });
    await page.waitForFunction(
      () =>
        !document.body.innerText.includes("模型正在回复") &&
        !document.body.innerText.includes("处理中"),
      null,
      { timeout: 180000, polling: 500 },
    );
    await page.waitForTimeout(1500);
    const bodyText = await page.locator("body").innerText();
    const answerExcerpt = bodyText.slice(-1200);
    record("回答包含值班二号", bodyText.includes("值班二号"), {
      excerpt: answerExcerpt,
    });
    record(
      "回答引用知识库来源",
      /sample-rollout|Graph E2E|chunk:\/\/|doc:\/\//.test(answerExcerpt),
      { excerpt: answerExcerpt },
    );
    await page.screenshot({
      path: path.join(OUT, "chat-citation.png"),
      fullPage: false,
    });
    const knownWorkbench404 = failed404.filter((url) => url.includes("/workbench"));
    const api404 = failed404.filter(
      (url) => url.includes("/api/") && !url.includes("/workbench"),
    );
    const unexpectedConsole = consoleErrors.filter(
      (text) => !(text.includes("404") && knownWorkbench404.length > 0),
    );
    record("浏览器控制台无非预期 error", unexpectedConsole.length === 0, {
      count: unexpectedConsole.length,
      sample: unexpectedConsole.slice(0, 3),
    });
    record("无非 workbench API 404", api404.length === 0, {
      api404,
      knownWorkbench404,
    });
  } catch (error) {
    record("脚本中断", false, String(error && error.stack ? error.stack : error));
    await page
      .screenshot({ path: path.join(OUT, "graph-e2e-failure.png"), fullPage: false })
      .catch(() => {});
  } finally {
    console.log(`SUMMARY ${checks.filter((c) => c.ok).length}/${checks.length}`);
    await browser.close();
  }
})();
