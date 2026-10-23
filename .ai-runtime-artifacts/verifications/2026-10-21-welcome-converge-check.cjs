const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const bootstrapUrl = process.env.ZHIXU_BOOTSTRAP_URL;
if (!bootstrapUrl) throw new Error("ZHIXU_BOOTSTRAP_URL is required");
const origin = new URL(bootstrapUrl).origin;
const outDir = path.resolve(".ai-runtime-artifacts/verifications/2026-10-21-welcome-converge");
fs.mkdirSync(outDir, { recursive: true });

function collectStyles() {
  const tokenColor = (name) => {
    const probe = document.createElement("span");
    probe.style.color = `hsl(var(${name}))`;
    document.body.appendChild(probe);
    const color = getComputedStyle(probe).color;
    probe.remove();
    return color;
  };
  const style = (selector) => {
    const element = document.querySelector(selector);
    if (!element) throw new Error(`missing ${selector}`);
    return getComputedStyle(element);
  };
  const dot = style(".thread-welcome-eyebrow-dot");
  const illustration = style(".thread-welcome-illustration");
  const image = style(".thread-welcome-illustration > img");
  const chip = style(".thread-welcome-chip");
  const recent = style(".thread-welcome-recent-row");
  return {
    tokens: {
      textStrong: tokenColor("--text-strong"),
      muted: tokenColor("--muted-foreground"),
      primary: tokenColor("--primary"),
    },
    eyebrow: style(".thread-welcome-eyebrow").color,
    dot: { background: dot.backgroundColor, width: dot.width, height: dot.height },
    title: { color: style(".thread-welcome-title").color, size: style(".thread-welcome-title").fontSize },
    description: { size: style(".thread-welcome-description").fontSize },
    label: { size: style(".thread-welcome-starters-label").fontSize },
    chip: {
      size: chip.fontSize,
      borderColor: chip.borderColor,
      background: chip.backgroundColor,
    },
    sectionHeading: { size: style(".thread-welcome-section-heading h2").fontSize },
    recent: {
      borderBottom: recent.borderBottomWidth,
      titleSize: style(".thread-welcome-recent-copy strong").fontSize,
      detailSize: style(".thread-welcome-recent-copy > span").fontSize,
      iconBackground: style(".thread-welcome-recent-icon").backgroundColor,
    },
    foot: style(".thread-welcome-foot").display,
    illustration: {
      width: illustration.width,
      imageWidth: image.width,
      halo: style(".thread-welcome-halo").display,
    },
  };
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const errors = [];
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 960 },
      locale: "zh-CN",
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });

    await page.goto(bootstrapUrl, { waitUntil: "networkidle" });
    await page.locator(".thread-welcome-title").waitFor();
    await page.evaluate(() => document.fonts?.ready);
    await page.waitForTimeout(600);

    const welcome = await page.evaluate(collectStyles);
    assert.equal(welcome.title.color, welcome.tokens.textStrong, "H1 uses --text-strong");
    assert.equal(welcome.title.size, "27px", "H1 is 27px");
    assert.equal(welcome.eyebrow, welcome.tokens.muted, "eyebrow is neutral grey");
    assert.equal(welcome.dot.background, welcome.tokens.primary, "eyebrow dot is brand blue");
    assert.deepEqual([welcome.dot.width, welcome.dot.height], ["6px", "6px"], "dot stays 6px");
    assert.equal(welcome.description.size, "15px", "description is 15px");
    assert.equal(welcome.label.size, "13px", "section label is 13px");
    assert.equal(welcome.chip.size, "13px", "chip text is 13px");
    assert.equal(welcome.chip.borderColor, "rgba(0, 0, 0, 0)", "chip border is transparent");
    assert.equal(welcome.chip.background, "rgba(0, 0, 0, 0)", "chip background is transparent");
    assert.equal(welcome.sectionHeading.size, "13px", "recent heading is 13px");
    assert.equal(welcome.recent.borderBottom, "0px", "recent rows have no divider");
    assert.equal(welcome.recent.titleSize, "13px", "recent title is 13px");
    assert.equal(welcome.recent.detailSize, "11px", "recent detail is 11px");
    assert.equal(welcome.recent.iconBackground, "rgba(0, 0, 0, 0)", "recent icon has no plate");
    assert.equal(welcome.foot, "none", "welcome footnote is hidden");
    assert.equal(welcome.illustration.width, "96px", "mascot column is 96px");
    assert.equal(welcome.illustration.imageWidth, "96px", "mascot image is 96px");
    assert.equal(welcome.illustration.halo, "none", "mascot halo is removed");
    await page.screenshot({ path: path.join(outDir, "welcome-1440.png"), fullPage: true });

    await page.goto(`${origin}/#/memory`, { waitUntil: "networkidle" });
    await page.locator(".assistant-page-heading").waitFor();
    await page.waitForTimeout(600);
    const memory = await page.evaluate(() => {
      const tokenColor = (name) => {
        const probe = document.createElement("span");
        probe.style.color = `hsl(var(${name}))`;
        document.body.appendChild(probe);
        const color = getComputedStyle(probe).color;
        probe.remove();
        return color;
      };
      const style = (selector) => getComputedStyle(document.querySelector(selector));
      return {
        textStrong: tokenColor("--text-strong"),
        muted: tokenColor("--muted-foreground"),
        kicker: style(".assistant-page-kicker").color,
        heading: style(".assistant-page-heading").color,
        illustrationWidth: style(".assistant-page-illustration").width,
      };
    });
    assert.equal(memory.kicker, memory.muted, "shared kicker is neutral grey");
    assert.equal(memory.heading, memory.textStrong, "shared heading uses --text-strong");
    assert.equal(memory.illustrationWidth, "80px", "compact illustration is 80px");
    await page.screenshot({ path: path.join(outDir, "memory-1440.png"), fullPage: true });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${origin}/#/`, { waitUntil: "networkidle" });
    await page.locator(".thread-welcome-title").waitFor();
    await page.waitForTimeout(500);
    const mobile = await page.evaluate(() => ({
      titleSize: getComputedStyle(document.querySelector(".thread-welcome-title")).fontSize,
      descriptionSize: getComputedStyle(document.querySelector(".thread-welcome-description")).fontSize,
      illustration: getComputedStyle(document.querySelector(".thread-welcome-illustration")).display,
      chipSize: getComputedStyle(document.querySelector(".thread-welcome-chip")).fontSize,
    }));
    assert.equal(mobile.titleSize, "24px", "mobile title steps down to 24px");
    assert.equal(mobile.descriptionSize, "13px", "mobile description is 13px");
    assert.equal(mobile.chipSize, "13px", "mobile chip text stays 13px");
    assert.equal(mobile.illustration, "none", "mascot hides below 560px");
    await page.screenshot({ path: path.join(outDir, "welcome-390.png"), fullPage: true });

    console.log(JSON.stringify({ welcome, memory, mobile, consoleErrors: errors }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
