// 修复验收：真实浏览器量「工作台 rail 是否还压住输入区」，并检查 <768px 不再白让位。
const port = process.argv[2] ?? "9333";
const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const page = list.find((t) => t.type === "page" && t.url.includes("5173"));
if (!page) { console.error("no page target"); process.exit(1); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0; const pending = new Map();
ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
await new Promise((r) => ws.addEventListener("open", r));
const send = (method, params = {}) => new Promise((res) => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
await send("Runtime.enable");
await send("Page.enable");
const ev = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true, userGesture: true });
  if (r.result?.exceptionDetails) return "EXC " + JSON.stringify(r.result.exceptionDetails).slice(0, 200);
  return r.result?.result?.value;
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const fs = await import("node:fs");
const OUT = "/Users/mima0000/Documents/学习-001/do-project/zhixu/.ai-runtime-artifacts/verifications/2026-10-17-chat-page-cover";
const shot = async (n) => { const s = await send("Page.captureScreenshot", { format: "png" }); if (s.result?.data) fs.writeFileSync(`${OUT}/${n}`, Buffer.from(s.result.data, "base64")); return n; };

// 等应用启动
for (let i = 0; i < 40; i++) {
  const ready = await ev(`Boolean(document.querySelector('[data-testid="host-sidebar-flow"]'))`);
  if (ready === true) break;
  await wait(500);
}

// 打开一个会话（rail 只在 session 存在时渲染）
const opened = await ev(String.raw`
(() => {
  if (document.querySelector('.thread-composer-surface') && Array.from(document.querySelectorAll("div")).some((d) => (d.getAttribute("class")||"").includes("w-[19rem]"))) {
    return "already in session";
  }
  const rows = Array.from(document.querySelectorAll("li"));
  const row = rows.find((li) => (li.textContent || "").trim().startsWith("今天有点累聊聊"))
    || rows.find((li) => (li.textContent || "").trim().startsWith("说“收到”两个字"));
  const btn = row?.querySelector("button");
  if (!btn) return "no session row";
  btn.click();
  return "clicked " + (row.textContent || "").trim().slice(0, 16);
})()
`);
console.log("open session:", opened);
await wait(3500);

const MEASURE = String.raw`
(() => {
  const r = (el) => { const b = el.getBoundingClientRect(); return { left: Math.round(b.left), right: Math.round(b.right), top: Math.round(b.top), bottom: Math.round(b.bottom), width: Math.round(b.width), height: Math.round(b.height) }; };
  const host = document.querySelector('[data-testid="workbench-composer-host"]');
  const rail = Array.from(document.querySelectorAll("div, aside")).find((el) => (el.getAttribute("class")||"").includes("w-[19rem]"));
  const surface = document.querySelector(".thread-composer-surface");
  const out = { viewport: [innerWidth, innerHeight] };
  out.host = host ? { rect: r(host), cls: host.getAttribute("class"), inlineStyle: host.getAttribute("style"), computedMarginRight: getComputedStyle(host).marginRight, scrollWidth: host.scrollWidth, clientWidth: host.clientWidth } : null;
  out.rail = rail ? { rect: r(rail), cls: (rail.getAttribute("class")||"").slice(0, 80) } : null;
  out.surface = surface ? { rect: r(surface) } : null;
  if (out.host && out.rail) {
    const h = out.host.rect, rr = out.rail.rect;
    const hOverlap = Math.max(0, Math.min(h.right, rr.right) - Math.max(h.left, rr.left));
    const vOverlap = Math.max(0, Math.min(h.bottom, rr.bottom) - Math.max(h.top, rr.top));
    out.overlap = { horizontal: hOverlap, vertical: vOverlap, overlapped: hOverlap > 0 && vOverlap > 0 };
    out.gapHostToRail = rr.left - h.right;
  }
  return out;
})()
`;

console.log("\n=== 桌面宽度（1320）===");
const wide = await ev(MEASURE);
console.log(JSON.stringify(wide, null, 1));
console.log("screenshot:", await shot("verify-fix-wide.png"));

// 窄屏：rail 隐藏，输入区不该留 19rem 空档
await send("Emulation.setDeviceMetricsOverride", { width: 600, height: 700, deviceScaleFactor: 1, mobile: false });
await wait(1200);
console.log("\n=== 窄屏（600，rail 应隐藏）===");
const narrow = await ev(MEASURE);
console.log(JSON.stringify(narrow, null, 1));
console.log("screenshot:", await shot("verify-fix-narrow.png"));
await send("Emulation.clearDeviceMetricsOverride");
ws.close();
