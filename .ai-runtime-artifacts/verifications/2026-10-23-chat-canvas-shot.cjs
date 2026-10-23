const { chromium } = require("C:/Users/Huangqh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const BASE = `http://127.0.0.1:5173/#/?bootstrapSecret=${process.env.ZHIXU_BOOTSTRAP_SECRET}`;
const OUT = "D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-10-23-chat-canvas-white.png";

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 });
  await context.addInitScript(() => {
    localStorage.setItem("zhixu-webui.theme", "light");
    localStorage.setItem("zhixu-webui.appearance", JSON.stringify({
      schemaVersion: 1, mode: "light", presetId: "graphite", custom: null,
      uiFont: { id: "system", family: "" }, codeFont: { id: "default", family: "" },
    }));
  });
  const page = await context.newPage();
  await page.goto(BASE);
  await page.waitForTimeout(2000);
  await page.screenshot({ path: OUT });
  console.log(OUT);
  await browser.close();
})();
