const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const bootstrapUrl = process.env.ZHIXU_BOOTSTRAP_URL;
if (!bootstrapUrl) throw new Error("ZHIXU_BOOTSTRAP_URL is required");
const sessionKey = process.env.ZHIXU_SESSION_KEY;
if (!sessionKey) throw new Error("ZHIXU_SESSION_KEY is required");
const outDir = path.resolve(".ai-runtime-artifacts/verifications/2026-10-21-mention-converge");
fs.mkdirSync(outDir, { recursive: true });

function parseRgba(value) {
  const match = /rgba?\(([^)]+)\)/.exec(value);
  if (!match) return null;
  const parts = match[1].split(",").map((part) => parseFloat(part.trim()));
  return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
}

function sameHue(a, b) {
  if (!a || !b) return false;
  return Math.abs(a.r - b.r) < 26 && Math.abs(a.g - b.g) < 26 && Math.abs(a.b - b.b) < 26;
}

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
    await page.evaluate((key) => {
      window.location.hash = `#/chat/${encodeURIComponent(key)}`;
    }, sessionKey);
    await page.waitForSelector('[data-testid="message-agent-mention-commander"]', { timeout: 15000 });
    await page.waitForTimeout(800);

    const message = await page.evaluate(() => {
      const chip = document.querySelector('[data-testid="message-agent-mention-commander"]');
      const at = chip.querySelector("[data-mention-at]");
      const name = [...chip.querySelectorAll("span")].find((el) => el.textContent === "commander");
      return {
        chipColor: getComputedStyle(chip).color,
        atColor: getComputedStyle(at).color,
        atOpacity: getComputedStyle(at).opacity,
        atText: at.textContent,
        nameColor: name ? getComputedStyle(name).color : null,
        chipText: chip.textContent,
        svgCount: chip.querySelectorAll("svg").length,
        glyphBadge: Boolean(chip.querySelector('[data-testid^="message-agent-mention-glyph-"]')),
      };
    });

    assert.equal(message.chipText, "@commander", "the chip keeps only the mention text");
    assert.equal(message.svgCount, 0, "the agent glyph is gone from the message chip");
    assert.equal(message.glyphBadge, false, "no glyph badge is rendered");
    assert.equal(message.atText, "@", "the @ prefix is still rendered");
    const atRgba = parseRgba(message.atColor);
    const nameRgba = parseRgba(message.nameColor);
    assert.ok(
      sameHue(atRgba, nameRgba),
      `the @ keeps the identity hue (at=${message.atColor}, name=${message.nameColor})`,
    );
    assert.notEqual(
      atRgba?.r === 98 && atRgba?.g === 108 && atRgba?.b === 127,
      true,
      "the @ is no longer the muted gray",
    );
    assert.equal(message.atOpacity, "0.6", "the @ is dimmed, not flat");
    assert.deepEqual(errors, [], "no console errors on the message side");

    const bubble = page.locator("[data-user-message-text]").last();
    await bubble.screenshot({ path: path.join(outDir, "mention-message-chip.png") });

    const input = page.locator("textarea:visible").last();
    await input.click();
    await input.type("@commander");
    await page.waitForSelector('[data-testid="composer-agent-mention-commander"]', { timeout: 10000 });
    await page.waitForTimeout(400);

    const composer = await page.evaluate(() => {
      const chip = document.querySelector('[data-testid="composer-agent-mention-commander"]');
      const at = chip.querySelector("[data-mention-at]");
      return {
        chipColor: getComputedStyle(chip).color,
        atColor: getComputedStyle(at).color,
        atOpacity: getComputedStyle(at).opacity,
        atText: at.textContent,
      };
    });

    assert.equal(composer.atText, "@", "composer keeps the @ prefix");
    const composerAt = parseRgba(composer.atColor);
    const composerChip = parseRgba(composer.chipColor);
    assert.ok(
      sameHue(composerAt, composerChip),
      `composer @ keeps the identity hue (at=${composer.atColor}, chip=${composer.chipColor})`,
    );
    assert.equal(composer.atOpacity, "0.6", "composer @ is dimmed, not flat");

    const composerChipLocator = page.locator("form:visible, [data-testid='composer']:visible").last();
    const composerShot = await composerChipLocator.count()
      ? composerChipLocator
      : page.locator("textarea:visible").last();
    const chipBox = await page.locator('[data-testid="composer-agent-mention-commander"]').boundingBox();
    if (chipBox) {
      await page.screenshot({
        path: path.join(outDir, "mention-composer-chip.png"),
        clip: {
          x: Math.max(0, chipBox.x - 12),
          y: Math.max(0, chipBox.y - 10),
          width: Math.min(360, chipBox.width + 90),
          height: chipBox.height + 20,
        },
      });
    }

    console.log(JSON.stringify({ message, composer, consoleErrors: errors }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
