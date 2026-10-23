// 复现「点了停止仍显示回答中」：
// 用真实 WS 协议跑一轮普通 turn，然后精确重放前端 stop() 发的 /stop 帧，
// 记录服务端下发的所有帧，并查询 /api/sessions 的 run_started_at 是否残留。
const BASE = "http://127.0.0.1:8765";
const cfg = JSON.parse(
  (await import("node:fs")).readFileSync(`${process.env.HOME}/.nanobot/config.json`, "utf8"),
);
const SECRET =
  (cfg.channels?.websocket?.tokenIssueSecret || "").trim() ||
  (cfg.channels?.websocket?.token || "").trim();

const boot = await (
  await fetch(`${BASE}/webui/bootstrap`, { headers: { Authorization: `Bearer ${SECRET}` } })
).json();
const token = boot.token;
const apiToken = boot.api_token;
const wsUrl = `ws://127.0.0.1:8765/?token=${encodeURIComponent(token)}`;

const t0 = Date.now();
const at = () => `+${String(Date.now() - t0).padStart(6)}ms`;
const short = (v, n = 160) => (typeof v === "string" ? v.slice(0, n) : v);

const ws = new WebSocket(wsUrl);
let chatId = null;
let turnId = null;
const frames = [];
ws.addEventListener("message", (e) => {
  const raw = typeof e.data === "string" ? e.data : "";
  let o;
  try {
    o = JSON.parse(raw);
  } catch {
    console.log(`${at()} <non-json frame>`);
    return;
  }
  frames.push({ at: at(), ev: o.event ?? o.type, o });
  const brief = { ...o };
  for (const k of ["text", "detail", "goal_state", "usage", "round_usages", "team_binding"]) {
    if (k in brief) brief[k] = short(JSON.stringify(brief[k]), 90);
  }
  delete brief.media_urls;
  console.log(`${at()} <= ${JSON.stringify(brief)}`);
  if (o.event === "attached" && o.chat_id && !chatId) chatId = o.chat_id;
});
ws.addEventListener("open", () => console.log(`${at()} ws open`));
ws.addEventListener("close", (ev) => console.log(`${at()} ws closed ${ev.code}`));

const send = (frame) => {
  console.log(`${at()} => ${JSON.stringify(frame)}`);
  ws.send(JSON.stringify(frame));
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const waitFor = async (pred, ms) => {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (frames.some(pred)) return true;
    await wait(100);
  }
  return false;
};
const serverRunStartedAt = async (id) => {
  const r = await fetch(`${BASE}/api/sessions`, {
    headers: { Authorization: `Bearer ${apiToken}` },
  });
  const d = await r.json();
  const row = (d.sessions ?? []).find((s) => s.key === `websocket:${id}`);
  return row ? { run_started_at: row.run_started_at ?? null, updated_at: row.updated_at } : null;
};

await new Promise((r) => (ws.readyState === 1 ? r() : ws.addEventListener("open", r)));
await wait(500);

console.log("\n=== 1) 新建会话 ===");
send({ type: "new_chat" });
if (!(await waitFor((f) => f.ev === "attached", 8000)) || !chatId) {
  console.error("new_chat failed");
  process.exit(1);
}
chatId = chatId.trim();
console.log("chat_id =", chatId);

console.log("\n=== 2) 发一条普通消息（与前端一致：带客户端 turn_id + webui:true）===");
turnId = crypto.randomUUID();
frames.length = 0;
send({
  type: "message",
  chat_id: chatId,
  content: "只回复 ok 两个字符",
  turn_id: turnId,
  webui: true,
});
await waitFor((f) => f.ev === "turn_end", 60000);
await wait(1500);
console.log("turn_id =", turnId);
console.log("本轮帧序列:", frames.map((f) => f.ev).join(" -> "));
console.log("turn_end 帧:", JSON.stringify(frames.find((f) => f.ev === "turn_end")?.o ?? null));
console.log("暂停后服务端会话状态:", JSON.stringify(await serverRunStartedAt(chatId)));

console.log("\n=== 3) 精确重放前端 stop()：sendMessage(chatId, '/stop')（无 turn_id）===");
frames.length = 0;
send({ type: "message", chat_id: chatId, content: "/stop", webui: true });
await wait(6000);
console.log("stop 后帧序列:", frames.map((f) => f.ev).join(" -> "));
console.log("stop 后服务端会话状态:", JSON.stringify(await serverRunStartedAt(chatId)));

console.log("\n=== 4) 再点一次停止 ===");
frames.length = 0;
send({ type: "message", chat_id: chatId, content: "/stop", webui: true });
await wait(6000);
console.log("第二次 stop 后帧序列:", frames.map((f) => f.ev).join(" -> "));
console.log("第二次 stop 后服务端会话状态:", JSON.stringify(await serverRunStartedAt(chatId)));

console.log("\n完成，chat_id =", chatId);
ws.close();
await wait(300);
process.exit(0);
