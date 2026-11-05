import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionWorkbench } from "@/components/workbench/SessionWorkbench";
import { useSessionWorkbench } from "@/components/workbench/use-session-workbench";
import type { SessionWorkbenchSnapshot } from "@/components/workbench/session-workbench-api";
import type { SessionWorkbenchModel } from "@/components/workbench/session-workbench-model";
import i18n from "@/i18n";

const snapshot = (session_key = "websocket:one"): SessionWorkbenchSnapshot => ({
  schema_version: 1, session_key, updated_at: null, goal: null,
  tasks: [{ id: "t1", title: "接通会话计划", status: "in_progress", source: "workbench_plan" }],
  plan: { explanation: "按真实数据接入", steps: [], updated_at: null },
  agents: [{ id: "a1", name: "界面 Agent", status: "running", detail: "正在检查", invocation_id: "exec-1" }],
  memories: [{ id: "m1", kind: "working", title: "当前约束", content: "保持真实数据", source: "scratchpad", persisted: false, updated_at: null }],
  skills: [{ name: "design", status: "available", source: "workspace" }],
  mcp: [{ name: "github", status: "connected", calls: 0, successes: null, failures: null }], limitations: [],
});
const model: SessionWorkbenchModel = {
  updatedAtMs: null, summary: "", diffFiles: 0, diffAdded: 0, diffDeleted: 0,
  projectName: "", projectPath: "", accessMode: null, mcpUsed: [], cliUsed: [], outputs: [], sources: [],
};
function reply(body: SessionWorkbenchSnapshot) { return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }); }
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("会话工作台真实数据", () => {
  it("会话切换立即清旧数据且丢弃延迟响应", async () => {
    let finishOld!: (response: Response) => void;
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { finishOld = resolve; }))
      .mockResolvedValueOnce(reply(snapshot("websocket:two")));
    vi.stubGlobal("fetch", fetcher);
    const { result, rerender } = renderHook(({ key }) => useSessionWorkbench({ sessionKey: key, token: "tok" }), { initialProps: { key: "websocket:one" } });
    rerender({ key: "websocket:two" });
    expect(result.current.snapshot).toBeNull();
    await waitFor(() => expect(result.current.snapshot?.session_key).toBe("websocket:two"));
    await act(async () => { finishOld(reply(snapshot())); });
    expect(result.current.snapshot?.session_key).toBe("websocket:two");
    expect(fetcher.mock.calls[1][0]).toBe("/api/sessions/websocket%3Atwo/workbench");
  });

  it("权限错误保持错误态并允许重试", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response('{"error":"forbidden"}', { status: 403 }))
      .mockResolvedValueOnce(reply(snapshot())));
    const { result } = renderHook(() => useSessionWorkbench({ sessionKey: "websocket:one", token: "tok" }));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.snapshot).toBeNull();
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.snapshot?.tasks).toHaveLength(1));
    expect(result.current.error).toBeNull();
  });

  it("展示真实计划和执行详情入口并区分能力可用与已使用", async () => {
    await i18n.changeLanguage("zh");
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(reply(snapshot()))));
    const open = vi.fn();
    render(<SessionWorkbench model={model} sessionKey="websocket:one" token="tok" onOpenExecution={open} />);
    await screen.findByText("接通会话计划");
    fireEvent.click(screen.getByRole("button", { name: /界面 Agent/ }));
    expect(open).toHaveBeenCalledWith("exec-1");
    fireEvent.click(screen.getByRole("tab", { name: /记忆/ }));
    expect(screen.getByText("尚未持久化")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /能力/ }));
    expect(screen.getByText("可用 · 未记录使用")).toBeInTheDocument();
    expect(screen.getByText("已连接")).toBeInTheDocument();
    expect(screen.getByText("尚未调用")).toBeInTheDocument();
    expect(screen.queryByText(/成功 0/)).toBeNull();
  });

  it("长任务持续限频刷新，隐藏页面暂停，卸载后不再请求", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(reply(snapshot())));
    vi.stubGlobal("fetch", fetcher);
    const { unmount } = renderHook(() => useSessionWorkbench({ sessionKey: "websocket:one", token: "tok" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(130_000); });
    expect(fetcher).toHaveBeenCalledTimes(14);
    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(fetcher).toHaveBeenCalledTimes(14);
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(fetcher).toHaveBeenCalledTimes(15);
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(fetcher).toHaveBeenCalledTimes(15);
  });

  it("把能力跟踪限制解释为用户可读提示", async () => {
    await i18n.changeLanguage("zh");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply({ ...snapshot(), limitations: ["mcp_call_outcomes_not_tracked"] })));
    render(<SessionWorkbench model={model} sessionKey="websocket:one" token="tok" />);
    await screen.findByText("接通会话计划");
    expect(screen.getByText("MCP 调用结果尚未跟踪，成功与失败次数显示为未知。")).toBeInTheDocument();
    expect(screen.queryByText("mcp_call_outcomes_not_tracked")).toBeNull();
  });

  it.each([
    { ...snapshot(), tasks: [null] },
    { ...snapshot(), plan: { explanation: "缺少步骤" } },
    { ...snapshot(), tasks: [{ id: "bad", title: "错误状态", source: "plan", status: "invented" }] },
  ])("错形接口响应进入错误面板而不崩溃", async (payload) => {
    await i18n.changeLanguage("zh");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } })));
    render(<SessionWorkbench model={model} sessionKey="websocket:one" token="tok" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("工作台返回的数据格式不正确");
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});

