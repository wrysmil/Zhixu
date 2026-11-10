/* eslint-disable */
/**
 * 知识全流程浏览器回归（隔离实例 127.0.0.1:8765）
 *
 * 覆盖：知识库总览 → 建库 → 上传入库 → 分块预览（保护代码块/表格）→ 检索预览（三模式）
 *      → 聊天内知识检索 → 评测台建集 → 首个运行与报告 → 第二配置运行 → 对比
 *
 * 输出：本目录下 step-*.png、e2e-fullflow-report.json
 */
const fs = require("fs");
const path = require("path");
const { chromium } = require("/Users/mima0000/.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core");

const CHROME = path.join(
  process.env.HOME,
  "Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
);
const OUT = __dirname;
const BASE = "http://127.0.0.1:8765";
const DOC = path.join(OUT, "e2e-rollback.md");
const KB_NAME = "E2E 全流程回归知识库";
const DS_NAME = "E2E 全流程数据集";
const QB1 = "线上版本出问题了怎么回滚";
const QB2 = "退款多久到账";

const steps = [];
const consoleErrors = [];
const httpErrors = [];
const captured = { search: [], report: [], metrics: [] };

function now() {
  return new Date().toISOString();
}

function record(name, ok, detail) {
  steps.push({ step: name, ok, detail, at: now() });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name} :: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
}

async function shot(page, name, fullPage = true) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage });
}

async function waitBodyText(page, text, timeout) {
  await page.waitForFunction(
    (needle) => document.body && document.body.innerText.includes(needle),
    text,
    { timeout, polling: 500 },
  );
}

