// 本地验收：使用真实聊天组件与内存样例，不连接网关、不调用模型。
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import "/src/globals.css";
import { initializeI18n, setAppLanguage } from "/src/i18n";
import { ThreadViewport } from "/src/components/thread/ThreadViewport";
import type { UIMessage } from "/src/lib/types";

await initializeI18n();
await setAppLanguage("zh-CN");
const at = Date.now();
const history: UIMessage[] = Array.from({ length: 6 }, (_, i) => ([
  { id: `old-u-${i}`, role: "user", content: "请检查工单重复创建的原因。", createdAt: at - 120000 + i * 1000 },
  { id: `old-a-${i}`, role: "assistant", content: "已检查事件接收与数据库事务边界，接下来验证重复事件处理。", createdAt: at - 119500 + i * 1000 },
])).flat() as UIMessage[];

function Preview() {
  const [mode, setMode] = useState("idle");
  const [turn, setTurn] = useState(1);
  const active = !["idle", "done"].includes(mode);
  const turnId = `turn-${turn}`;
  const prompt: UIMessage = { id: `prompt-${turn}`, role: "user", content: "在临时 SQLite 数据库验证同一事件只创建一张工单，并保存验证结果。", turnId, createdAt: at };
  const trace = `exec(${JSON.stringify({ command: "python -m pytest tests/test_ticket_dedup.py -q", description: "验证重复事件只创建一张工单" })})`;
  const tool: UIMessage = {
    id: `tool-${turn}`, role: "tool", kind: "trace", content: trace, traces: [trace], turnId, createdAt: at + 100,
    toolEvents: [{ name: "exec", phase: "end", call_id: "test-call", arguments: { command: "python -m pytest tests/test_ticket_dedup.py -q", description: "验证重复事件只创建一张工单" }, result: "5 passed" }],
  };
  const reasoning: UIMessage = {
    id: `reason-${turn}`, role: "assistant", content: "", turnId, createdAt: at + 200,
    reasoning: "正在核对同租户重复事件、不同租户相同编号与事务回滚三种边界。\n\n先完成临时数据库中的验证，再将真实结果写入测试报告。不会将未经执行的项目标记为通过。\n\n" + "下一步检查并发写入是否受到唯一索引保护，以及失败时去重记录是否会一并回滚。\n".repeat(8),
    reasoningStreaming: mode === "thinking", isStreaming: mode === "thinking",
  };
  const answer: UIMessage = { id: `answer-${turn}`, role: "assistant", content: mode === "long" ? "## 验证结果\n\n" + "同一租户重复事件仅创建一张工单。不同租户相同事件编号可各自创建。\n\n".repeat(24) : "我按这四条约束整理了验证方案，接下来写入测试报告并核对结果。", turnId, createdAt: at + 300, isStreaming: active };
  const pending: UIMessage = {
    id: `pending-${turn}`, role: "tool", kind: "trace", content: "", traces: [], turnId, createdAt: at + 400,
    fileEdits: [{ call_id: "write-report", tool: "write_file", path: "", status: "editing", pending: true, added: 0, deleted: 0, approximate: true }],
  };
  const body = mode === "idle" ? history : [...history, prompt, ...(mode === "waiting" ? [] : mode === "thinking" ? [tool, reasoning] : mode === "file" ? [tool, answer, pending] : [tool, answer])];
  return <main className="flex flex-col bg-background text-foreground" style={{ height: "100vh" }}>
    <nav className="flex flex-wrap gap-2 border-b p-3 text-sm">
      {[['waiting','开始等待'],['thinking','展开思考'],['text','正文暂停'],['file','准备写文件'],['long','长回复'],['done','完成']].map(([key, name]) => <button key={key} data-mode={key} className="rounded-md border px-3 py-1.5" onClick={() => setMode(key)}>{name}</button>)}
      <button className="rounded-md border px-3 py-1.5" onClick={() => { setTurn(x => x + 1); setMode('waiting'); }}>新一轮</button>
      <button className="rounded-md border px-3 py-1.5" onClick={() => document.documentElement.classList.toggle('dark')}>切换主题</button>
    </nav>
    <ThreadViewport messages={body} isStreaming={active} activeTurnId={mode === 'idle' || mode === 'done' ? null : turnId} activeTurnStartedHere={active} conversationKey="local-preview" composer={<div className="rounded-2xl border bg-background p-4"><div className="mb-6 text-sm text-muted-foreground">{active ? '模型正在回复…' : '输入消息'}</div><div className="flex justify-between text-xs text-muted-foreground"><span>＋　项目 · 此计算机</span><span>{active ? '■ 停止' : '发送'}</span></div></div>} />
  </main>;
}
createRoot(document.getElementById("root")!).render(<Preview />);
