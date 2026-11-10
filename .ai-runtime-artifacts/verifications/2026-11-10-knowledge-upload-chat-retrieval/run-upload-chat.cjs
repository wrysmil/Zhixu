/* eslint-disable */
/**
 * 聚焦链路：上传新文档 → 入库解析 → 分块 → 聊天检索验证
 * 输出：本目录 step-*.png、upload-chat-report.json
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
const DOC = path.join(OUT, "e2e-release.md");
const KB_NAME = "E2E 全流程回归知识库";
const DOC_TITLE = "e2e-release";
const QUESTION = "知识库里的发布手册：灰度第一批放多少流量、观察多久？P1 故障升级给谁、电话是多少？";

const steps = [];
const consoleErrors = [];

function record(name, ok, detail) {
  steps.push({ step: name, ok, detail, at: new Date().toISOString() });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name} :: ${JSON.stringify(detail)}`);
}

async function shot(page, name, fullPage = false) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage });
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, locale: "zh-CN" });
  page.setDefaultTimeout(30000);
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));

  try {
    // 1) 打开知识库并进入详情
    await page.goto(`${BASE}/#/knowledge`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="knowledge-page"]');
    await page.waitForTimeout(1500);
    await page.locator('[data-testid="knowledge-kb-list"] li').filter({ hasText: KB_NAME }).first().click();
    await page.waitForTimeout(800);

    // 2) 上传新文档
    const rows = () => page.locator("tbody tr");
    const before = await rows().count();
    await page.locator('[data-testid="knowledge-page"] input[type="file"]').first().setInputFiles(DOC);
    await page.waitForFunction(
      (count) => document.querySelectorAll("tbody tr").length > count,
      before,
      { timeout: 60000, polling: 1000 },
    );
    await shot(page, "step-01-uploaded");

    // 3) 等待解析/入库完成
    await page.waitForFunction(
      () => {
        const row = Array.from(document.querySelectorAll("tbody tr")).find((item) =>
          item.innerText.includes("e2e-release"),
        );
        return Boolean(row && row.innerText.includes("就绪"));
      },
      null,
      { timeout: 180000, polling: 1000 },
    );
    await page.waitForTimeout(800);
    const docRow = await page
      .locator("tbody tr")
      .filter({ hasText: DOC_TITLE })
      .first()
      .innerText();
    await shot(page, "step-02-ready");
    record("上传并入库解析 ready", true, docRow.replace(/\s+/g, " "));

    // 4) 分块预览
    await page
      .locator("tbody tr")
      .filter({ hasText: DOC_TITLE })
      .first()
      .getByRole("button", { name: "查看分块" })
      .click();
    await page.waitForSelector('[data-testid="knowledge-chunk-preview"]');
    await page.waitForSelector('[data-testid="knowledge-chunk"]');
    const chunks = await page.$$eval('[data-testid="knowledge-chunk"]', (els) => els.map((el) => el.innerText));
    await shot(page, "step-03-chunks");
    const joined = chunks.join("\n---\n");
    const facts = [
      { key: "5% 流量", ok: joined.includes("5%") },
      { key: "15 分钟", ok: joined.includes("15 分钟") },
      { key: "P1 电话", ok: joined.includes("021-6688-9900") },
      { key: "发布窗口", ok: joined.includes("10:00–16:00") },
    ];
    record("分块内容含关键事实", facts.every((item) => item.ok), {
      chunkCount: chunks.length,
      facts,
    });
    await page.getByRole("button", { name: "关闭" }).click();

    // 5) 聊天提问
    await page.goto(`${BASE}/#/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
    await page.locator("textarea").first().fill(QUESTION);
    await page.locator("textarea").first().press("Enter");

    let retrieved = false;
    try {
      await page.waitForFunction(
        () => {
          const text = document.body.innerText;
          return text.includes("5%") && text.includes("021-6688-9900");
        },
        null,
        { timeout: 180000, polling: 1000 },
      );
      retrieved = true;
    } catch {
      /* 记录失败，不中断 */
    }
    await page.waitForTimeout(2500);
    await shot(page, "step-04-chat-answer");
    const chatTail = await page.evaluate(() => {
      const main = document.querySelector("main") || document.body;
      return main.innerText.slice(-2500);
    });
    const answerChecks = {
      percent: chatTail.includes("5%"),
      observe: chatTail.includes("15 分钟"),
      escalation: chatTail.includes("值班二号"),
      phone: chatTail.includes("021-6688-9900"),
      citation: chatTail.includes("来源：知识库") || chatTail.includes("doc://"),
    };
    record("聊天检索到文档内容", retrieved && Object.values(answerChecks).filter(Boolean).length >= 4, {
      retrieved,
      answerChecks,
      answerTail: chatTail.replace(/\s+/g, " ").slice(-500),
    });

    // 6) 明确验证引用来源（二次追问，避免首答带入其他上下文）
    await page.locator("textarea").first().fill("只用知识库回答：发布窗口允许的时间段是什么？");
    await page.locator("textarea").first().press("Enter");
    let windowHit = false;
    try {
      await page.waitForFunction(
        () => document.body.innerText.includes("10:00") && document.body.innerText.includes("16:00"),
        null,
        { timeout: 120000, polling: 1000 },
      );
      windowHit = true;
    } catch {
      /* 记录失败 */
    }
    await page.waitForTimeout(2000);
    await shot(page, "step-05-chat-window");
    record("二次追问命中发布窗口", windowHit, { windowHit });
  } catch (error) {
    record("脚本中断", false, String(error && error.stack ? error.stack : error));
    try {
      await shot(page, "step-99-failure");
    } catch {
      /* ignore */
    }
  } finally {
    const summary = { at: new Date().toISOString(), kbName: KB_NAME, document: DOC_TITLE, steps, consoleErrors };
    fs.writeFileSync(path.join(OUT, "upload-chat-report.json"), JSON.stringify(summary, null, 2));
    const failed = steps.filter((item) => !item.ok);
    console.log(`SUMMARY: ${steps.length - failed.length}/${steps.length} passed; consoleErrors=${consoleErrors.length}`);
    await browser.close();
  }
})();
