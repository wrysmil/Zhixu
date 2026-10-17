// 打开一个会话，量工作台常驻状态下的输入区/横幅链路，定位水平溢出与 scrollLeft 污染。
const port = process.argv[2] ?? "9333";
const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const page = list.find((t) => t.type === "page" && t.url.includes("5173"));
if (!page) { console.error("no page"); process.exit(1); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0; const pending = new Map();
ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
await new Promise((r) => ws.addEventListener("open", r));
const send = (method, params = {}) => new Promise((res) => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
await send("Runtime.enable");
const ev = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 500));
  return r.result?.result?.value;
};

const MEASURE = String.raw`
(() => {
  const box = (el) => { const r = el.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  const desc = (el) => el.tagName.toLowerCase() + (el.getAttribute("data-testid") ? "[data-testid=" + el.getAttribute("data-testid") + "]" : "") + " ." + (el.getAttribute("class") || "").slice(0, 90);
  const out = { viewport: [innerWidth, innerHeight], scrollPolluted: [], hScrollable: [], notes: [] };

  // 任何 scrollLeft > 0 的元素 = 被污染（刷新会清零）
  for (const el of document.querySelectorAll("*")) {
    if (el.scrollLeft > 0) out.scrollPolluted.push({ el: desc(el), scrollLeft: el.scrollLeft, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, box: box(el) });
  }
  // 可水平滚动（overflow-x 允许滚动）且内容更宽
  for (const el of document.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    const scrollable = cs.overflowX === "auto" || cs.overflowX === "scroll" || cs.overflowX === "hidden";
    if (scrollable && el.clientWidth > 0 && el.scrollWidth - el.clientWidth > 1) {
      out.hScrollable.push({ el: desc(el), overflowX: cs.overflowX, scrollLeft: el.scrollLeft, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, overflow: el.scrollWidth - el.clientWidth, box: box(el), text: (el.textContent || "").trim().slice(0, 40) });
    }
  }
  // 输入区 host / surface / 工作台 rail
  const host = document.querySelector('[data-testid="workbench-composer-host"]');
  out.composerHost = host ? { box: box(host), cls: host.getAttribute("class"), style: host.getAttribute("style"), scrollWidth: host.scrollWidth, clientWidth: host.clientWidth, parent: host.parentElement ? { tag: host.parentElement.tagName, cls: (host.parentElement.getAttribute("class")||"").slice(0,120), box: box(host.parentElement) } : null } : null;
  const surface = document.querySelector(".thread-composer-surface");
  out.composerSurface = surface ? { box: box(surface), cls: surface.getAttribute("class"), scrollWidth: surface.scrollWidth, clientWidth: surface.clientWidth, containerType: getComputedStyle(surface).containerType } : null;
  const rail = document.querySelector(".session-workbench, [class*='w-[19rem]']");
  out.rail = rail ? { box: box(rail), cls: (rail.getAttribute("class")||"").slice(0,120) } : null;
  out.alert = document.querySelector('[role="alert"]') ? { text: document.querySelector('[role="alert"]').textContent.slice(0,120), box: box(document.querySelector('[role="alert"]')) } : null;

  // 输入区祖先链（从 surface 向上到 main）
  if (surface) {
    const chain = []; let cur = surface.parentElement;
    while (cur && cur.tagName !== "BODY") {
      const cs = getComputedStyle(cur);
      chain.push({ el: desc(cur), box: box(cur), overflowX: cs.overflowX, scrollLeft: cur.scrollLeft, scrollWidth: cur.scrollWidth, clientWidth: cur.clientWidth, marginLeft: cs.marginLeft, marginRight: cs.marginRight, paddingLeft: cs.paddingLeft, paddingRight: cs.paddingRight });
      cur = cur.parentElement;
    }
    out.surfaceAncestors = chain.slice(0, 10);
  }
  return out;
})()
`;

console.log("=== 打开会话前 ===");
console.log(JSON.stringify(await ev(MEASURE), null, 1).slice(0, 1200));

// 点击侧栏里第一个会话（优先「今天有点累聊聊」，否则任一「新建话题」）
const clicked = await ev(String.raw`
(() => {
  const wanted = ["今天有点累聊聊", "新建话题"];
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], a, li'));
  for (const label of wanted) {
    const hit = nodes.find((n) => (n.textContent || "").trim() === label || ((n.textContent || "").includes(label) && (n.getAttribute("data-testid") || "").match(/session|chat|topic/i)));
    if (hit) { hit.click(); return label + " -> " + hit.tagName + " " + (hit.getAttribute("data-testid") || ""); }
  }
  const any = nodes.find((n) => (n.textContent || "").includes("新建话题"));
  if (any) { any.click(); return "fallback 新建话题"; }
  return "none";
})()
`);
console.log("\n=== 点击结果 ===", clicked);
await new Promise((r) => setTimeout(r, 4000));
console.log("\n=== 打开会话后 ===");
console.log(JSON.stringify(await ev(MEASURE), null, 1).slice(0, 6000));

// 用同一尺寸截图，便于与用户截图对照
const shot = await send("Page.captureScreenshot", { format: "png" });
if (shot.result?.data) {
  const fs = await import("node:fs");
  fs.writeFileSync("/Users/mima0000/Documents/学习-001/do-project/zhixu/.ai-runtime-artifacts/verifications/2026-10-17-chat-page-cover/repro-session.png", Buffer.from(shot.result.data, "base64"));
  console.log("\n截图已保存 repro-session.png");
}
ws.close();
