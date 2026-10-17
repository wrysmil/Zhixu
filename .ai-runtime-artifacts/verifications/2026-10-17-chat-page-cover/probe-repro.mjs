// 复现并定位：打开会话 → 量输入区（portal 到 workbench footer）链路 → 触发交互后看谁被横向滚走。
const port = process.argv[2] ?? "9333";
const mode = process.argv[3] ?? "measure";
const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const page = list.find((t) => t.type === "page" && t.url.includes("5173"));
if (!page) { console.error("no page"); process.exit(1); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0; const pending = new Map();
ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
await new Promise((r) => ws.addEventListener("open", r));
const send = (method, params = {}) => new Promise((res) => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
await send("Runtime.enable");
const ev = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true, userGesture: true });
  if (r.result?.exceptionDetails) return "EXC: " + JSON.stringify(r.result.exceptionDetails).slice(0, 300);
  return r.result?.result?.value;
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const shot = async (name) => {
  const s = await send("Page.captureScreenshot", { format: "png" });
  if (s.result?.data) {
    const fs = await import("node:fs");
    fs.writeFileSync(`/Users/mima0000/Documents/学习-001/do-project/zhixu/.ai-runtime-artifacts/verifications/2026-10-17-chat-page-cover/${name}`, Buffer.from(s.result.data, "base64"));
    return "saved " + name;
  }
  return "shot failed";
};

const MEASURE = String.raw`
(() => {
  const b = (el) => { const r = el.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  const desc = (el) => el.tagName.toLowerCase() + (el.getAttribute("data-testid") ? "[" + el.getAttribute("data-testid") + "]" : "") + "." + (el.getAttribute("class") || "").split(" ").slice(0, 3).join(".");
  const out = {};
  const host = document.querySelector('[data-testid="workbench-composer-host"]');
  out.host = host ? { box: b(host), cls: host.getAttribute("class"), inlineStyle: host.getAttribute("style"), computedMarginRight: getComputedStyle(host).marginRight, clientWidth: host.clientWidth, scrollWidth: host.scrollWidth, parentFooter: host.parentElement ? { box: b(host.parentElement), cls: host.parentElement.getAttribute("class") } : null } : null;
  const surface = document.querySelector(".thread-composer-surface");
  out.surface = surface ? { box: b(surface), cls: surface.getAttribute("class"), clientWidth: surface.clientWidth, scrollWidth: surface.scrollWidth, isHero: surface.getAttribute("class").includes("max-w-heroComposer") } : null;
  const footer = document.querySelector(".thread-composer-footer");
  out.footer = footer ? { box: b(footer), clientWidth: footer.clientWidth, scrollWidth: footer.scrollWidth, overflowX: getComputedStyle(footer).overflowX, scrollLeft: footer.scrollLeft } : null;
  const ta = document.querySelector(".thread-composer-surface textarea");
  out.textarea = ta ? { box: b(ta), clientWidth: ta.clientWidth, scrollWidth: ta.scrollWidth, scrollLeft: ta.scrollLeft } : null;
  // 被污染（scrollLeft>0）的元素
  out.scrollPolluted = [];
  for (const el of document.querySelectorAll("*")) {
    if (el.scrollLeft > 0) out.scrollPolluted.push({ el: desc(el), scrollLeft: el.scrollLeft, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, box: b(el), text: (el.textContent||"").trim().slice(0,30) });
  }
  // 可滚动且内容更宽的元素（潜在被滚对象）
  out.hOverflow = [];
  for (const el of document.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    if (el.clientWidth > 0 && el.scrollWidth - el.clientWidth > 1 && (cs.overflowX === "auto" || cs.overflowX === "scroll" || cs.overflowX === "hidden")) {
      out.hOverflow.push({ el: desc(el), overflowX: cs.overflowX, overflow: el.scrollWidth - el.clientWidth, scrollLeft: el.scrollLeft, box: b(el), text: (el.textContent||"").trim().slice(0,30) });
    }
  }
  const rail = Array.from(document.querySelectorAll("div, aside")).find((el) => (el.getAttribute("class")||"").includes("w-[19rem]"));
  out.rail = rail ? { box: b(rail), cls: (rail.getAttribute("class")||"").slice(0,110) } : null;
  out.alert = document.querySelector('[role="alert"]') ? { text: document.querySelector('[role="alert"]').textContent.slice(0,140), box: b(document.querySelector('[role="alert"]')) } : null;
  return out;
})()
`;

if (mode === "measure") {
  // 点击侧栏会话行里的按钮（真正的 onClick 在 li > button 上）
  const clicked = await ev(String.raw`
  (() => {
    const rows = Array.from(document.querySelectorAll("li"));
    const row = rows.find((li) => (li.textContent || "").trim().startsWith("今天有点累聊聊"))
      || rows.find((li) => (li.textContent || "").trim().startsWith("说“收到”两个字"))
      || rows.find((li) => (li.textContent || "").trim() === "New topic" || (li.textContent || "").trim() === "新建话题");
    if (!row) return "no row";
    const btn = row.querySelector("button");
    if (!btn) return "no button in row";
    btn.click();
    return "clicked: " + (row.textContent || "").trim().slice(0, 20);
  })()
  `);
  console.log("click:", clicked);
  await wait(4000);
  console.log("MEASURE after open:\n" + JSON.stringify(await ev(MEASURE), null, 1));
  console.log(await shot("repro-open-session.png"));
} else if (mode === "type") {
  const focused = await ev(String.raw`
  (() => { const ta = document.querySelector(".thread-composer-surface textarea"); if (!ta) return "no textarea"; ta.focus(); return "focused, clientWidth=" + ta.clientWidth + " scrollWidth=" + ta.scrollWidth; })()
  `);
  console.log("focus:", focused);
  await send("Input.insertText", { text: "test message from probe" });
  await wait(800);
  console.log("MEASURE after typing:\n" + JSON.stringify(await ev(MEASURE), null, 1));
  console.log(await shot("repro-after-type.png"));
} else if (mode === "send") {
  await send("Input.dispatchKeyEvent", { type: "rawKeyDown", windowsVirtualKeyCode: 13, key: "Enter", code: "Enter" });
  await send("Input.dispatchKeyEvent", { type: "keyUp", windowsVirtualKeyCode: 13, key: "Enter", code: "Enter" });
  await wait(9000);
  console.log("MEASURE after send:\n" + JSON.stringify(await ev(MEASURE), null, 1));
  console.log(await shot("repro-after-send.png"));
} else if (mode === "slash") {
  await send("Input.insertText", { text: "/" });
  await wait(1200);
  console.log("MEASURE after slash:\n" + JSON.stringify(await ev(MEASURE), null, 1));
  // 连按方向键触发 scrollIntoView
  for (let i = 0; i < 6; i++) {
    await send("Input.dispatchKeyEvent", { type: "rawKeyDown", windowsVirtualKeyCode: 40, key: "ArrowDown", code: "ArrowDown" });
    await send("Input.dispatchKeyEvent", { type: "keyUp", windowsVirtualKeyCode: 40, key: "ArrowDown", code: "ArrowDown" });
    await wait(150);
  }
  await wait(600);
  console.log("MEASURE after arrow keys:\n" + JSON.stringify(await ev(MEASURE), null, 1));
  console.log(await shot("repro-after-slash.png"));
}
ws.close();
