const { chromium } = require("playwright");

const bootstrapUrl = process.env.ZHIXU_BOOTSTRAP_URL;
if (!bootstrapUrl) throw new Error("ZHIXU_BOOTSTRAP_URL is required");
const sessionKey = process.env.ZHIXU_SESSION_KEY;
if (!sessionKey) throw new Error("ZHIXU_SESSION_KEY is required");

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1160, height: 824 }, locale: "zh-CN" });
  await page.goto(bootstrapUrl, { waitUntil: "networkidle" });
  await page.evaluate((key) => {
    window.location.hash = `#/chat/${encodeURIComponent(key)}`;
  }, sessionKey);
  await page.waitForTimeout(2500);

  const message = await page.evaluate(() => {
    const chip = document.querySelector('[data-testid^="message-agent-mention-"]')
      || document.querySelector('[data-testid^="message-session-mention-"]')
      || document.querySelector('[data-testid^="message-cli-mention-"]');
    const bubbles = [...document.querySelectorAll("[data-user-message-text]")];
    if (!chip) {
      return {
        found: false,
        hash: window.location.hash,
        bubbleCount: bubbles.length,
        bubbleTexts: bubbles.map((b) => b.textContent?.slice(0, 60)),
        bubbleHtml: bubbles[bubbles.length - 1]?.outerHTML?.slice(0, 900) ?? null,
      };
    }
    const spans = [...chip.querySelectorAll("span")];
    const dump = (el) => {
      const s = getComputedStyle(el);
      return {
        text: el.textContent,
        color: s.color,
        fontFamily: s.fontFamily,
        fontSize: s.fontSize,
        fontWeight: s.fontWeight,
        className: el.className,
      };
    };
    return {
      found: true,
      testId: chip.getAttribute("data-testid"),
      chipText: chip.textContent,
      chipClass: chip.className,
      chipColor: getComputedStyle(chip).color,
      spans: spans.map(dump),
      bubbleHtml: chip.closest("p,[data-user-message-text]")?.outerHTML?.slice(0, 1200) ?? null,
    };
  });

  const composer = await page.evaluate(() => {
    const input = document.querySelector("textarea");
    return input ? { placeholder: input.getAttribute("placeholder"), testId: input.getAttribute("data-testid") } : null;
  });

  console.log(JSON.stringify({ message, composer }, null, 2));
  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
