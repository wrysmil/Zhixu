/* Verify the patched unpacked extension auto-injects on page load and survives a refresh.
 * Loads the extension into a persistent Chromium profile, logs in, then reloads.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

const PLAYWRIGHT = "D:/studyspace/project/Zhixu/webui/node_modules/playwright-core";
const CHROME = path.join(process.env.LOCALAPPDATA, "ms-playwright/chromium-1243/chrome-win64/chrome.exe");
const EXT = path.join(process.env.LOCALAPPDATA, "qf-langfuse-zh/langfuse-zh-ui");
const OUT = "D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-20-langfuse-zh-ui";
const BASE = "http://127.0.0.1:3000";
const EMAIL = process.env.LF_EMAIL;
const PASSWORD = process.env.LF_PASSWORD;

const { chromium } = require(PLAYWRIGHT);

async function sidebarText(page) {
  return page.evaluate(() => {
    const links = [...document.querySelectorAll('a[href*="/project/"]')]
      .map((a) => (a.innerText || "").trim())
      .filter(Boolean);
    return links.slice(0, 12);
  });
}

async function waitForChineseOrEnglish(page, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const labels = await sidebarText(page);
    const zh = labels.filter((t) => /[\u4e00-\u9fff]/.test(t));
    if (zh.length >= 3) return { state: "zh", labels };
    await page.waitForTimeout(1000);
  }
  return { state: "en", labels: await sidebarText(page) };
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "lf-zh-ext-"));
  let context;
  try {
    context = await chromium.launchPersistentContext(profile, {
      headless: false,
      executablePath: CHROME,
      viewport: { width: 1440, height: 900 },
      args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
    });
  } catch (err) {
    console.error("LAUNCH FAILED:", err.message);
    process.exit(2);
  }

  const page = context.pages()[0] || (await context.newPage());
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(5000);

  if (page.url().includes("/auth/sign-in")) {
    await page.waitForSelector('input[name="email"]', { timeout: 30000 });
    await page.fill('input[name="email"]', EMAIL);
    await page.fill('input[name="password"]', PASSWORD);
    await page.getByRole("button", { name: /Sign in|登录/, exact: false }).click();
    await page.waitForURL((url) => !url.pathname.startsWith("/auth/"), { timeout: 60000 });
  }

  await page.goto(`${BASE}/project/zhixu/traces`, { waitUntil: "domcontentloaded" });

  // First load (login redirect reloads the document; a content script should have run).
  const first = await waitForChineseOrEnglish(page, 20000);
  console.log("after login:", first.state, JSON.stringify(first.labels.slice(0, 8)));
  await page.screenshot({ path: path.join(OUT, "autoinject-after-login.png") });

  // Hard refresh: with content_scripts the injection must come back without any click.
  await page.reload({ waitUntil: "domcontentloaded" });
  const refreshed = await waitForChineseOrEnglish(page, 20000);
  console.log("after reload:", refreshed.state, JSON.stringify(refreshed.labels.slice(0, 8)));
  await page.screenshot({ path: path.join(OUT, "autoinject-after-reload.png") });

  console.log(refreshed.state === "zh" ? "PASS" : "FAIL");
  await context.close();
  process.exit(refreshed.state === "zh" ? 0 : 1);
})().catch((err) => {
  console.error("TEST FAILED:", err);
  process.exit(2);
});
