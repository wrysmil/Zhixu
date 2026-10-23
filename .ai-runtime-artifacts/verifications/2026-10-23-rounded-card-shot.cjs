const { chromium } = require("C:/Users/Huangqh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const BASE = `http://127.0.0.1:5173/#/?bootstrapSecret=${process.env.ZHIXU_BOOTSTRAP_SECRET}`;
const OUT = "D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications";

async function shoot(browser, { width, height, mode, tag }) {
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
  await context.addInitScript(([mode]) => {
    localStorage.setItem("zhixu-webui.theme", mode);
    localStorage.setItem("zhixu-webui.appearance", JSON.stringify({
      schemaVersion: 1, mode, presetId: "graphite", custom: null,
      uiFont: { id: "system", family: "" }, codeFont: { id: "default", family: "" },
    }));
  }, [mode]);
  const page = await context.newPage();
  await page.goto(BASE);
  await page.waitForTimeout(1800);
  const probe = await page.evaluate(() => {
    const card = document.querySelector(".app-shell-card");
    const canvas = document.querySelector(".app-shell-canvas");
    const rect = card.getBoundingClientRect();
    const style = getComputedStyle(card);
    return {
      radius: style.borderRadius,
      border: style.borderTopWidth + " " + style.borderTopColor,
      card: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
      cardBg: style.backgroundColor,
      canvasBg: canvas ? getComputedStyle(canvas).backgroundColor : null,
    };
  });
  await page.screenshot({ path: `${OUT}/2026-10-23-${tag}.png` });
  console.log(tag, JSON.stringify(probe));
  await context.close();
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  await shoot(browser, { width: 1600, height: 900, mode: "light", tag: "rounded-card-light" });
  await shoot(browser, { width: 1600, height: 900, mode: "dark", tag: "rounded-card-dark" });
  await shoot(browser, { width: 1000, height: 720, mode: "light", tag: "rounded-card-narrow" });
  await browser.close();
})();
