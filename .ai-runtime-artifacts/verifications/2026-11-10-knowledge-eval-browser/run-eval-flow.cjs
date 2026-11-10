/* eslint-disable */
/**
 * 知识检索评测台浏览器回归（当前已提交构建）
 *
 * 覆盖：使用指南/模板下载、模板导入、表单建集、标注切换、隐私门禁取消、
 *      两配置运行、报告（指标/逐题/口径）、第二次运行、对比、删除导入数据集。
 *
 * 输出：本目录 eval-*.png、eval-report.json、template.csv
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
const MANUAL_NAME = "E2E 评测回归数据集";
const IMPORT_NAME = "knowledge-eval-dataset-template";

const steps = [];
const consoleErrors = [];
const httpErrors = [];
const reports = [];
const compares = [];

function record(name, ok, detail) {
  steps.push({ step: name, ok, detail, at: new Date().toISOString() });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name} :: ${JSON.stringify(detail)}`);
}

async function shot(page, name, fullPage = false) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage });
}

function datasetItem(page, name) {
  return page
    .locator('[data-testid="knowledge-eval-datasets"] li')
    .filter({ hasText: name })
    .first();
}

async function waitCompleted(page, timeout = 180000) {
  await page.waitForSelector('[data-testid="knowledge-eval-run-header"]', { timeout: 30000 });
  await page.waitForFunction(
    () => {
      const header = document.querySelector('[data-testid="knowledge-eval-run-header"]');
      return header && header.innerText.includes("completed");
    },
    null,
    { timeout, polling: 1000 },
  );
  await page.waitForTimeout(1200);
}

async function back(page) {
  await page
    .locator('[data-testid="knowledge-eval-page"] button')
    .filter({ hasText: "检索评测台" })
    .first()
    .click();
  await page.waitForTimeout(900);
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, locale: "zh-CN" });
  page.setDefaultTimeout(30000);
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));
  page.on("response", async (res) => {
    try {
      const url = res.url();
      if (res.status() >= 400) httpErrors.push({ url, status: res.status() });
      if (res.ok() && /\/knowledge\/runs\/[^/]+\/report/.test(url)) reports.push(await res.json());
      if (res.ok() && url.includes("/knowledge/compare")) compares.push(await res.json());
    } catch {
      /* ignore non-json */
    }
  });

  try {
    // 0) 打开评测台 + 幂等清理
    await page.goto(`${BASE}/#/knowledge-eval`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="knowledge-eval-page"]');
    await page.waitForTimeout(1500);
    for (const name of [MANUAL_NAME, IMPORT_NAME, "template"]) {
      const item = datasetItem(page, name);
      for (let i = 0; i < 3 && (await item.count()) > 0; i += 1) {
        const before = await item.count();
        await item.getByRole("button", { name: "删除数据集" }).click();
        await page.waitForTimeout(1200);
        if ((await item.count()) >= before) break;
      }
    }
    await shot(page, "eval-01-list", true);
    record("评测台列表加载", true, { metricsLoaded: true });

    // 1) 使用指南 + 模板下载
    await page.getByRole("button", { name: "使用指南" }).click();
    await page.waitForSelector('[role="dialog"]');
    const manualText = await page.locator('[role="dialog"]').innerText();
    const manualChecks = ["建数据集", "选检索配置", "发起运行", "读报告", "做对比"].map((k) => manualText.includes(k));
    await shot(page, "eval-02-manual");
    const templatePath = path.join(OUT, `${IMPORT_NAME}.csv`);
    let downloadName = "";
    let downloadSize = 0;
    try {
      const downloadPromise = page.waitForEvent("download", { timeout: 15000 });
      await page.locator('[role="dialog"]').getByRole("button", { name: "下载模板（CSV）" }).click();
      const download = await downloadPromise;
      await download.saveAs(templatePath);
      downloadName = download.suggestedFilename();
      downloadSize = fs.statSync(templatePath).size;
    } catch (error) {
      record("模板下载", false, String(error));
    }
    await page.locator('[role="dialog"]').getByRole("button", { name: "关闭", exact: true }).click();
    await page.waitForTimeout(500);
    record("使用指南五步 + 模板下载", manualChecks.every(Boolean) && downloadSize > 0, {
      steps: manualChecks,
      filename: downloadName,
      size: downloadSize,
    });

    // 2) 模板导入
    if (fs.existsSync(templatePath)) {
      await page
        .locator('[data-testid="knowledge-eval-page"] input[type="file"][accept*=".csv"]')
        .setInputFiles(templatePath);
      await page.waitForFunction(
        (name) =>
          Array.from(document.querySelectorAll('[data-testid="knowledge-eval-datasets"] li')).some((el) =>
            el.innerText.includes(name),
          ),
        IMPORT_NAME,
        { timeout: 30000, polling: 500 },
      );
      const imported = datasetItem(page, IMPORT_NAME);
      const importedText = await imported.innerText();
      await shot(page, "eval-03-imported");
      record("模板导入数据集", importedText.includes("3 条查询"), importedText.replace(/\s+/g, " ").slice(0, 160));
    } else {
      record("模板导入数据集", false, "模板文件未下载成功");
    }

    // 3) 表单建集（一条关键词标注 + 一条模型判定）
    await page.getByRole("button", { name: "新建数据集" }).first().click();
    const form = page.locator("form").filter({ has: page.getByRole("button", { name: "新建数据集" }) });
    await form.getByLabel("数据集名称").fill(MANUAL_NAME);
    await form.getByLabel("备注").fill("评测台浏览器回归");
    await form.getByLabel("查询").first().fill("线上版本出问题了怎么回滚");
    await form.getByLabel("期望关键词").first().fill("kubectl rollout undo");
    await form.getByRole("button", { name: "添加查询" }).click();
    await form.getByLabel("查询").nth(1).fill("退款多久到账");
    await form.getByRole("button", { name: "新建数据集" }).click();
    await page.waitForFunction(
      (name) =>
        Array.from(document.querySelectorAll('[data-testid="knowledge-eval-datasets"] li')).some((el) =>
          el.innerText.includes(name),
        ),
      MANUAL_NAME,
      { timeout: 30000, polling: 500 },
    );
    await page.waitForTimeout(600);
    const manualItem = datasetItem(page, MANUAL_NAME);
    const datasetText = await manualItem.innerText();
    await shot(page, "eval-04-datasets");
    record("表单建集与标注推断", datasetText.includes("关键词标注") && datasetText.includes("模型判定"), {
      hasExpect: datasetText.includes("关键词标注"),
      hasJudge: datasetText.includes("模型判定"),
    });

    // 4) 隐私门禁：judge 用例发起前必须确认，取消不发
    await page.getByRole("tab", { name: "运行", exact: true }).click();
    await page.getByLabel("数据集", { exact: true }).selectOption({ label: MANUAL_NAME });
    await page.getByRole("button", { name: "发起运行" }).click();
    await page.waitForSelector('[role="dialog"]');
    const privacyText = await page.locator('[role="dialog"]').innerText();
    await shot(page, "eval-05-privacy");
    await page.locator('[role="dialog"]').getByRole("button", { name: "取消" }).click();
    await page.waitForTimeout(800);
    const stillList = (await page.locator('[data-testid="knowledge-eval-run-header"]').count()) === 0;
    record("隐私门禁弹出且取消不发", privacyText.includes("模型判定会把命中块正文发给模型") && stillList, {
      dialogShown: true,
      stayedOnList: stillList,
    });

    // 5) 把 judge 用例标成关键词标注（避免真调 judge 模型）
    await page.getByRole("tab", { name: "数据集", exact: true }).click();
    const judgeRow = datasetItem(page, MANUAL_NAME).locator("li").filter({ hasText: "退款多久到账" });
    await judgeRow.getByLabel("期望关键词").fill("到账");
    await judgeRow.getByRole("button", { name: "保存" }).click();
    await page.waitForFunction(
      () => {
        const row = Array.from(document.querySelectorAll("li")).find((el) => el.innerText.includes("退款多久到账"));
        return row && row.innerText.includes("关键词标注") && !row.innerText.includes("模型判定");
      },
      null,
      { timeout: 30000, polling: 500 },
    );
    await shot(page, "eval-06-labeled");
    record("标注来源切换（模型判定→关键词标注）", true, {});

    // 6) 两配置运行 → 报告
    await page.getByRole("tab", { name: "运行", exact: true }).click();
    await page.getByLabel("数据集", { exact: true }).selectOption({ label: MANUAL_NAME });
    await page.getByRole("button", { name: "添加配置" }).click();
    await page.getByLabel("配置 ID").nth(1).fill("keyword");
    await page.getByLabel("配置名称").nth(1).fill("Keyword");
    await page.getByLabel("检索模式").nth(1).selectOption("keyword");
    await page.getByRole("button", { name: "发起运行" }).click();
    await waitCompleted(page);
    await shot(page, "eval-07-report", true);
    const reportDom = await page.evaluate(() => {
      const overview = document.querySelector('[data-testid="knowledge-eval-overview"]')?.innerText ?? "";
      const cases = document.querySelector('[data-testid="knowledge-eval-case-results"]')?.innerText ?? "";
      return { overview: overview.replace(/\s+/g, " "), cases: cases.replace(/\s+/g, " ") };
    });
    const metricLabels = ["准确率@K", "召回率@K", "命中率@K", "MRR", "NDCG@K"].map((label) =>
      reportDom.overview.includes(label),
    );
    const report = reports.at(-1);
    record("两配置运行出报告", metricLabels.every(Boolean) && (report?.results?.length ?? 0) >= 4, {
      metrics: metricLabels,
      configs: report?.configs?.length,
      results: report?.results?.length,
      agg: report?.metrics?.expect?.metrics,
    });

    // 7) 口径区展开
    await page.locator("summary").filter({ hasText: "指标是怎么算的" }).click();
    await page.waitForSelector('[data-testid="knowledge-eval-metric-guide"]');
    const guideText = await page.locator('[data-testid="knowledge-eval-metric-guide"]').innerText();
    await shot(page, "eval-08-guide", true);
    record("指标口径区来自后端", guideText.includes("相关块数") && guideText.includes("未评率"), {
      length: guideText.length,
    });

    // 8) 第二次运行（同配置）→ 对比
    await back(page);
    await page.getByRole("button", { name: "发起运行" }).click();
    await waitCompleted(page);
    await back(page);
    await page.getByRole("tab", { name: "对比", exact: true }).click();
    const optionValues = await page.$eval('select[aria-label="基线运行"]', (select) =>
      Array.from(select.options).map((option) => option.value).filter(Boolean),
    );
    if (optionValues.length >= 2) {
      await page.getByLabel("基线运行").selectOption(optionValues[1]);
      await page.getByLabel("候选运行").selectOption(optionValues[0]);
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
      await page.waitForFunction(() => document.body.innerText.includes("差值"), null, {
        timeout: 30000,
        polling: 500,
      });
      await page.waitForTimeout(800);
      const compareText = await page.locator('[data-testid="knowledge-eval-compare"]').innerText();
      await shot(page, "eval-09-compare", true);
      const compare = compares.at(-1);
      record("两次运行对比", compareText.includes("退化查询") && (compare?.metrics?.length ?? 0) === 7, {
        metricRows: compare?.metrics?.length,
        regressions: compare?.regressions?.length,
        textTail: compareText.replace(/\s+/g, " ").slice(-160),
      });
    } else {
      record("两次运行对比", false, { optionValues });
    }

    // 9) 删除导入数据集
    await page.getByRole("tab", { name: "数据集", exact: true }).click();
    const imported = datasetItem(page, IMPORT_NAME);
    if ((await imported.count()) > 0) {
      await imported.getByRole("button", { name: "删除数据集" }).click();
      await page.waitForTimeout(1500);
      await shot(page, "eval-10-deleted");
      record("删除导入数据集", (await imported.count()) === 0, {});
    } else {
      record("删除导入数据集", false, "导入数据集不存在");
    }

    // 10) 刷新按钮
    await page.getByRole("button", { name: "刷新" }).first().click();
    await page.waitForTimeout(1200);
    record("列表刷新无报错", (await page.locator('[role="alert"]').count()) === 0, {});
  } catch (error) {
    record("脚本中断", false, String(error && error.stack ? error.stack : error));
    try {
      await shot(page, "eval-99-failure");
    } catch {
      /* ignore */
    }
  } finally {
    const summary = {
      at: new Date().toISOString(),
      steps,
      consoleErrors,
      httpErrors,
      reports: reports.map((report) => ({
        id: report.id,
        status: report.status,
        configs: report.configs?.length,
        results: report.results?.length,
        agg: report.metrics?.expect?.metrics,
        progress: report.progress,
      })),
      compares,
    };
    fs.writeFileSync(path.join(OUT, "eval-report.json"), JSON.stringify(summary, null, 2));
    const failed = steps.filter((item) => !item.ok);
    console.log(`SUMMARY: ${steps.length - failed.length}/${steps.length} passed; consoleErrors=${consoleErrors.length}; httpErrors=${httpErrors.length}`);
    await browser.close();
  }
})();
