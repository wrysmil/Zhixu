/* Coverage probe: inject zleo-ai/langfuse-zh-ui dictionary+translate.js into the
 * local Langfuse 3.174.1 UI and report how many visible controls switch to Chinese.
 * Credentials come from LF_EMAIL / LF_PASSWORD so they never land in this file.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const PLAYWRIGHT = process.env.PLAYWRIGHT_CORE
  || "D:/studyspace/project/Zhixu/webui/node_modules/playwright-core";
const CHROME = process.env.CHROME_PATH
  || path.join(process.env.LOCALAPPDATA, "ms-playwright/chromium-1243/chrome-win64/chrome.exe");
const EXT = process.env.EXT_DIR
  || path.join(process.env.LOCALAPPDATA, "qf-langfuse-zh/langfuse-zh-ui");
const OUT = process.env.OUT_DIR
  || "D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-20-langfuse-zh-ui";
const BASE = process.env.LF_BASE_URL || "http://127.0.0.1:3000";
const EMAIL = process.env.LF_EMAIL;
const PASSWORD = process.env.LF_PASSWORD;

const { chromium } = require(PLAYWRIGHT);

const SELECTOR = [
  'a[href]', 'button', '[role="tab"]', '[role="menuitem"]',
  'th', '[role="columnheader"]', 'h1', 'h2', 'h3', 'label',
  '[data-sidebar="group-label"]',
].join(",");

async function collect(page) {
  return page.evaluate((selector) => {
    const rows = [];
    for (const el of document.querySelectorAll(selector)) {
      const text = (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ");
      if (!text || text.length > 60) continue;
      if (el.closest('pre,code,td,[role="cell"],.cm-editor,.monaco-editor')) continue;
      rows.push(text);
    }
    return rows;
  }, SELECTOR);
}

function diff(before, after) {
  const changed = [];
  const same = [];
  const n = Math.min(before.length, after.length);
  for (let i = 0; i < n; i += 1) {
    if (before[i] === after[i]) same.push(after[i]);
    else changed.push([before[i], after[i]]);
  }
  return { changed, same, beforeCount: before.length, afterCount: after.length };
}

async function inject(page) {
  await page.addScriptTag({ path: path.join(EXT, "dictionary.js") });
  await page.addScriptTag({ path: path.join(EXT, "translate.js") });
  return page.evaluate(() => {
    if (!globalThis.langfuseZhUI) return { api: false };
    const enabled = globalThis.langfuseZhUI.setEnabled(true);
    const keys = globalThis.langfuseZhDictionary
      ? Object.keys(globalThis.langfuseZhDictionary).length
      : 0;
    return { api: true, enabled, dictionaryKeys: keys, title: document.title, url: location.pathname };
  });
}

async function restore(page) {
  await page.evaluate(() => globalThis.langfuseZhUI?.setEnabled(false));
}

function report(label, result) {
  console.log(`\n=== ${label} ===`);
  console.log(`before=${result.beforeCount} after=${result.afterCount} changed=${result.changed.length}`);
  console.log("changed:");
  for (const [a, b] of result.changed.slice(0, 60)) console.log(`  ${JSON.stringify(a)} -> ${JSON.stringify(b)}`);
  const englishLeft = result.same.filter((t) => /^[A-Za-z][A-Za-z0-9 .,:&'()\/-]{2,}$/.test(t));
  console.log(`unchanged-English=${englishLeft.length}`);
  for (const t of englishLeft.slice(0, 60)) console.log(`  ${JSON.stringify(t)}`);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: CHROME });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (err) => errors.push(String(err)));

  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(6000);
  console.log("landing:", page.url(), "| title:", await page.title());
  await page.screenshot({ path: path.join(OUT, "login-en.png") });

  const loginBefore = await collect(page);
  const loginInject = await inject(page);
  console.log("login inject:", JSON.stringify(loginInject));
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(OUT, "login-zh.png") });
  const loginAfter = await collect(page);
  report("login page", diff(loginBefore, loginAfter));
  await restore(page);

  if (!/\/project\//.test(new URL(page.url()).pathname)) {
    await page.waitForSelector('input[name="email"]', { timeout: 30000 });
    await page.fill('input[name="email"]', EMAIL);
    await page.fill('input[name="password"]', PASSWORD);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.waitForURL((url) => !url.pathname.startsWith("/auth/"), { timeout: 60000 });
  }
  const startPath = process.env.LF_START_PATH || "/project/zhixu/traces";
  if (new URL(page.url()).pathname !== startPath) {
    await page.goto(`${BASE}${startPath}`, { waitUntil: "domcontentloaded" });
  }
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(4000);
  console.log("\ndashboard:", page.url(), "| title:", await page.title());
  await page.screenshot({ path: path.join(OUT, "dashboard-en.png") });

  const dashBefore = await collect(page);
  const dashInject = await inject(page);
  console.log("dashboard inject:", JSON.stringify(dashInject));
  await page.waitForTimeout(1000);
  await page.screenshot({ path: path.join(OUT, "dashboard-zh.png") });
  const dashAfter = await collect(page);
  report("dashboard", diff(dashBefore, dashAfter));

  console.log("\npage errors:", errors.length ? errors.slice(0, 5) : "none");
  await browser.close();
})().catch((err) => {
  console.error("PROBE FAILED:", err);
  process.exit(1);
});
