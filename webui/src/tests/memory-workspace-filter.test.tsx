import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { MemoryEditDialog } from "@/components/settings/memory/MemoryEditDialog";
import { MemoryFilters } from "@/components/settings/memory/MemoryPresentation";
import i18n from "@/i18n";
import type { MemoryPayload } from "@/lib/types";

const api = vi.hoisted(() => ({ createMemory: vi.fn(), updateMemory: vi.fn() }));
vi.mock("@/lib/api", () => api);
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ client: {} }) }));

const workspaces = [
  { id: "ws-a", name: "项目A", path: "/tmp/a" },
  { id: "default", name: "全局记忆", path: "" },
];
const memory: MemoryPayload = {
  id: "m-1", content: "旧内容", workspace_id: "ws-a", type: "fact",
  priority: "long_term", source: "manual", importance_score: 0.5, access_count: 0,
  tags: [], subject: "", predicate: "", confidence: 1, decay_rate: 0,
  expires_at: null, last_accessed_at: null, superseded_by: null, source_episode_id: null,
  scope: "global", created_at: "2026-10-27T09:00:00", updated_at: "2026-10-27T09:00:00",
  metadata: {},
};

beforeEach(async () => {
  vi.resetAllMocks();
  api.createMemory.mockResolvedValue({ memory });
  api.updateMemory.mockResolvedValue({ memory });
  await i18n.changeLanguage("zh-CN");
});
afterEach(cleanup);

it("工作区筛选包含全局记忆项", () => {
  render(<MemoryFilters
    query="" onQuery={vi.fn()} origin="" onOrigin={vi.fn()}
    workspace="" onWorkspace={vi.fn()} onClear={vi.fn()} active={false}
    result={{ items: [], total: 0, source_counts: { all: 0, ai: 0, manual: 0, unknown: 0 },
      workspace_options: [workspaces[1]], scope: "current_store" }} loading={false}
  />);

  expect(screen.getByRole("option", { name: "全局记忆" })).toHaveValue("default");
  expect(screen.getByRole("option", { name: "全部工作区" })).toHaveValue("");
  expect(screen.queryByRole("option", { name: "默认工作区" })).not.toBeInTheDocument();
});

it("编辑态可以选择归属工作区并提交", async () => {
  const user = userEvent.setup();
  render(<MemoryEditDialog memory={memory} workspaces={workspaces}
    initialWorkspace="default" onClose={vi.fn()} onSaved={vi.fn()} />);

  expect(screen.getByLabelText("工作区")).toHaveValue("ws-a");
  expect(screen.getByRole("option", { name: "全局记忆" })).toHaveValue("default");
  expect(screen.queryByRole("option", { name: "默认工作区" })).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("工作区"), "default");
  await user.click(screen.getByRole("button", { name: /保存|Save/ }));

  expect(api.updateMemory).toHaveBeenCalledWith(
    expect.anything(), "m-1", expect.objectContaining({ workspace: "default" }),
  );
});

it.each(["ws-a", "default", undefined])("编辑未改归属时仍提交当前工作区 %s", async (workspaceId) => {
  const user = userEvent.setup();
  render(<MemoryEditDialog memory={{ ...memory, workspace_id: workspaceId }}
    workspaces={workspaces} initialWorkspace="ws-a" onClose={vi.fn()} onSaved={vi.fn()} />);

  expect(screen.getByLabelText("工作区")).toHaveValue(workspaceId ?? "default");
  await user.click(screen.getByRole("button", { name: /保存|Save/ }));
  expect(api.updateMemory).toHaveBeenCalledWith(
    expect.anything(), "m-1", expect.objectContaining({ workspace: workspaceId ?? "default" }),
  );
});

it.each([undefined, "", "default", "ws-a"])("新增记忆按选择提交归属 %s", async (initialWorkspace) => {
  const user = userEvent.setup();
  render(<MemoryEditDialog memory={null} workspaces={workspaces}
    initialWorkspace={initialWorkspace} onClose={vi.fn()} onSaved={vi.fn()} />);

  expect(screen.getByLabelText("工作区")).toHaveValue(initialWorkspace || "default");
  await user.type(screen.getAllByRole("textbox")[0], "新增内容");
  await user.click(screen.getByRole("button", { name: /保存|Save/ }));
  expect(api.createMemory).toHaveBeenCalledWith(
    expect.anything(), expect.objectContaining({ workspace: initialWorkspace || "default" }),
  );
});
