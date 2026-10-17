// 变化矩阵探针：找出「WebUI turn 注册后不被清理」的触发条件。
// 每个场景用独立 chat_id，跑完检查 /api/sessions 里是否残留 run_started_at。
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

async function connect() {
  const b = await (
    await fetch(`${BASE}/webui/bootstrap`, { headers: { Authorization: `Bearer ${SECRET}` } })
  ).json();
  const ws = new WebSocket(`ws://127.0.0.1:8765/?token=${encodeURIComponent(b.token)}`);
  const frames = [];
  ws.addEventListener("message", (e) => {
    try {
      const o = JSON.parse(e.data);
      frames.push(o);
    } catch {}
  });
  await new Promise((r) => ws.addEventListener("open", r));
  await wait(200);
  return { ws, frames, send: (f) => ws.send(JSON.stringify(f)) };
}

const runState = async (chatId) => {
  const r = await fetch(`${BASE}/api/sessions`, {
    headers: { Authorization: `Bearer ${apiToken}` },
  });
  const d = await r.json();
  const row = (d.sessions ?? []).find((s) => s.key === `websocket:${chatId}`);
  return row ? row.run_started_at ?? null : "NO_SESSION";
};

const report = async (label, chatId, frames) => {
  const kinds = [...new Set(frames.map((f) => f.event ?? f.type))];
  console.log(
    `${label.padEnd(38)} chat=${chatId.slice(0, 14).padEnd(14)} run_started_at=${String(
      await runState(chatId),
    ).padEnd(20)} frames=[${kinds.join(",")}]`,
  );
};

async function scenario(label, fn) {
  const chatId = `probe-stale-${Math.random().toString(36).slice(2, 8)}`;
  const c = await connect();
  await fn({ ...c, chatId });
  await wait(1200);
  await report(label, chatId, c.frames);
  try {
    c.ws.close();
  } catch {}
  await wait(300);
}

const MSG = "只回复 ok 两个字符";

// S1 基线：带 turn_id + webui:true
await scenario("S1 带 turn_id", async ({ send, chatId, frames }) => {
  send({ type: "message", chat_id: chatId, content: MSG, turn_id: crypto.randomUUID(), webui: true });
  for (let i = 0; i < 300 && !frames.some((f) => f.event === "turn_end"); i++) await wait(100);
  await wait(1200);
});

// S2 不带 turn_id
await scenario("S2 不带 turn_id", async ({ send, chatId, frames }) => {
  send({ type: "message", chat_id: chatId, content: MSG, webui: true });
  for (let i = 0; i < 300 && !frames.some((f) => f.event === "turn_end"); i++) await wait(100);
  await wait(1200);
});

// S3 发送后立刻断开连接（无订阅者）
await scenario("S3 发完即断开", async ({ send, chatId }) => {
  send({ type: "message", chat_id: chatId, content: MSG, turn_id: crypto.randomUUID(), webui: true });
  await wait(250);
  // 场景函数返回后由外层关闭连接；这里先等足够长时间让 turn 跑完
  await wait(9000);
});

// S4 turn 运行中点击停止（发送 /stop）
await scenario("S4 运行中 /stop", async ({ send, chatId, frames }) => {
  send({ type: "message", chat_id: chatId, content: MSG, turn_id: crypto.randomUUID(), webui: true });
  await wait(1200);
  send({ type: "message", chat_id: chatId, content: "/stop", webui: true });
  await wait(9000);
});

// S5 turn 运行中 /stop，然后断开
await scenario("S5 运行中 /stop 后断开", async ({ send, chatId, ws }) => {
  send({ type: "message", chat_id: chatId, content: MSG, turn_id: crypto.randomUUID(), webui: true });
  await wait(1200);
  send({ type: "message", chat_id: chatId, content: "/stop", webui: true });
  await wait(1500);
  ws.close();
  await wait(7000);
});

// S6 同一条消息重复发两次（同 turn_id）
await scenario("S6 同 turn_id 重发两次", async ({ send, chatId, frames }) => {
  const tid = crypto.randomUUID();
  send({ type: "message", chat_id: chatId, content: MSG, turn_id: tid, webui: true });
  await wait(300);
  send({ type: "message", chat_id: chatId, content: MSG, turn_id: tid, webui: true });
  for (let i = 0; i < 300 && !frames.some((f) => f.event === "turn_end"); i++) await wait(100);
  await wait(1500);
});

console.log("\n汇总（run_started_at 非 null 即为残留 turn 注册）");
process.exit(0);
