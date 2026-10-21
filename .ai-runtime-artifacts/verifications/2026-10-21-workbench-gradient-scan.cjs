const { chromium } = require("C:/Users/Huangqh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const BASE = `http://127.0.0.1:5173/#/?bootstrapSecret=${process.env.ZHIXU_BOOTSTRAP_SECRET}`;

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(BASE);
  await page.waitForTimeout(1200);
  await page.getByText("打招呼", { exact: true }).first().click();
  await page.waitForTimeout(2000);
  const offenders = await page.evaluate(() => {
    const root = document.querySelector(".workbench-canvas");
    if (!root) return [{ selector: ".workbench-canvas", missing: true }];
    const out = [];
    for (const node of [root, ...root.querySelectorAll("*")]) {
      const style = getComputedStyle(node);
      if (style.backgroundImage && style.backgroundImage !== "none") {
        out.push({
          tag: node.tagName.toLowerCase(),
          className: typeof node.className === "string" ? node.className.slice(0, 120) : "",
          testid: node.getAttribute("data-testid"),
          image: style.backgroundImage,
        });
      }
    }
    return out;
  });
  console.log(JSON.stringify(offenders, null, 2));
  await browser.close();
})();
