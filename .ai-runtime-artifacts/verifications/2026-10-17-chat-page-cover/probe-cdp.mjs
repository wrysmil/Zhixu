// 会话页「横幅/输入框内容被横向裁掉」取证脚本：CDP 直连无头 Chrome 量真实 DOM。
// 用法：node probe-cdp.mjs <debugPort>
const port = process.argv[2] ?? "9333";

const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const page = list.find((t) => t.type === "page");
if (!page) {
  console.error("no page target", JSON.stringify(list, null, 2));
  process.exit(1);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
ws.addEventListener("message", (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg);
    pending.delete(msg.id);
  }
});
await new Promise((res) => ws.addEventListener("open", res));

const send = (method, params = {}) =>
  new Promise((res) => {
    const mid = ++id;
    pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

await send("Runtime.enable");

const expression = String.raw`
(() => {
  const out = {};
  const rect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
  };
  const desc = (el) => {
    if (!el || !el.tagName) return String(el);
    const cls = (el.getAttribute("class") || "").slice(0, 140);
    const testid = el.getAttribute("data-testid") || "";
    return el.tagName.toLowerCase() + (testid ? "[data-testid=" + testid + "]" : "") + (cls ? "." + cls.replace(/\s+/g, ".") : "");
  };
  const path = (el, depth = 6) => {
    const chain = [];
    let cur = el;
    for (let i = 0; i < depth && cur && cur.tagName; i++) {
      const cs = getComputedStyle(cur);
      chain.push({
        tag: cur.tagName.toLowerCase(),
        testid: cur.getAttribute("data-testid") || undefined,
        cls: (cur.getAttribute("class") || "").slice(0, 200),
        rect: rect(cur),
        overflowX: cs.overflowX,
        overflowY: cs.overflowY,
        scrollLeft: cur.scrollLeft,
        scrollWidth: cur.scrollWidth,
        clientWidth: cur.clientWidth,
        marginLeft: cs.marginLeft,
        marginRight: cs.marginRight,
        paddingLeft: cs.paddingLeft,
        direction: cs.direction,
        position: cs.position,
        zIndex: cs.zIndex,
        transform: cs.transform,
        textAlign: cs.textAlign,
        whiteSpace: cs.whiteSpace,
      });
      cur = cur.parentElement;
    }
    return chain;
  };

  out.viewport = {
    innerWidth: innerWidth,
    innerHeight: innerHeight,
    dpr: devicePixelRatio,
    docDir: document.documentElement.getAttribute("dir") || getComputedStyle(document.documentElement).direction,
    bodyDir: getComputedStyle(document.body).direction,
    htmlScrollLeft: document.documentElement.scrollLeft,
    htmlScrollWidth: document.documentElement.scrollWidth,
    htmlClientWidth: document.documentElement.clientWidth,
    bodyScrollLeft: document.body.scrollLeft,
    bodyScrollWidth: document.body.scrollWidth,
    bodyClientWidth: document.body.clientWidth,
  };

  // 1) 所有水平溢出的元素（scrollWidth > clientWidth）
  const overflowing = [];
  for (const el of document.querySelectorAll("*")) {
    if (el.scrollWidth - el.clientWidth > 1 && el.clientWidth > 0) {
      const cs = getComputedStyle(el);
      overflowing.push({
        el: desc(el),
        rect: rect(el),
        scrollLeft: el.scrollLeft,
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
        overflowX: cs.overflowX,
        direction: cs.direction,
        text: (el.textContent || "").trim().slice(0, 60),
      });
    }
  }
  out.overflowing = overflowing.slice(0, 25);

  // 2) 关键节点
  const q = (sel) => document.querySelector(sel);
  out.keyNodes = {
    composerHost: (() => { const el = q('[data-testid="workbench-composer-host"]'); return el ? { rect: rect(el), cls: el.getAttribute("class"), style: el.getAttribute("style") } : null; })(),
    composerSurface: (() => { const el = q(".thread-composer-surface"); return el ? { rect: rect(el), cls: el.getAttribute("class") } : null; })(),
    workbenchRail: (() => { const el = q('[class*="w-[19rem]"]'); return el ? { rect: rect(el), cls: el.getAttribute("class") } : null; })(),
    sidebarAside: (() => { const el = q('[data-testid="host-sidebar-flow"]'); return el ? { rect: rect(el), cls: el.getAttribute("class"), style: el.getAttribute("style") } : null; })(),
    main: (() => { const el = q("main"); return el ? { rect: rect(el), cls: el.getAttribute("class") } : null; })(),
    alert: (() => { const el = q('[role="alert"]'); return el ? { rect: rect(el), cls: el.getAttribute("class"), text: (el.textContent || "").slice(0, 120) } : null; })(),
  };

  // 3) 命中测试：把截图里出问题的点拿回来（1320x710 视口坐标）
  const points = { banner: [640, 533], bannerLeft: [278, 533], composer: [600, 672], composerLeft: [280, 577] };
  out.hitTest = {};
  for (const [name, [x, y]] of Object.entries(points)) {
    const el = document.elementFromPoint(x, y);
    out.hitTest[name] = el
      ? { point: [x, y], top: desc(el), rect: rect(el), text: (el.textContent || "").trim().slice(0, 60), chain: path(el, 5) }
      : { point: [x, y], top: null };
  }
  return out;
})()
`;

const res = await send("Runtime.evaluate", {
  expression,
  returnByValue: true,
  awaitPromise: true,
});
const val = res.result?.result?.value;
if (!val) {
  console.error("evaluate failed:", JSON.stringify(res, null, 2).slice(0, 2000));
  process.exit(2);
}
console.log(JSON.stringify(val, null, 2));
ws.close();