async function main() {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 950 },
    locale: "zh-CN",
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);

  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));
  page.on("response", async (res) => {
    try {
      const url = res.url();
      if (res.status() >= 400) httpErrors.push({ url, status: res.status() });
      if (res.ok() && url.includes("/knowledge/metrics")) captured.metrics.push(await res.json());
      if (res.ok() && url.includes("/knowledge/search")) captured.search.push(await res.json());
      if (res.ok() && /\/knowledge\/runs\/[^/]+\/report/.test(url)) captured.report.push(await res.json());
    } catch {
      /* 忽略非 JSON 响应 */
    }
  });

  try {
    // ---------------------------------------------------------------- 1. 首页
    await page.goto(`${BASE}/#/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);
    await shot(page, "step-01-home", false);
    record("打开 WebUI 首页", true, await page.title());

    // ---------------------------------------------------------------- 2. 知识库总览
    await page.getByRole("button", { name: "知识库", exact: true }).first().click();
    await page.waitForSelector('[data-testid="knowledge-page"]');
    await page.waitForTimeout(1200);

    // 幂等：上一轮失败留下的同名知识库先清掉，避免重复建库干扰断言。
    const stale = page.locator('[data-testid="knowledge-kb-list"] li').filter({ hasText: KB_NAME });
    if ((await stale.count()) > 0) {
      await stale.first().hover();
      await stale.first().getByRole("button", { name: "删除知识库" }).click();
      await page.getByRole("dialog").getByRole("button", { name: "确认" }).click();
      await page.waitForTimeout(2000);
      record("清理同名知识库", !(await page.locator('[data-testid="knowledge-kb-list"] li').filter({ hasText: KB_NAME }).count()), KB_NAME);
    }
    await shot(page, "step-02-knowledge-overview");
    const statsBefore = await page.evaluate(() => {
      const read = (id) => document.querySelector(`[data-testid="${id}"]`)?.innerText.replace(/\s+/g, " ").trim() ?? null;
      return {
        kbs: read("knowledge-stat-kbs"),
        docs: read("knowledge-stat-docs"),
        chunks: read("knowledge-stat-chunks"),
        pending: read("knowledge-stat-pending"),
        vectors: read("knowledge-stat-vectors"),
      };
    });
    record("知识库总览渲染", statsBefore.kbs !== null, statsBefore);

    // ---------------------------------------------------------------- 3. 新建知识库
    await page.getByRole("button", { name: "新建知识库" }).click();
    const kbForm = page.locator("form").filter({ has: page.getByRole("button", { name: "创建" }) });
    await kbForm.locator("input").first().fill(KB_NAME);
    await kbForm.locator("textarea").first().fill("浏览器全流程回归：建库、入库、分块保护、检索与评测。");
    await kbForm.getByRole("button", { name: "创建" }).click();
    await waitBodyText(page, KB_NAME, 30000);
    await shot(page, "step-03-kb-created");
    record("新建知识库", true, KB_NAME);

    // ---------------------------------------------------------------- 4. 入库
    await page.locator('[data-testid="knowledge-kb-list"] li').filter({ hasText: KB_NAME }).first().click();
    await page.waitForTimeout(600);
    await shot(page, "step-04-kb-detail-empty");
    // 页面里还有聊天侧栏的隐藏附件 input，必须限定在知识库页面内。
    await page
      .locator('[data-testid="knowledge-page"] input[type="file"]')
      .first()
      .setInputFiles(DOC);
    await page.waitForFunction(
      () => {
        const rows = Array.from(document.querySelectorAll("tbody tr"));
        return rows.some((row) => row.innerText.includes("就绪"));
      },
      null,
      { timeout: 120000, polling: 1000 },
    );
    const docRow = await page.locator("tbody tr").first().innerText();
    await page.waitForTimeout(800);
    await shot(page, "step-05-doc-ready");
    record("文档入库并 ready", true, docRow.replace(/\s+/g, " "));

    // ---------------------------------------------------------------- 5. 分块预览
    await page.getByRole("button", { name: "查看分块" }).first().click();
    await page.waitForSelector('[data-testid="knowledge-chunk-preview"]');
    await page.waitForSelector('[data-testid="knowledge-chunk"]');
    const chunks = await page.$$eval('[data-testid="knowledge-chunk"]', (els) =>
      els.map((el) => el.innerText),
    );
    await shot(page, "step-06-chunks", false);
    const chunksJoined = chunks.join("\n---\n");
    const fenceIntact = chunks.some((c) => c.includes("```bash") && c.includes("kubectl rollout undo deployment/web-api"));
    const tableIntact = chunks.some((c) => c.includes("| 支付方式 | 到账时间 |") && c.includes("| 银行卡 | 1-3 个工作日 |"));
    record("分块预览", chunks.length > 0, { chunkCount: chunks.length, fenceIntact, tableIntact });
    await page.getByRole("button", { name: "关闭" }).click();

    // ---------------------------------------------------------------- 6. 检索预览（混合）
    await page.getByRole("tab", { name: "检索预览" }).click();
    await page.getByLabel("查询").fill(QB1);
    await page.getByLabel("模式").selectOption("hybrid");
    await page.getByLabel("条数").selectOption("8");
    await page.getByRole("button", { name: "检索", exact: true }).click();
    await waitBodyText(page, "实际模式", 30000);
    await page.waitForTimeout(500);
    await shot(page, "step-07-search-hybrid");
    const hybrid = captured.search.at(-1);
    const hybridTop = hybrid?.hits?.[0]?.content ?? "";
    record("检索预览 hybrid", hybrid?.hits?.length > 0 && hybridTop.includes("回滚"), {
      modeUsed: hybrid?.modeUsed,
      degraded: hybrid?.degraded,
      hits: hybrid?.hits?.length,
      top: hybridTop.replace(/\s+/g, " ").slice(0, 80),
    });

    // ---------------------------------------------------------------- 7. 检索预览（关键词 / 语义）
    await page.getByLabel("查询").fill("kubectl rollout undo");
    await page.getByLabel("模式").selectOption("keyword");
    await page.getByRole("button", { name: "检索", exact: true }).click();
    await page.waitForTimeout(1500);
    await shot(page, "step-08-search-keyword");
    const keyword = captured.search.at(-1);
    const keywordTop = keyword?.hits?.[0]?.content ?? "";
    record("检索预览 keyword", keyword?.hits?.length > 0 && keywordTop.includes("kubectl rollout undo"), {
      modeUsed: keyword?.modeUsed,
      hits: keyword?.hits?.length,
      top: keywordTop.replace(/\s+/g, " ").slice(0, 80),
    });

    await page.getByLabel("查询").fill(QB2);
    await page.getByLabel("模式").selectOption("semantic");
    await page.getByRole("button", { name: "检索", exact: true }).click();
    await page.waitForTimeout(2500);
    await shot(page, "step-09-search-semantic");
    const semantic = captured.search.at(-1);
    const semanticTop = semantic?.hits?.[0]?.content ?? "";
    record("检索预览 semantic", (semantic?.hits?.length ?? 0) > 0, {
      modeUsed: semantic?.modeUsed,
      degraded: semantic?.degraded,
      hits: semantic?.hits?.length,
      top: semanticTop.replace(/\s+/g, " ").slice(0, 80),
    });

    // ---------------------------------------------------------------- 8. 回列表看统计
    await page
      .locator('[data-testid="knowledge-page"] button')
      .filter({ hasText: "知识库" })
      .first()
      .click();
    await page.waitForTimeout(1500);
    await shot(page, "step-09b-knowledge-stats");
    const statsAfter = await page.evaluate(() => {
      const read = (id) => document.querySelector(`[data-testid="${id}"]`)?.innerText.replace(/\s+/g, " ").trim() ?? null;
      return {
        kbs: read("knowledge-stat-kbs"),
        docs: read("knowledge-stat-docs"),
        chunks: read("knowledge-stat-chunks"),
        pending: read("knowledge-stat-pending"),
        vectors: read("knowledge-stat-vectors"),
      };
    });
    record("入库后统计卡", statsAfter.docs?.includes("1"), statsAfter);

    // ---------------------------------------------------------------- 9. 聊天内知识检索
    await page.goto(`${BASE}/#/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
    const box = page.locator("textarea").first();
    await box.fill("请根据知识库回答：线上版本出问题了怎么回滚？请给出命令。");
    await box.press("Enter");
    let chatOk = false;
    let chatDetail = "";
    try {
      await page.waitForFunction(
        () => document.body.innerText.includes("doc://") || document.body.innerText.includes("kubectl rollout undo"),
        null,
        { timeout: 150000, polling: 1000 },
      );
      chatOk = true;
      chatDetail = "回答出现 doc:// 引用或回滚命令";
    } catch {
      chatDetail = "150s 内未出现知识引用（可能是模型服务不可用）";
    }
    await page.waitForTimeout(2000);
    await shot(page, "step-10-chat-knowledge", false);
    const chatText = await page.evaluate(() => {
      const main = document.querySelector("main") || document.body;
      return main.innerText.slice(-1500);
    });
    record("聊天内知识检索", chatOk, { detail: chatDetail, tail: chatText.replace(/\s+/g, " ").slice(-300) });

    // ---------------------------------------------------------------- 9. 评测台列表
    await page.goto(`${BASE}/#/knowledge-eval`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="knowledge-eval-page"]');
    await page.waitForTimeout(1500);
    await shot(page, "step-11-eval-list");
    record("评测台列表页加载", true, { metrics: captured.metrics.at(-1)?.metrics?.length ?? 0 });

    // 幂等：清掉上一轮留下的同名数据集（若被运行引用而无法删除则跳过）。
    const dsList = page.locator('[data-testid="knowledge-eval-datasets"] li').filter({ hasText: DS_NAME });
    for (let i = 0; i < 3 && (await dsList.count()) > 0; i += 1) {
      const before = await dsList.count();
      await dsList.first().getByRole("button", { name: "删除数据集" }).click();
      await page.waitForTimeout(1500);
      if ((await dsList.count()) >= before) break;
    }
    if ((await dsList.count()) > 0) {
      record("清理同名数据集", false, "同名数据集仍存在（可能被历史运行引用），本轮改用带后缀的名称");
    }
    const datasetName = (await dsList.count()) > 0 ? `${DS_NAME} ${Date.now().toString().slice(-5)}` : DS_NAME;

    // ---------------------------------------------------------------- 10. 新建数据集
    await page.getByRole("button", { name: "新建数据集" }).first().click();
    const dsForm = page.locator("form").filter({ has: page.getByRole("button", { name: "新建数据集" }) });
    await dsForm.getByLabel("数据集名称").fill(datasetName);
    await dsForm.getByLabel("备注").fill("浏览器全流程回归数据集");
    await dsForm.getByLabel("查询").first().fill(QB1);
    await dsForm.getByLabel("期望关键词").first().fill("kubectl rollout undo");
    await dsForm.getByRole("button", { name: "添加查询" }).click();
    await dsForm.getByLabel("查询").nth(1).fill(QB2);
    await dsForm.getByLabel("期望关键词").nth(1).fill("到账");
    await dsForm.getByRole("button", { name: "新建数据集" }).click();
    await waitBodyText(page, datasetName, 30000);
    await page.waitForTimeout(800);
    await shot(page, "step-12-dataset-created");
    record("新建评测数据集", true, datasetName);

    // ---------------------------------------------------------------- 11. 首个运行 → 报告
    await page.getByRole("tab", { name: "运行", exact: true }).click();
    await page.getByLabel("数据集", { exact: true }).selectOption({ label: datasetName });
    await page.getByRole("button", { name: "发起运行" }).click();
    await page.waitForSelector('[data-testid="knowledge-eval-run-header"]', { timeout: 30000 });
    await page.waitForFunction(
      () => {
        const header = document.querySelector('[data-testid="knowledge-eval-run-header"]');
        return header && header.innerText.includes("completed");
      },
      null,
      { timeout: 180000, polling: 1000 },
    );
    await page.waitForTimeout(1500);
    await shot(page, "step-13-run-detail");
    const runDetail1 = await page.evaluate(() => {
      const header = document.querySelector('[data-testid="knowledge-eval-run-header"]')?.innerText ?? "";
      const overview = document.querySelector('[data-testid="knowledge-eval-overview"]')?.innerText ?? "";
      const cases = document.querySelector('[data-testid="knowledge-eval-case-results"]')?.innerText ?? "";
      return { header: header.replace(/\s+/g, " "), overview: overview.replace(/\s+/g, " ").slice(0, 600), cases: cases.replace(/\s+/g, " ").slice(0, 500) };
    });
    const report1 = captured.report.at(-1);
    const agg = report1?.metrics?.expect?.metrics ?? null;
    record("首个运行完成并出报告", Boolean(report1 && agg), { header: runDetail1.header, agg });
    const runId1 = runDetail1.header.match(/run_[a-z0-9]+/i)?.[0] ?? null;

    // ---------------------------------------------------------------- 12. 返回 → 第二配置运行
    // 限定页面内面包屑，避免命中左侧导航（导航会重置到「数据集」分区）。
    await page
      .locator('[data-testid="knowledge-eval-page"] button')
      .filter({ hasText: "检索评测台" })
      .first()
      .click();
    await page.waitForTimeout(1000);
    await page.getByRole("button", { name: "添加配置" }).click();
    await page.getByLabel("配置 ID").nth(1).fill("keyword");
    await page.getByLabel("配置名称").nth(1).fill("Keyword");
    await page.getByLabel("检索模式").nth(1).selectOption("keyword");
    await page.getByRole("button", { name: "发起运行" }).click();
    await page.waitForSelector('[data-testid="knowledge-eval-run-header"]', { timeout: 30000 });
    await page.waitForFunction(
      () => {
        const header = document.querySelector('[data-testid="knowledge-eval-run-header"]');
        return header && header.innerText.includes("completed");
      },
      null,
      { timeout: 180000, polling: 1000 },
    );
    await page.waitForTimeout(1200);
    await shot(page, "step-14-run-detail-2");
    const header2 = await page.evaluate(
      () => (document.querySelector('[data-testid="knowledge-eval-run-header"]')?.innerText ?? "").replace(/\s+/g, " "),
    );
    record("第二个配置运行完成", header2.includes("2 个配置"), header2);

    // ---------------------------------------------------------------- 13. 对比
    await page
      .locator('[data-testid="knowledge-eval-page"] button')
      .filter({ hasText: "检索评测台" })
      .first()
      .click();
    await page.waitForTimeout(1000);
    await page.getByRole("tab", { name: "对比", exact: true }).click();
    const runIds = await page.$eval('select[aria-label="基线运行"]', (select) =>
      Array.from(select.options).map((option) => option.value).filter(Boolean),
    );
    if (runIds.length >= 2) {
      // 列表按「新的在前」渲染：取最近两次运行做 base / candidate。
      await page.getByLabel("基线运行").selectOption(runIds[1]);
      await page.getByLabel("候选运行").selectOption(runIds[0]);
      await page.waitForFunction(
        () => {
          const button = Array.from(document.querySelectorAll("button")).find(
            (item) => item.textContent.trim() === "对比",
          );
          return Boolean(button && !button.disabled);
        },
        null,
        { timeout: 10000 },
      );
      await page.getByRole("button", { name: "对比", exact: true }).click();
      await waitBodyText(page, "差值", 30000);
      await page.waitForTimeout(800);
      await shot(page, "step-15-compare");
      const compareText = await page.evaluate(
        () => (document.querySelector('[data-testid="knowledge-eval-compare"]')?.innerText ?? document.body.innerText).replace(/\s+/g, " ").slice(-700),
      );
      record("两次运行对比", true, { runIds, tail: compareText.slice(-300) });
    } else {
      record("两次运行对比", false, { runIds });
    }
  } catch (error) {
    record("脚本中断", false, String(error && error.stack ? error.stack : error));
    try {
      await shot(page, "step-99-failure", false);
    } catch {
      /* ignore */
    }
  } finally {
    const summary = {
      at: now(),
      baseUrl: BASE,
      kbName: KB_NAME,
      datasetName: DS_NAME,
      steps,
      consoleErrors: consoleErrors.slice(0, 40),
      httpErrors: httpErrors.slice(0, 40),
      capturedMetricsSpecs: captured.metrics.at(-1)?.length ?? 0,
      reports: captured.report,
      searches: captured.search.map((s) => ({
        modeUsed: s.modeUsed,
        degraded: s.degraded,
        hits: (s.hits || []).length,
        top: (s.hits?.[0]?.content || "").replace(/\s+/g, " ").slice(0, 120),
      })),
    };
    fs.writeFileSync(path.join(OUT, "e2e-fullflow-report.json"), JSON.stringify(summary, null, 2));
    const failed = steps.filter((s) => !s.ok);
    console.log(`\nSUMMARY: ${steps.length - failed.length}/${steps.length} passed; consoleErrors=${consoleErrors.length}; httpErrors=${httpErrors.length}`);
    await browser.close();
    process.exit(failed.length ? 1 : 0);
  }
}

main();
