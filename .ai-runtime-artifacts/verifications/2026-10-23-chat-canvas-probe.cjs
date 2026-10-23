const { chromium } = require("C:/Users/Huangqh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const BASE = `http://127.0.0.1:5173/#/?bootstrapSecret=${process.env.ZHIXU_BOOTSTRAP_SECRET}`;

const PICK = [
  ".app-shell-canvas",
  ".app-shell-card",
  ".workbench-canvas",
  ".workbench-pane",
  ".agent-thread-shell",
  ".thread-viewport",
  ".thread-composer-surface",
  ".thread-welcome",
  "[data-testid='host-sidebar-flow']",
];

async function measure(page, label) {
  const report = await page.evaluate((picks) => {
    const pick = (selector) => {
      const node = document.querySelector(selector);
      if (!node) return { selector, missing: true };
      const style = getComputedStyle(node);
      return {
        selector,
        color: style.backgroundColor,
        image: style.backgroundImage,
        vars: {
          background: getComputedStyle(document.documentElement).getPropertyValue("--background"),
          card: getComputedStyle(document.documentElement).getPropertyValue("--card"),
          sidebar: getComputedStyle(document.documentElement).getPropertyValue("--sidebar"),
        },
      };
    };
    return {
      preset: localStorage.getItem("zhixu-webui.appearance"),
      items: picks.map(pick),
    };
  }, PICK);
  console.log(`\n== ${label} ==`);
  console.log("stored:", report.preset);
  for (const item of report.items) console.log(JSON.stringify(item));
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 });
  await context.addInitScript(() => localStorage.setItem("zhixu-webui.theme", "light"));
  const page = await context.newPage();
  await page.goto(BASE);
  await page.waitForTimeout(1500);

  await measure(page, "as-shipped (no stored appearance)");

  await page.evaluate(() => {
    localStorage.setItem("zhixu-webui.appearance", JSON.stringify({
      schemaVersion: 1, mode: "light", presetId: "graphite", custom: null,
      uiFont: { id: "system", family: "" }, codeFont: { id: "default", family: "" },
    }));
  });
  await page.reload();
  await page.waitForTimeout(1500);
  await measure(page, "graphite preset");

  await browser.close();
})();
