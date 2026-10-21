const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");

const bootstrapUrl = process.env.ZHIXU_BOOTSTRAP_URL;
if (!bootstrapUrl) throw new Error("ZHIXU_BOOTSTRAP_URL is required");
const width = Number(process.env.PROBE_WIDTH || 528);
const height = Number(process.env.PROBE_HEIGHT || 800);
const outDir = path.resolve(".ai-runtime-artifacts/verifications/2026-10-21-creative-converge");
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width, height }, locale: "zh-CN" });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });

    await page.goto(bootstrapUrl, { waitUntil: "networkidle" });
    await page.evaluate(() => {
      window.location.hash = "#/creative";
    });
    await page.waitForSelector('[data-testid^="open-article-"]', { timeout: 15000 });
    await page.locator('[data-testid^="open-article-"]').first().click();
    await page.waitForSelector('[data-testid="article-editor"]', { timeout: 15000 });
    await page.waitForTimeout(1200);

    const toggle = page.locator('[data-testid="partner-toggle"]');
    console.log(JSON.stringify({ toggleCount: await toggle.count(), toggleVisible: await toggle.isVisible() }));
    await toggle.click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(outDir, `creative-chat-open-${width}.png`) });

    const state = await page.evaluate(() => {
      const rect = (el) => {
        if (!el) return null;
        const box = el.getBoundingClientRect();
        return {
          x: Math.round(box.x),
          y: Math.round(box.y),
          w: Math.round(box.width),
          h: Math.round(box.height),
          right: Math.round(box.right),
          bottom: Math.round(box.bottom),
        };
      };
      const style = (el) => (el ? getComputedStyle(el) : null);
      const panel = document.querySelector('[data-testid="writing-chat-panel"]');
      const column = document.querySelector('[data-testid="creative-partner-column"]');
      const input = document.querySelector('[data-testid="writing-chat-input"]');
      const send = document.querySelector('[data-testid="writing-chat-send"]');
      const scroller = panel?.querySelector(".overflow-y-auto");
      return {
        viewport: { w: innerWidth, h: innerHeight },
        scrollWidth: document.documentElement.scrollWidth,
        panel: panel ? { rect: rect(panel), display: style(panel).display } : null,
        column: column
          ? {
              rect: rect(column),
              width: style(column).width,
              position: style(column).position,
            }
          : null,
        input: input
          ? {
              rect: rect(input),
              fontSize: style(input).fontSize,
              disabled: input.disabled,
              visible: Boolean(input.offsetParent),
            }
          : null,
        send: send ? { rect: rect(send), disabled: send.disabled } : null,
        scroller: scroller
          ? {
              rect: rect(scroller),
              scrollHeight: scroller.scrollHeight,
              clientHeight: scroller.clientHeight,
              overflowY: style(scroller).overflowY,
            }
          : null,
      };
    });

    if (state.input?.visible) {
      await page.locator('[data-testid="writing-chat-input"]').fill("测试输入");
      await page.waitForTimeout(300);
      state.afterTyping = await page.evaluate(() => {
        const input = document.querySelector('[data-testid="writing-chat-input"]');
        const send = document.querySelector('[data-testid="writing-chat-send"]');
        return {
          value: input?.value,
          sendDisabled: send?.disabled,
        };
      });
      await page.locator('[data-testid="writing-chat-input"]').fill("");
    }

    console.log(JSON.stringify({ state, errors }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
