const { chromium } = require("C:/Users/Huangqh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const sharp = require("C:/Users/Huangqh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp");

const BASE = `http://127.0.0.1:5173/#/?bootstrapSecret=${process.env.ZHIXU_BOOTSTRAP_SECRET}`;
const OUT = "D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications";
const TAG = process.argv[2] || "after";
const THEME = process.argv[3] || "light";

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  await context.addInitScript((theme) => {
    localStorage.setItem("zhixu-webui.theme", theme);
  }, THEME);
  const page = await context.newPage();
  await page.goto(BASE);
  await page.waitForTimeout(1200);
  await page.getByText(process.argv[4] || "b3a107e4-7440-4f03-9180-9f112f94b621").first().click();
  await page.waitForTimeout(2200);

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
    return {
      theme: document.documentElement.className,
      layers: [
        "body",
        ".app-shell-canvas",
        ".app-shell-card",
        ".workbench-canvas",
        ".workbench-pane",
        ".thread-viewport",
        ".thread-message-viewport",
        "[data-testid='thread-composer-dock']",
        ".thread-composer-surface",
        "footer",
      ].map(pick),
    };
  });

  const file = `${OUT}/2026-10-21-chat-${TAG}-${THEME}.png`;
  await page.screenshot({ path: file });
  const { data, info } = await sharp(file).raw().toBuffer({ resolveWithObject: true });
  const at = (x, y) => {
    const i = (y * info.width + x) * info.channels;
    return `${data[i]},${data[i + 1]},${data[i + 2]}`;
  };
  const sample = (x, rows) => rows.filter((y) => Number.isInteger(y)).map((y) => ({ y, rgb: at(x, y) }));
  const composerRect = report.layers.at(-2)?.rect ?? {};

  console.log(JSON.stringify({
    ...report,
    pixels: {
      topRight: sample(1100, [40, 45, 50, 52, 53, 55, 58, 61, 64, 70, 80]),
      composerAbove: sample(860, composerRect.y ? [composerRect.y - 12, composerRect.y - 6, composerRect.y - 2, composerRect.y, composerRect.y + 4] : []),
      bottom: sample(860, [870, 880, 886, 890, 895]),
    },
  }, null, 2));
  await browser.close();
})();
