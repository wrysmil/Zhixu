const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const bootstrapUrl = process.env.ZHIXU_BOOTSTRAP_URL;
if (!bootstrapUrl) throw new Error("ZHIXU_BOOTSTRAP_URL is required");
const outDir = path.resolve(".ai-runtime-artifacts/verifications/2026-10-21-welcome-converge");
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1160, height: 824 }, locale: "zh-CN" });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });

    await page.goto(bootstrapUrl, { waitUntil: "networkidle" });
    await page
      .locator(".thread-welcome-recent-row", { hasText: "Leader 装配拆分" })
      .first()
      .click();
    await page.locator(".markdown-content").first().waitFor({ timeout: 15000 });
    await page
      .waitForFunction(
        () =>
          [...document.querySelectorAll(".markdown-content")].some(
            (node) => node.querySelector("h1,h2,h3,h4,table"),
          ),
        { timeout: 15000 },
      )
      .catch(() => {});
    await page.evaluate(() => document.fonts?.ready);
    await page.waitForTimeout(800);

    const styles = await page.evaluate(() => {
      const nodes = [...document.querySelectorAll(".markdown-content")];
      const markdown = nodes.find((node) => node.querySelector("h1,h2,h3,h4,table")) ?? nodes[0];
      const style = (element) => (element ? getComputedStyle(element) : null);
      const paragraph = markdown.querySelector("p");
      const heading = markdown.querySelector("h1,h2,h3,h4");
      const listItem = markdown.querySelector("li");
      const cell = markdown.querySelector("td");
      return {
        blocks: nodes.length,
        markdownFontSize: style(markdown).fontSize,
        markdownLineHeight: style(markdown).lineHeight,
        paragraphFontSize: paragraph ? style(paragraph).fontSize : null,
        paragraphLineHeight: paragraph ? style(paragraph).lineHeight : null,
        heading: heading
          ? { tag: heading.tagName, fontSize: style(heading).fontSize, lineHeight: style(heading).lineHeight }
          : null,
        listItemFontSize: listItem ? style(listItem).fontSize : null,
        tableCellFontSize: cell ? style(cell).fontSize : null,
      };
    });

    assert.ok(styles.blocks > 0, "chat has markdown content");
    assert.equal(styles.markdownFontSize, "14px", "markdown body is 14px");
    assert.equal(styles.paragraphFontSize, "14px", "paragraphs inherit 14px");
    assert.ok(parseFloat(styles.markdownLineHeight) >= 22, `line-height keeps body rhythm (${styles.markdownLineHeight})`);
    if (styles.listItemFontSize) assert.equal(styles.listItemFontSize, "14px", "list items are 14px");
    if (styles.tableCellFontSize) {
      assert.ok(
        ["13px", "14px"].includes(styles.tableCellFontSize),
        `table cells stay compact (${styles.tableCellFontSize})`,
      );
    }

    await page.screenshot({ path: path.join(outDir, "chat-1160.png") });
    console.log(JSON.stringify({ styles, consoleErrors: errors }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
