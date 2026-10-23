const { chromium } = require("C:/Users/Huangqh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const BASE = `http://127.0.0.1:5173/#/?bootstrapSecret=${process.env.ZHIXU_BOOTSTRAP_SECRET}`;
const OUT = "D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications";
const TAG = process.argv[2] || "before";

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  await context.addInitScript(() => {
    localStorage.setItem("zhixu-webui.theme", "light");
  });
  const page = await context.newPage();
  await page.goto(BASE);
  await page.waitForTimeout(1500);

  const report = await page.evaluate(() => {
    const pick = (selector) => {
      const node = document.querySelector(selector);
      if (!node) return null;
      const style = getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return {
        selector,
        image: style.backgroundImage,
        color: style.backgroundColor,
        rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          w: Math.round(rect.width),
          h: Math.round(rect.height),
        },
      };
    };
    return [
      "body",
      ".app-shell-canvas",
      ".app-shell-card",
      ".workspace-navigation",
      ".workbench-canvas",
      ".workbench-pane",
      ".thread-viewport",
      ".thread-composer-dock",
      ".thread-composer-surface",
      ".bg-settings-canvas",
    ].map(pick);
  });

  await page.screenshot({ path: `${OUT}/2026-10-21-canvas-seam-${TAG}-light.png` });
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/2026-10-21-canvas-seam-${TAG}-dark.png` });

  console.log(JSON.stringify(report, null, 2));
  await browser.close();
})();
