const { chromium } = require("C:/Users/Huangqh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const BASE = "http://127.0.0.1:5173/#/?bootstrapSecret=zhixu-ui-check-2026";
const OUT = "D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications";

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });

  await page.goto(BASE);
  await page.getByTestId("start-dock-toggle").waitFor();
  await page.waitForTimeout(600);

  const shell = await page.evaluate(() => {
    const main = document.querySelector("main");
    const aside = document.querySelector('[data-testid="host-sidebar-flow"]');
    const rect = (node) => {
      const r = node.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    };
    return {
      viewport: { w: innerWidth, h: innerHeight },
      main: rect(main),
      aside: aside ? rect(aside) : null,
      canvas: getComputedStyle(document.querySelector(".app-shell-canvas")).backgroundColor,
      cardRadius: getComputedStyle(main).borderRadius,
    };
  });
  await page.screenshot({ path: `${OUT}/2026-10-20-shell-wrap-light.png` });

  await page.getByTestId("start-dock-toggle").click();
  await page.getByTestId("start-dock").waitFor();
  await page.waitForTimeout(500);
  const dock = await page.evaluate(() => {
    const node = document.querySelector('[data-testid="start-dock"]');
    const card = document.querySelector('[data-testid="dock-card-files"]');
    const r = node.getBoundingClientRect();
    const c = card.getBoundingClientRect();
    return {
      dock: { w: Math.round(r.width), h: Math.round(r.height) },
      card: { w: Math.round(c.width), h: Math.round(c.height) },
      surface: getComputedStyle(document.querySelector('[data-testid="dock-launcher"]')).backgroundImage,
    };
  });
  await page.screenshot({ path: `${OUT}/2026-10-20-dock-launcher.png` });

  await page.getByTestId("start-dock-terminal-toggle").click();
  await page.waitForTimeout(1200);
  const terminalRequest = await page.evaluate(() => ({
    tabs: document.querySelectorAll('[data-testid="start-dock"] [role="tab"]').length,
    launcher: Boolean(document.querySelector('[data-testid="dock-launcher"]')),
  }));
  await page.screenshot({ path: `${OUT}/2026-10-20-dock-terminal.png` });

  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/2026-10-20-shell-wrap-dark.png` });

  console.log(JSON.stringify({ shell, dock, terminalRequest, errors }, null, 2));
  await browser.close();
})();
