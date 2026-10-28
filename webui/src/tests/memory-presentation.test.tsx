import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { MemoryListView } from "@/components/settings/memory/MemoryListView";
import { EpisodeListView } from "@/components/settings/memory/EpisodeListView";
import { MemoryPage } from "@/components/workspace/MemoryPage";
import type { EpisodePayload, MemoryPayload, SettingsPayload } from "@/lib/types";

const api = vi.hoisted(() => ({ listMemories: vi.fn(), listEpisodes: vi.fn(), fetchMemory: vi.fn(), fetchEpisode: vi.fn(), deleteMemory: vi.fn(), deleteEpisode: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({ ...await original<typeof import("@/lib/api")>(), ...api }));
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: "token", client: {} }) }));

const workspace = { id: "ws-zhixu", name: "Zhixu", path: "D:/projects/Zhixu" };
const memory: MemoryPayload = {
  id: "m1", content: "文件修改前先说明改动边界", type: "rule", priority: "long_term", source: "extraction",
  importance_score: 0.8, access_count: 3, tags: ["协作"], subject: "用户", predicate: "偏好", confidence: 0.9,
  decay_rate: 0.1, expires_at: null, last_accessed_at: null, superseded_by: null, source_episode_id: null,
  scope: "global", workspace_id: "default", created_at: "2026-10-27T09:05:00", updated_at: "2026-10-27T09:05:00", metadata: {},
  ...{ origin_kind: "ai", generation_method: "ai", trigger_mode: "unknown", trigger_reason: "idle", workspaces: [workspace] },
};
const episode: EpisodePayload = { id: "e1", session_id: "websocket:a-long-session-id", summary: "修复了筛选状态", goal: "修复记忆页", outcome: "completed", source: "idle", started_at: "2026-10-27T10:15:00", ended_at: "2026-10-27T10:20:00", action_nodes: [], entities: [], tools_used: ["edit_file"], linked_memory_ids: ["m1"], tags: [], importance_score: 0.7, access_count: 1, ...{ origin_kind: "ai", generation_method: "ai", trigger_mode: "automatic", trigger_reason: "idle", workspaces: [workspace] } };
const result = <T,>(items: T[]) => ({ items, total: items.length, source_counts: { all: items.length, ai: items.length, manual: 0, unknown: 0 }, workspace_options: [workspace], scope: "current_store" });

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("zh-CN");
  api.listMemories.mockResolvedValue(result([memory]));
  api.listEpisodes.mockResolvedValue(result([episode]));
  api.fetchMemory.mockResolvedValue({ memory });
  api.fetchEpisode.mockResolvedValue({ episode });
});
afterEach(cleanup);

