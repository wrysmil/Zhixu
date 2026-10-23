const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");

const bootstrapUrl = process.env.ZHIXU_BOOTSTRAP_URL;
if (!bootstrapUrl) throw new Error("ZHIXU_BOOTSTRAP_URL is required");
const outDir = path.resolve(".ai-runtime-artifacts/verifications/2026-10-21-creative-converge");
fs.mkdirSync(outDir, { recursive: true });

const viewport = {
  width: Number(process.env.PROBE_WIDTH || 1440),
  height: Number(process.env.PROBE_HEIGHT || 960),
};
const tag = `${viewport.width}`;

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport, locale: "zh-CN" });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });

    await page.goto(bootstrapUrl, { waitUntil: "networkidle" });
    await page.evaluate(() => {
      window.location.hash = "#/creative";
    });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: path.join(outDir, `creative-list-${tag}.png`) });

    const listMetrics = await page.evaluate(() => {
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
      const openButtons = [...document.querySelectorAll('[data-testid^="open-article-"]')];
      return {
        viewport: { w: innerWidth, h: innerHeight },
        scrollWidth: document.documentElement.scrollWidth,
        h1: document.querySelector("h1")
          ? {
              text: document.querySelector("h1").textContent?.trim(),
              fontSize: style(document.querySelector("h1")).fontSize,
              rect: rect(document.querySelector("h1")),
            }
          : null,
        articleCount: openButtons.length,
        firstCardTitle: openButtons[0]?.querySelector("h3")
          ? {
              text: openButtons[0].querySelector("h3").textContent?.trim(),
              fontSize: style(openButtons[0].querySelector("h3")).fontSize,
            }
          : null,
        listState:
          document.querySelector("[data-testid=article-list-empty]") ? "empty"
            : document.querySelector("[data-testid=article-list-error]") ? "error"
              : document.querySelector("[data-testid=article-list-loading]") ? "loading"
                : "ready",
      };
    });
    console.log(JSON.stringify({ stage: "list", listMetrics, errors }, null, 2));

    const firstArticle = page.locator('[data-testid^="open-article-"]').first();
    if (await firstArticle.count()) {
      await firstArticle.click();
      await page.waitForSelector('[data-testid="article-editor"]', { timeout: 15000 });
      await page.waitForTimeout(1500);
      await page.screenshot({ path: path.join(outDir, `creative-editor-${tag}.png`) });

      const editorMetrics = await page.evaluate(() => {
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
        const body = document.querySelector('[data-testid="article-body-input"]');
        const partnerToggle = document.querySelector('[data-testid="partner-toggle"]');
        const partnerColumn = document.querySelector('[data-testid="creative-partner-column"]');
        const chatPanel = document.querySelector('[data-testid="writing-chat-panel"]');
        const chatInput = document.querySelector('[data-testid="writing-chat-input"]');
        const markdown = chatPanel?.querySelector(".markdown-content");
        const chatEmpty = document.querySelector('[data-testid="writing-chat-empty"]');
        const headerButtons = [...document.querySelectorAll("#article-editor, [data-testid=article-editor] button")]
          .map((el) => ({ text: el.textContent?.trim().slice(0, 20), rect: rect(el) }))
          .filter((entry) => entry.rect && entry.rect.y < 70);
        return {
          viewport: { w: innerWidth, h: innerHeight },
          scrollWidth: document.documentElement.scrollWidth,
          bodyFontSize: body ? style(body).fontSize : null,
          bodyLineHeight: body ? style(body).lineHeight : null,
          bodyRect: rect(body),
          partnerToggle: partnerToggle
            ? {
                rect: rect(partnerToggle),
                visible: Boolean(partnerToggle.offsetParent),
                fontSize: style(partnerToggle).fontSize,
                text: partnerToggle.textContent?.trim(),
              }
            : null,
          partnerColumn: partnerColumn
            ? {
                rect: rect(partnerColumn),
                display: style(partnerColumn).display,
                width: style(partnerColumn).width,
              }
            : null,
          chatPanel: chatPanel
            ? {
                rect: rect(chatPanel),
                display: style(chatPanel).display,
                text: chatPanel.textContent?.slice(0, 120),
              }
            : null,
          chatEmpty: chatEmpty ? { rect: rect(chatEmpty), text: chatEmpty.textContent?.slice(0, 100) } : null,
          chatInput: chatInput ? { rect: rect(chatInput), fontSize: style(chatInput).fontSize } : null,
          chatMarkdown: markdown
            ? { fontSize: style(markdown).fontSize, lineHeight: style(markdown).lineHeight }
            : null,
          headerButtons,
        };
      });
      console.log(JSON.stringify({ stage: "editor", editorMetrics, errors }, null, 2));

      const toggle = page.locator('[data-testid="partner-toggle"]');
      if (await toggle.count()) {
        const visible = await toggle.isVisible();
        if (!visible) {
          console.log(JSON.stringify({ stage: "editor", note: "partner-toggle not visible", errors }, null, 2));
        }
      }
    }
    console.log(JSON.stringify({ finalErrors: errors }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