it("即时工具结束不会被延迟快照回退，断线时标记过期", async () => {
  let chat!: (event: any) => void;
  let status!: (value: any) => void;
  const client = { status: "open", onChat: (_id: string, cb: any) => { chat = cb; return () => {}; }, onStatus: (cb: any) => { status = cb; return () => {}; }, onSessionUpdate: () => () => {} } as any;
  let finish!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve; })));
  const { result } = renderHook(() => useSessionWorkbench({ sessionKey: "websocket:one", token: "tok", client }));
  act(() => { chat({ event: "message_accepted", chat_id: "one", turn_id: "t1", starts_turn: true }); chat({ event: "message", chat_id: "one", turn_id: "t1", text: "", tool_events: [{ phase: "start", call_id: "c1", name: "read" }] }); });
  expect(result.current.activity?.events[0].status).toBe("running");
  act(() => chat({ event: "message", chat_id: "one", turn_id: "t1", text: "", tool_events: [{ phase: "end", call_id: "c1", name: "read" }] }));
  await act(async () => finish(reply(snapshot())));
  expect(result.current.activity?.events[0].status).toBe("succeeded");
  act(() => status("reconnecting"));
  expect(result.current.stale).toBe(true);
});

it("项目切换丢弃旧响应，断线不显示运行中的工具", async () => {
  let chat!: (event: any) => void; let status!: (value: any) => void; let finish!: (response: Response) => void;
  const client = { status: "open", onChat: (_: string, cb: any) => { chat = cb; return () => {}; }, onStatus: (cb: any) => { status = cb; return () => {}; }, onSessionUpdate: () => () => {} } as any;
  vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => new Promise<Response>(r => { finish = r; })).mockResolvedValue(reply(snapshot())));
  const { result, rerender } = renderHook(({ projectIdentity }) => useSessionWorkbench({ sessionKey: "websocket:one", token: "tok", client, projectIdentity }), { initialProps: { projectIdentity: "A" } });
  act(() => { chat({ event: "message_accepted", turn_id: "t", starts_turn: true }); chat({ event: "message", turn_id: "t", tool_events: [{ call_id: "c", phase: "start", name: "read" }] }); status("reconnecting"); });
  expect(result.current.stale).toBe(true);
  expect(result.current.activity?.events).toHaveLength(0);
  rerender({ projectIdentity: "B" });
  await act(async () => finish(reply({ ...snapshot(), tasks: [{ id: "old", title: "旧项目", status: "pending", source: "old" }] })));
  expect(result.current.snapshot?.tasks.some(item => item.title === "旧项目")).toBeFalsy();
});

it("技能按来源分组，区分可用性、读取状态及覆盖关系", async () => {
  await i18n.changeLanguage("zh");
  const empty = { items: [], total: 0, next_cursor: null, revision: "r" };
  const item = { id: "one", name: "skill-a", description: "", scope: "workspace" as const, origin: "workspace" as const, effective: false, availability: "requirements_missing" as const, observed_state: "read" as const, overridden_by: "other", source_label: "project", requirement_labels: ["python"] };
  const data = { ...snapshot(), skill_catalog: { available_total: 0, groups: { workspace: { ...empty, items: [item], total: 1 }, global: empty, plugin: empty, builtin: empty } } };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(data)));
  render(<SessionWorkbench model={model} sessionKey="websocket:one" token="tok" />);
  await screen.findByText("接通会话计划"); fireEvent.click(screen.getByRole("tab", { name: /能力/ }));
  expect(screen.getByText("缺少依赖 · 已读取")).toBeInTheDocument();
  expect(screen.getByText(/被覆盖: other/)).toBeInTheDocument();
  expect(screen.getByText(/可用技能: 0/)).toBeInTheDocument();
});
