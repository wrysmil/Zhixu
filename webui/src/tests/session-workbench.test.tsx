// SessionWorkbench 是会话页的常驻信息面板（概览/环境/技能与 MCP/产出/来源）。
// 工具坞（文件/终端/浏览器）不嵌在这里：宿主 ThreadShell 在头部按钮打开时用覆盖层
// 盖住本列，覆盖层行为由 thread-shell.test.tsx 覆盖。
// 覆盖：面板常驻无收起入口、内部不含 dock、产出条目点击上抛预览回调。
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SessionWorkbench } from "@/components/workbench/SessionWorkbench";
import type { SessionWorkbenchModel } from "@/components/workbench/session-workbench-model";

const emptyModel: SessionWorkbenchModel = {
  updatedAtMs: null,
  summary: "",
  diffFiles: 0,
  diffAdded: 0,
  diffDeleted: 0,
  projectName: "demo",
  projectPath: "/tmp/project",
  accessMode: "full",
  mcpUsed: [],
  cliUsed: [],
  outputs: [],
  sources: [],
};

describe("SessionWorkbench", () => {
  it("stays pinned: no collapse affordance and no tools dock inside", () => {
    render(<SessionWorkbench model={emptyModel} />);
    expect(screen.getByTestId("session-workbench")).toBeInTheDocument();
    expect(screen.queryByTestId("start-dock")).toBeNull();
    expect(screen.queryByRole("button", { name: /collapse/i })).toBeNull();
  });

  it("routes an output click to the host file preview callback", () => {
    const onOpenFilePreview = vi.fn();
    render(
      <SessionWorkbench
        model={{
          ...emptyModel,
          outputs: [{
            path: "src/a.ts",
            display: "src/a.ts",
            added: 3,
            deleted: 1,
            status: "done",
          }],
        }}
        onOpenFilePreview={onOpenFilePreview}
      />,
    );
    // 产出 Section 默认折叠：先展开再点条目。
    fireEvent.click(screen.getByRole("button", { name: /^Outputs/ }));
    fireEvent.click(screen.getByRole("button", { name: /src\/a\.ts/ }));
    expect(onOpenFilePreview).toHaveBeenCalledWith("src/a.ts");
  });
});