describe("记忆页面信息与筛选", () => {
  it("保留记忆中心面包屑，移除大标题介绍区并保留分类和筛选", async () => {
    const { container } = render(<MemoryPage settingsSnapshot={null} onSettingsChange={vi.fn()} />);
    expect(await screen.findByRole("tab", { name: "长期信息" })).toBeInTheDocument();
    expect(screen.getByText("记忆中心")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "对话记录" })).toBeInTheDocument();
    expect(screen.getByRole("switch")).toBeInTheDocument();
    expect(screen.getByRole("searchbox")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
    expect(container.querySelector(".assistant-page-header")).toBeNull();
    expect(container.querySelector("img[src='/illustrations/zhixu-toolbox.webp']")).toBeNull();
  });
  it("常驻展示完整日期、来源、工作区、重要性与引用次数，并使用图标详情", async () => {
    render(<MemoryListView />);
    expect(await screen.findByText(memory.content)).toBeInTheDocument();
    expect(screen.getByText("2026-10-27 09:05")).toBeInTheDocument();
    expect(screen.getAllByText("Zhixu").length).toBeGreaterThan(0);
    expect(screen.getByText("重要性 高 · 80%")).toHaveClass("text-amber-800");
    expect(screen.getByText("已引用 3 次")).toBeInTheDocument();
    const row = screen.getByText(memory.content).closest("li")!;
    expect(within(row).getByRole("button", { name: "查看详情" })).toHaveAttribute("title", "查看详情");
    fireEvent.click(within(row).getByRole("button", { name: "查看详情" }));
    expect(await screen.findByRole("dialog", { name: "记忆详情" })).toBeInTheDocument();
    expect(await screen.findByText("置信度")).toBeInTheDocument();
  });

  it("将来源、工作区、关键词一起提交，清空搜索后恢复当前筛选", async () => {
    render(<MemoryListView />);
    await screen.findByText(memory.content);
    fireEvent.click(screen.getByRole("button", { name: /手动触发/ }));
    fireEvent.change(screen.getByRole("combobox", { name: "工作区" }), { target: { value: workspace.id } });
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "边界" } });
    await waitFor(() => expect(api.listMemories).toHaveBeenLastCalledWith("token", expect.objectContaining({ origin: "manual", workspace: workspace.id, q: "边界" })));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    await waitFor(() => expect(api.listMemories).toHaveBeenLastCalledWith("token", expect.objectContaining({ origin: "manual", workspace: workspace.id, q: undefined })));
  });

  it("忽略旧列表响应，避免快速切换后覆盖最新来源筛选", async () => {
    let resolveOld!: (value: ReturnType<typeof result<MemoryPayload>>) => void;
    api.listMemories.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    render(<MemoryListView />);
    await waitFor(() => expect(api.listMemories).toHaveBeenCalledTimes(1));
    api.listMemories.mockResolvedValue(result([{ ...memory, content: "最新手动记录" }]));
    fireEvent.click(screen.getByRole("button", { name: /手动触发/ }));
    await screen.findByText("最新手动记录");
    await act(async () => { resolveOld(result([memory])); });
    expect(screen.queryByText(memory.content)).not.toBeInTheDocument();
  });

  it("对话列表展示目标、开始结束时间及结果，并提供可访问详情", async () => {
    render(<EpisodeListView />);
    expect(await screen.findByText(episode.summary)).toBeInTheDocument();
    expect(screen.getByText(episode.goal)).toBeInTheDocument();
    expect(screen.getByText("2026-10-27 10:15")).toBeInTheDocument();
    expect(screen.getByText("2026-10-27 10:20")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    expect(await screen.findByText(episode.session_id)).toBeInTheDocument();
  });

  it("两类列表支持按重要程度从高到低排序", async () => {
    const memoryView = render(<MemoryListView />);
    await screen.findByText(memory.content);
    fireEvent.change(screen.getByRole("combobox", { name: "排序" }), { target: { value: "importance" } });
    await waitFor(() => expect(api.listMemories).toHaveBeenLastCalledWith("token", expect.objectContaining({ order: "importance" })));
    memoryView.unmount();
    render(<EpisodeListView />);
    await screen.findByText(episode.summary);
    fireEvent.change(screen.getByRole("combobox", { name: "排序" }), { target: { value: "importance" } });
    await waitFor(() => expect(api.listEpisodes).toHaveBeenLastCalledWith("token", expect.objectContaining({ order: "importance" })));
    expect(screen.getByText("重要性 中 · 70%")).toHaveClass("text-blue-800");
  });

  it("把搜索、工作区和次级筛选集中在同一工具栏，保留独立来源入口", async () => {
    render(<MemoryListView />);
    await screen.findByText(memory.content);
    const toolbar = screen.getByRole("toolbar", { name: "记忆筛选" });
    expect(within(toolbar).getByRole("searchbox")).toBeInTheDocument();
    expect(within(toolbar).getByRole("combobox", { name: "工作区" })).toBeInTheDocument();
    expect(within(toolbar).getByRole("combobox", { name: "类型" })).toBeInTheDocument();
    expect(within(toolbar).getByRole("combobox", { name: "保留时长" })).toBeInTheDocument();
    expect(within(toolbar).getByRole("combobox", { name: "排序" })).toBeInTheDocument();
    expect(within(toolbar).getByRole("status")).toHaveTextContent("筛选后 1 条");
    expect(within(toolbar).queryByRole("button", { name: /手动触发/ })).toBeNull();
    expect(screen.queryByText("按当前记忆库中保存的工作区归属筛选。")).toBeNull();
  });

  it("对话记录使用同一紧凑工具栏，重新查询时仍显示加载提示", async () => {
    render(<EpisodeListView />);
    await screen.findByText(episode.summary);
    const toolbar = screen.getByRole("toolbar", { name: "记忆筛选" });
    expect(within(toolbar).getByRole("combobox", { name: "排序" })).toBeInTheDocument();
    api.listEpisodes.mockReturnValueOnce(new Promise(() => {}));
    fireEvent.click(screen.getByRole("button", { name: /手动触发/ }));
    expect(await within(toolbar).findByLabelText("加载中")).toBeInTheDocument();
    expect(screen.getByText(episode.summary)).toBeInTheDocument();
    expect(screen.queryByText("按当前记忆库中保存的工作区归属筛选。")).toBeNull();
  });

  it("展示记忆的来源会话，缺标题时降级显示会话 id，手动记忆不显示", async () => {
    api.listMemories.mockResolvedValue(result([
      { ...memory, id: "m-titled", session_id: "websocket:deploy-2026", session_title: "部署流程讨论" },
      { ...memory, id: "m-untitled", session_id: "websocket:deleted-session", session_title: null },
      { ...memory, id: "m-manual", session_id: null },
    ]));
    render(<MemoryListView />);
    expect(await screen.findByText("会话：部署流程讨论")).toBeInTheDocument();
    expect(screen.getByText("会话：websocket:deleted-")).toBeInTheDocument();
    expect(screen.queryByText("来源会话未记录")).toBeNull();
  });

  it("深链直开时跟随后端异步加载到的设置同步自动记忆开关", async () => {
    const { rerender } = render(<MemoryPage settingsSnapshot={null} onSettingsChange={vi.fn()} />);
    const toggle = await screen.findByRole("switch");
    expect(toggle).toHaveAttribute("aria-checked", "false");

    rerender(
      <MemoryPage
        settingsSnapshot={{ runtime: { memory_enabled: true } } as unknown as SettingsPayload}
        onSettingsChange={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true"));
  });
});
