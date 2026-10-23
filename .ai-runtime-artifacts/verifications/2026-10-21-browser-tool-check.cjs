const { chromium } = require("C:/Users/Huangqh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const BASE = `http://127.0.0.1:5173/#/?bootstrapSecret=${process.env.ZHIXU_BOOTSTRAP_SECRET}`;
const OUT = "D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications";

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });

  await page.goto(BASE);
  await page.waitForTimeout(1200);
  await page.getByTestId("start-dock-toggle").click();
  await page.getByTestId("start-dock").waitFor();
  await page.waitForTimeout(400);
  await page.getByTestId("dock-card-browser").click();
  await page.locator("#browser-address").waitFor();
  await page.waitForTimeout(600);

  const state = await page.evaluate(() => {
    const dock = document.querySelector('[data-testid="start-dock"]');
    const gradients = [];
    if (dock) {
      for (const node of [dock, ...dock.querySelectorAll("*")]) {
        const style = getComputedStyle(node);
        if (style.backgroundImage && style.backgroundImage !== "none") {
          gradients.push({
            className: typeof node.className === "string" ? node.className.slice(0, 90) : "",
            image: style.backgroundImage,
          });
        }
      }
    }
    const iframe = document.querySelector('[data-testid="browser-iframe"]');
    return {
      dockRect: dock ? dock.getBoundingClientRect().toJSON() : null,
      address: document.querySelector("#browser-address")?.value ?? null,
      emptyHint: document.body.innerText.includes("Enter an address to start browsing"),
      iframe: iframe ? { src: iframe.getAttribute("src") } : null,
      gradients,
    };
  });
  await page.screenshot({ path: `${OUT}/2026-10-21-browser-tool-empty.png` });

  await page.locator("#browser-address").fill("https://example.com");
  await page.locator("#browser-address").press("Enter");
  await page.waitForTimeout(2500);
  const after = await page.evaluate(() => ({
    iframe: document.querySelector('[data-testid="browser-iframe"]')?.getAttribute("src") ?? null,
    blocked: document.body.innerText.includes("This site refuses to be embedded"),
    alert: document.querySelector('[role="alert"]')?.textContent ?? null,
  }));
  await page.screenshot({ path: `${OUT}/2026-10-21-browser-tool-example.png` });

  console.log(JSON.stringify({ state, after, errors }, null, 2));
  await browser.close();
})();
