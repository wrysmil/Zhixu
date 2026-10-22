const { chromium } = require("C:/Users/Huangqh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const BASE = "http://127.0.0.1:5173/#/?bootstrapSecret=zhixu-ui-check-2026";
const OUT = "D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications";

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  await context.addInitScript(() => {
    localStorage.setItem("zhixu-webui.theme", "dark");
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto(BASE);
  await page.getByTestId("start-dock-toggle").waitFor();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/2026-10-20-shell-wrap-dark-2.png` });

  await page.getByTestId("start-dock-toggle").click();
  await page.getByTestId("start-dock").waitFor();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/2026-10-20-dock-launcher-dark.png` });

  const theme = await page.evaluate(() => ({
    hasDark: document.documentElement.classList.contains("dark"),
    canvas: getComputedStyle(document.querySelector(".app-shell-canvas")).backgroundColor,
    card: getComputedStyle(document.querySelector("main")).backgroundColor,
  }));
  console.log(JSON.stringify({ theme, errors }, null, 2));
  await browser.close();
})();
