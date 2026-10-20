// dock「文件」标签页的布局契约：预览在左、文件树在右；预览内超长行换行而非横向滚动。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DockFileTab } from "@/components/dock/DockFileTab";
import { HOME_DOCK_SCOPE } from "@/lib/dock-store";

const mocks = vi.hoisted(() => ({
  fetchWorkspaceFiles: vi.fn(),
  fetchWorkspaceSearch: vi.fn(),
  fetchFilePreview: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, ...mocks };
});

const LONG_LINE = "const value = \"" + "x".repeat(400) + "\";";

beforeEach(() => {
  mocks.fetchWorkspaceFiles.mockReset().mockResolvedValue({
    path: "/tmp/project",
    project_path: "/tmp/project",
    entries: [{ name: "README.md", path: "README.md", kind: "file", size: 6 }],
  });
  mocks.fetchWorkspaceSearch.mockReset().mockResolvedValue({
    query: "README",
    results: [{
      name: "README.md",
      path: "README.md",
      display_path: "README.md",
      line: 0,
      column: 0,
      snippet: "README.md",
      match: "README.md",
      hidden: false,
    }],
    truncated: false,
    scanned_files: 1,
  });
  mocks.fetchFilePreview.mockReset().mockResolvedValue({
    path: "/tmp/project/README.md",
    display_path: "README.md",
    project_path: "/tmp/project",
    language: "javascript",
    content: LONG_LINE,
    size: LONG_LINE.length,
    truncated: false,
  });
});

function renderTab() {
  return render(
    <DockFileTab
      scope={HOME_DOCK_SCOPE}
      sessionKey={null}
      getToken={() => "tok"}
    />,
  );
}

describe("DockFileTab two-column layout", () => {
  it("puts the file tree in its own right-hand column", async () => {
    renderTab();
    const treeColumn = await screen.findByTestId("dock-file-tree-column");
    // 右栏固定宽度，左栏（预览）吃掉剩余空间。
    expect(treeColumn.className).toContain("w-[46%]");
    expect(treeColumn.className).toContain("shrink-0");
  });

  it("shows the pick-a-file placeholder until a file is chosen", async () => {
    renderTab();
    expect(await screen.findByTestId("dock-file-preview-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("dock-file-preview")).toBeNull();
  });

  it("wraps long lines in the preview instead of scrolling sideways", async () => {
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: /README\.md/ }));
    const preview = await screen.findByTestId("dock-file-preview");
    // 高亮块是懒加载的，等它挂载后再断言换行样式。
    await waitFor(() => {
      const code = preview.querySelector("code");
      expect(code?.style.whiteSpace).toBe("pre-wrap");
    }, { timeout: 3000 });
  });
});

describe("DockFileTab toolbar", () => {
  it("toggles hidden files through the scoped workspace endpoint", async () => {
    renderTab();
    await screen.findByTestId("dock-file-toolbar");
    fireEvent.click(screen.getByTestId("dock-file-hidden-toggle"));
    await waitFor(() => {
      expect(mocks.fetchWorkspaceFiles).toHaveBeenCalledWith("tok", null, "", true);
    });
  });

  it("searches file names and contents from the toolbar", async () => {
    renderTab();
    await screen.findByTestId("dock-file-toolbar");
    fireEvent.click(screen.getByTestId("dock-file-search-toggle"));
    fireEvent.change(screen.getByTestId("dock-file-search-input"), {
      target: { value: "README" },
    });
    expect(
      await screen.findByTestId("dock-file-search-hit-README.md", {}, { timeout: 2000 }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(mocks.fetchWorkspaceSearch).toHaveBeenCalledWith("tok", "README", "", false);
    });
  });
});
