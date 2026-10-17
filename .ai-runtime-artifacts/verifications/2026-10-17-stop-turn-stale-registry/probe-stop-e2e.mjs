// 端到端复现「点停止仍显示回答中」：精确重放前端 stop() 的帧，抓完整帧序列。
// 关注三件事：
//   1) /stop 后服务端到底发了哪些生命周期帧（turn_end? goal_status idle? 都没有?）
//   2) 这些帧的 turn_id 与被停止那一轮的 turn_id 是否一致（前端 stale 过滤器的判据）
//   3) 后端注册表 run_started_at 是否残留
const BASE = "http://127.0.0.1:8765";
const fs = await import("node:fs");
const cfg = JSON.parse(fs.readFileSync(`${process.env.HOME}/.nanobot/config.json`, "utf8"));
const SECRET =
  (cfg.channels?.websocket?.tokenIssueSecret || "").trim() ||
  (cfg.channels?.websocket?.token || "").trim();
const boot = await (
  await fetch(`${BASE}/webui/bootstrap`, { headers: { Authorization: `Bearer ${SECRET}` } })
).json();
const apiToken = boot.api_token;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const ws = new WebSocket(`ws://127.0.0.1:8765/?token=${encodeURIComponent(boot.token)}`);
const frames = [];
const t0 = Date.now();
ws.addEventListener("message", (e) => {
  let o;
  try {
    o = JSON.parse(e.data);
  } catch {
    return;
  }
  frames.push(o);
  const ev = o.event ?? o.type;
  const brief = {};
  for (const k of ["turn_id", "active_turn_id", "status", "started_at", "content", "text", "persistence"]) {
    if (o[k] !== undefined) brief[k] = typeof o[k] === "string" ? o[k].slice(0, 60) : o[k];
  }
  console.log(`+${String(Date.now() - t0).padStart(6)}ms <= ${ev} ${JSON.stringify(brief)}`);
});
await new Promise((r) => ws.addEventListener("open", r));
await wait(300);
const send = (f) => {
  console.log(`=> send ${JSON.stringify(f).slice(0, 140)}`);
  ws.send(JSON.stringify(f));
};
const runState = async (chatId) => {
  const d = await (
    await fetch(`${BASE}/api/sessions`, { headers: { Authorization: `Bearer ${apiToken}` } })
  ).json();
  const row = (d.sessions ?? []).find((s) => s.key === `websocket:${chatId}`);
  return row?.run_started_at ?? "NONE";
};

const chatId = `probe-stop2-${Math.random().toString(36).slice(2, 8)}`;
const turnId = crypto.randomUUID();

console.log("\n=== A) 发一条会长跑的消息（带 turn_id，与前端一致）===");
send({
  type: "message",
  chat_id: chatId,
  content: "不要调用任何工具。请从 1 一直数到 60，每个数字单独一行，中间不要停顿。",
  turn_id: turnId,
  webui: true,
});
console.log("本轮 turn_id =", turnId);

// 等到确实开跑（goal_status running / run_status）再点停止
for (let i = 0; i < 100; i++) {
  if (frames.some((f) => f.event === "goal_status" && f.status === "running")) break;
  await wait(100);
}
await wait(1500);
console.log("停止前 run_started_at =", await runState(chatId));

console.log("\n=== B) 精确重放前端 stop()：content=\"/stop\" 且不带 turn_id ===");
frames.length = 0;
send({ type: "message", chat_id: chatId, content: "/stop", webui: true });
await wait(12000);

const kinds = frames.map((f) => f.event ?? f.type);
console.log("\n--- stop 之后帧种类:", JSON.stringify(kinds));
console.log("--- 是否出现 turn_end:", kinds.includes("turn_end"));
console.log(
  "--- goal_status:",
  JSON.stringify(
    frames.filter((f) => f.event === "goal_status").map((f) => ({ s: f.status, t: f.turn_id })),
  ),
);
console.log(
  "--- turn_end 详情:",
  JSON.stringify(frames.filter((f) => f.event === "turn_end").map((f) => ({ t: f.turn_id }))),
);
console.log(
  "--- 含 'stop' 字样的内容帧:",
  JSON.stringify(
    frames
      .filter((f) => typeof f.content === "string" && /stop/i.test(f.content))
      .map((f) => ({ ev: f.event, c: f.content.slice(0, 50), t: f.turn_id })),
  ),
);
console.log("--- 停止后 run_started_at =", await runState(chatId));
console.log("--- 被停止那一轮的 turn_id =", turnId);

console.log("\n=== C) 再点一次停止（模拟用户反复点）===");
frames.length = 0;
send({ type: "message", chat_id: chatId, content: "/stop", webui: true });
await wait(6000);
console.log("第二次 stop 帧种类:", JSON.stringify(frames.map((f) => f.event ?? f.type)));
console.log("第二次 stop 后 run_started_at =", await runState(chatId));

ws.close();
await wait(300);
process.exit(0);
