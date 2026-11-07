import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";
import { ExperienceListView } from "@/components/settings/memory/ExperienceListView";
import { LongTermMemoryView } from "@/components/settings/memory/LongTermMemoryView";

const api = vi.hoisted(() => ({
  fetchMemoryDoc: vi.fn(),
  saveMemoryDoc: vi.fn(),
  listExperiences: vi.fn(),
  fetchWorkspaces: vi.fn(),
  fetchSidebarState: vi.fn(),
}));

vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  ...api,
}));
vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({ token: "token", client: {} }),
}));

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("zh-CN");
  api.fetchMemoryDoc.mockResolvedValue({
    scope: "global",
    content: "## 约定\n- 构建用 bun",
    chars: 16,
    limit: 3000,
    revision: "r1",
    exists: true,
  });
  api.saveMemoryDoc.mockImplementation(async (_client, input) => ({
    scope: input.scope,
    content: input.content,
    chars: input.content.length,
    limit: 3000,
    revision: "r2",
    exists: true,
  }));
  api.fetchWorkspaces.mockResolvedValue({
    schema_version: 1,
    default_access_mode: "full",
    default_scope: {
      project_path: "D:/projects/alpha",
      project_name: "alpha",
      access_mode: "full",
    },
    controls: { can_change_project: true, can_use_full_access: true },
  });
  api.fetchSidebarState.mockResolvedValue({
    schema_version: 1,
    pinned_keys: [],
    archived_keys: [],
    session_order: [],
    title_overrides: {},
    project_name_overrides: {},
    pinned_project_keys: [],
    hidden_project_keys: [],
    project_entries: [
      {
        key: "d:/projects/beta",
        path: "D:/projects/beta",
        name: "beta 后端",
      },
    ],
    tags_by_key: {},
    collapsed_groups: {},
    workbench: { version: 1, tabs: {} },
    view: {
      density: "comfortable",
      show_previews: false,
      show_timestamps: false,
      show_archived: false,
      sort: "updated_desc",
    },
    updated_at: null,
  });
  api.listExperiences.mockResolvedValue({
    items: [
      {
        id: "e1",
        title: "构建失败",
        summary: "bun run build 报模块缺失\n根因：未安装依赖\n解法：bun install 后重跑",
        outcome: "failed",
        root_cause: "未安装依赖",
        solution: "bun install 后重跑",
        workspace_id: "default",
        ended_at: "2026-11-06T10:00:00",
      },
    ],
    total: 1,
  });
});
afterEach(cleanup);

describe("长期记忆 + 经验库页面", () => {
  it("长期记忆页读取正文并保存", async () => {
    render(<LongTermMemoryView />);
    const select = await screen.findByRole("combobox", { name: "选择项目" });
    await waitFor(() => expect(select).toHaveValue("D:/projects/alpha"));
    const editor = (await screen.findByRole("textbox")) as HTMLTextAreaElement;
    await waitFor(() => expect(editor.value).toContain("构建用 bun"));
    fireEvent.change(editor, { target: { value: "## 约定\n- 构建用 pnpm" } });
    fireEvent.click(screen.getByRole("button", { name: /保存|Save/ }));
    await waitFor(() =>
      expect(api.saveMemoryDoc).toHaveBeenCalledWith(
        {},
        expect.objectContaining({
          scope: "project",
          content: "## 约定\n- 构建用 pnpm",
          workspacePath: "D:/projects/alpha",
        }),
      ),
    );
  });

  it("默认展示当前项目记忆，并可在下拉里切换项目", async () => {
    render(<LongTermMemoryView />);
    await screen.findByRole("textbox");
    // 默认落在项目范围，不需要用户先点「项目」。
    const select = await screen.findByRole("combobox", { name: "选择项目" });
    await waitFor(() => expect(select).toHaveValue("D:/projects/alpha"));
    await waitFor(() =>
      expect(api.fetchMemoryDoc).toHaveBeenCalledWith("token", "project", {
        workspacePath: "D:/projects/alpha",
      }),
    );
    expect(screen.getByRole("option", { name: "beta 后端" })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "D:/projects/beta" } });
    await waitFor(() =>
      expect(api.fetchMemoryDoc).toHaveBeenLastCalledWith("token", "project", {
        workspacePath: "D:/projects/beta",
      }),
    );
  });

  it("经验库页展示失败、根因与解法", async () => {
    render(<ExperienceListView />);
    expect(await screen.findByText("构建失败")).toBeInTheDocument();
    expect(screen.getByText(/未安装依赖/)).toBeInTheDocument();
    expect(screen.getByText(/bun install 后重跑/)).toBeInTheDocument();
  });

  it("经验库可按类型筛选", async () => {
    render(<ExperienceListView />);
    await screen.findByText("构建失败");
    fireEvent.change(screen.getByRole("combobox", { name: "类型筛选" }), {
      target: { value: "pitfall" },
    });
    await waitFor(() =>
      expect(api.listExperiences).toHaveBeenLastCalledWith(
        "token",
        expect.objectContaining({ kind: "pitfall" }),
      ),
    );
  });

  it("经验库默认只看当前项目，可切回全部工作区", async () => {
    api.listExperiences.mockResolvedValue({
      items: [],
      total: 0,
      workspace_options: [
        { id: "default", name: "全局记忆", path: "" },
        { id: "ws-beta", name: "beta 后端", path: "D:/projects/beta" },
      ],
    });
    render(<ExperienceListView />);
    const select = await screen.findByRole("combobox", { name: "工作区筛选" });
    await waitFor(() => expect(select).toHaveValue("__current__"));
    await waitFor(() =>
      expect(api.listExperiences).toHaveBeenLastCalledWith(
        "token",
        expect.objectContaining({ workspacePath: "D:/projects/alpha", workspace: undefined }),
      ),
    );
    expect(screen.getByRole("option", { name: "alpha" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "beta 后端" })).toBeInTheDocument();

    fireEvent.change(select, { target: { value: "" } });
    await waitFor(() =>
      expect(api.listExperiences).toHaveBeenLastCalledWith(
        "token",
        expect.objectContaining({ workspace: undefined, workspacePath: undefined }),
      ),
    );
  });
});
