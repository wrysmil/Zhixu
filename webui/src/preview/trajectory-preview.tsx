import { useState } from "react";
import ReactDOM from "react-dom/client";
import { Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";

import "../globals.css";
import { initializeI18n } from "../i18n";
import { TrajectoryPanel } from "@/components/thread/trajectory/TrajectoryPanel";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { cn } from "@/lib/utils";

const BASE = new Date(2026, 10, 13, 16, 12, 30).getTime();

type Ref = {
  bodyId: string | null;
  preview: string;
  contentType: string;
  storedBytes: number;
  originalBytes: number | null;
  redacted: boolean;
  truncated: boolean;
  unavailableReason: string | null;
};

const bodies: Record<string, string> = {};

function ref(bodyId: string, preview: string, contentType = "text/plain"): Ref {
  return {
    bodyId,
    preview,
    contentType,
    storedBytes: preview.length,
    originalBytes: preview.length,
    redacted: false,
    truncated: false,
    unavailableReason: null,
  };
}

function body(bodyId: string, text: string): Ref {
  bodies[bodyId] = text;
  return ref(bodyId, text.slice(0, 180), "application/json");
}

function textBody(bodyId: string, text: string): Ref {
  bodies[bodyId] = text;
  return ref(bodyId, text.slice(0, 180));
}

function record(
  recordId: string,
  kind: string,
  turnId: string,
  step: number,
  startedAtMs: number,
  durationMs: number,
  blocks: Array<{ kind: string; ref: Ref }>,
  extra: Record<string, unknown> = {},
  requestRef: Ref | null = null,
  status = "succeeded",
) {
  return {
    recordId,
    kind,
    runId: "run-1",
    turnId,
    step,
    spanId: null,
    parentSpanId: null,
    modelOrder: step,
    status,
    startedAtMs,
    finishedAtMs: startedAtMs + durationMs,
    durationMs,
    anchorSeq: 1,
    recordRevision: startedAtMs,
    blockSummaries: blocks.map((block, index) => ({
      index, kind: block.kind, content: block.ref,
    })),
    requestRef,
    schemaRef: null,
    extra,
  };
}

const recordPage = {
  schemaVersion: 1,
  records: [
    record("user:u1", "user", "turn-1", -1, BASE, 0, [
      { kind: "text", ref: ref("b-u1", "暂停，先提交这一步，再把测试跑一遍。") },
    ]),
    record("ctx:c1", "context", "turn-1", 0, BASE + 300, 0, [
      { kind: "text", ref: ref("b-c1", "记忆注入 · 项目约定：提交前跑 WebUI 类型检查") },
    ]),
    record("assistant:req-1", "assistant", "turn-1", 0, BASE + 600, 7800, [
      { kind: "reasoning", ref: ref("b-r1", "先确认当前改动范围，再停掉正在跑的生成。") },
      { kind: "text", ref: ref("b-a1", "我先确认当前改动范围，再停掉正在跑的生成。") },
    ], {
      provider: "deepseek",
      model: "deepseek-v3.2",
      attempts: [{ attempt: 1, provider: "deepseek" }],
      usage: { inputTokens: 8421, outputTokens: 96, source: "reported" },
    }, body("b-req1", JSON.stringify({
      system: "You are Zhixu, a careful coding agent.",
      messages: [{ role: "user", content: "暂停，先提交这一步，再把测试跑一遍。" }],
      tools: ["read_file", "exec", "interrupt_agent"],
      generation: { model: "deepseek-v3.2", temperature: 0.1 },
    }, null, 2))),
    record("tool:t1", "tool", "turn-1", 0, BASE + 8600, 620, [
      { kind: "result", ref: textBody("b-t1r", "已暂停当前生成") },
    ], {
      name: "interrupt_agent",
      argumentsPreview: "{\"reason\":\"awaiting user confirmation\"}",
      origin: "agent",
    }, body("b-t1req", "{\"reason\":\"awaiting user confirmation\"}")),
    record("assistant:req-2", "assistant", "turn-1", 1, BASE + 9500, 2400, [
      { kind: "tool-call", ref: ref("b-tc2", "[{\"name\":\"exec\",\"arguments\":{\"command\":\"npm test\"}}]") },
    ], {
      provider: "deepseek",
      model: "deepseek-v3.2",
      usage: { inputTokens: 9010, outputTokens: 41, source: "reported" },
    }, body("b-req2", JSON.stringify({
      messages: [{ role: "user", content: "暂停，先提交这一步，再把测试跑一遍。" }],
      tools: ["read_file", "exec"],
      generation: { model: "deepseek-v3.2", temperature: 0.1 },
    }, null, 2))),
    record("tool:t2", "tool", "turn-1", 1, BASE + 12100, 16300, [
      { kind: "result", ref: textBody("b-t2r", "Test Files  3 passed (3)\nTests  113 passed (113)") },
    ], {
      name: "exec",
      argumentsPreview: "{\"command\":\"npm test\",\"cwd\":\"webui\"}",
      origin: "agent",
    }, body("b-t2req", "{\"command\":\"npm test\",\"cwd\":\"webui\"}")),
    record("ctx:c2", "context", "turn-1", 2, BASE + 28700, 0, [
      { kind: "text", ref: ref("b-c2", "子 Agent 运行结果：类型检查通过，无新增错误。") },
    ]),
    record("assistant:req-3", "assistant", "turn-1", 2, BASE + 29000, 3600, [
      { kind: "text", ref: ref("b-a3", "测试与类型检查都通过了：113 个前端用例全绿，轨迹视图没有引入回归。") },
    ], {
      provider: "deepseek",
      model: "deepseek-v3.2",
      usage: { inputTokens: 9680, outputTokens: 58, source: "reported" },
    }, body("b-req3", JSON.stringify({
      messages: [{ role: "tool", content: "Test Files 3 passed (3)" }],
      generation: { model: "deepseek-v3.2", temperature: 0.1 },
    }, null, 2))),
    record("user:u2", "user", "turn-2", -1, BASE + 61000, 0, [
      { kind: "text", ref: ref("b-u2", "把轨迹页的暗色再看一下。") },
    ]),
    record("assistant:req-4", "assistant", "turn-2", 0, BASE + 61600, 2800, [
      { kind: "text", ref: ref("b-a4", "好的，暗色下三泳道和台账的对比度已确认。") },
    ], {
      provider: "deepseek",
      model: "deepseek-v3.2",
      usage: { inputTokens: 9890, outputTokens: 44, source: "reported" },
    }, body("b-req4", JSON.stringify({
      messages: [{ role: "user", content: "把轨迹页的暗色再看一下。" }],
      generation: { model: "deepseek-v3.2", temperature: 0.1 },
    }, null, 2))),
  ],
  ancestorStubs: [],
  snapshotSeq: 42,
  historyGeneration: 1,
  nextBeforeCursor: null,
  hasOlder: false,
  retainedFromSeq: 1,
  coverage: { status: "complete", reasons: [], contentMode: "standard", attemptVisibility: "provider_call" },
};

const summary = {
  schemaVersion: 1,
  sessionId: "websocket:preview-chat",
  mode: "standard",
  available: true,
  persistence: "sqlite",
  retainedFromSeq: 1,
  durableSeq: 42,
  historyGeneration: 1,
  coverage: { status: "complete", reasons: [], contentMode: "standard", attemptVisibility: "provider_call" },
  runs: [],
  capabilities: { events: true, bodies: true, memory: false },
};

function eventsPage() {
  return {
    schemaVersion: 1,
    events: [],
    recordUpdates: [],
    snapshotSeq: 42,
    retainedFromSeq: 1,
    durableSeq: 42,
    historyGeneration: 1,
    nextBeforeSeq: null,
    nextAfterSeq: 42,
    hasOlder: false,
    hasMore: false,
    coverage: recordPage.coverage,
  };
}

const originalFetch = window.fetch.bind(window);
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  if (url.includes("/api/trajectory/")) {
    let payload: unknown = summary;
    if (url.includes("/records")) payload = recordPage;
    else if (url.includes("/events")) payload = eventsPage();
    else if (url.includes("/bodies/")) {
      const bodyId = decodeURIComponent(url.split("/bodies/")[1].split("?")[0]);
      payload = {
        bodyId,
        contentType: "application/json",
        text: bodies[bodyId] ?? "（预览正文）",
        offsetBytes: 0,
        nextOffsetBytes: bodies[bodyId]?.length ?? 0,
        hasMore: false,
        storedBytes: bodies[bodyId]?.length ?? 0,
        originalBytes: bodies[bodyId]?.length ?? 0,
        redacted: false,
        truncated: false,
      };
    }
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  return originalFetch(input, init);
};

function PreviewTrajectory() {
  const { t } = useTranslation();
  const [dark, setDark] = useState(false);
  const toggleDark = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
  };
  return (
    <div className="flex h-screen min-h-0 flex-col bg-background text-foreground">
      <header className="relative flex h-11 shrink-0 items-center justify-between border-b border-border/60 px-3">
        <div className="flex min-w-0 items-center gap-2 text-[12px]">
          <span className="max-w-[22rem] truncate font-medium text-muted-foreground">
            重构执行轨迹
          </span>
          <span className="rounded-md px-1.5 py-1 text-[12px] font-medium text-muted-foreground">
            @preview
          </span>
        </div>
        <div className="absolute left-1/2 -translate-x-1/2">
          <SegmentedControl
            mode="tabs"
            ariaLabel="Thread view"
            value="trajectory"
            options={[
              { value: "chat", label: t("thread.trajectory.tabs.chat") },
              { value: "trajectory", label: t("thread.trajectory.tabs.trajectory") },
            ]}
            onChange={() => undefined}
          />
        </div>
        <button
          type="button"
          onClick={toggleDark}
          aria-label="toggle theme"
          className="rounded-full p-1.5 text-muted-foreground hover:bg-accent/40 hover:text-foreground"
        >
          {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </header>
      <div className="flex min-h-0 flex-1 flex-col">
        <TrajectoryPanel
          sessionKey="websocket:preview-chat"
          active
          getToken={() => "preview"}
          composer={(
            <div className={cn("mx-3 mb-3 mt-2 rounded-2xl border border-border/60 bg-background px-4 py-3 text-[13px] text-muted-foreground shadow-sm")}>
              输入消息…（界面预览，不连接模型）
            </div>
          )}
        />
      </div>
    </div>
  );
}

async function main() {
  await initializeI18n();
  ReactDOM.createRoot(document.getElementById("root")!).render(<PreviewTrajectory />);
}

void main();
