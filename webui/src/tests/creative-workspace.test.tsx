import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import CreativeWorkspace, {
  type CreativeWorkspaceProps,
} from "@/components/creative/CreativeWorkspace";
import type { WebUIMutationTransport } from "@/lib/api";
import { preloadMarkdownText } from "@/components/MarkdownText";

// A3.1 起演示数据只能经 preview 入口进入正式工作区（A-T11），真实挂载点不再有任何
// demo 分支，所以本套用例改挂预览入口：演示列表、本地筛选与内存写路径仍被完整覆盖。
function renderPreview() {
  const props: CreativeWorkspaceProps = {
    getToken: () => "",
    transport: {
      requestMutation: async () => {
        throw new Error("preview must not use the injected transport");
      },
    } as unknown as WebUIMutationTransport,
    workspacePath: "/preview-workspace",
    preview: true,
  };
  return render(<CreativeWorkspace {...props} />);
}

describe("CreativeWorkspace (preview entry)", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
    localStorage.clear();
  });

  it("renders the demo articles list", async () => {
    renderPreview();
    expect(
      await screen.findByText("从零构建 Agent 的记忆系统"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("沿着一次调用，读懂 Agent Loop"),
    ).toBeInTheDocument();
  });

  it("rejects blank title on new article", async () => {
    renderPreview();
    const btn = screen.getByRole("button", { name: /新建/ });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(screen.getByText("请输入文章主题")).toBeInTheDocument(),
    );
  });

  it("creates a new article and enters editor", async () => {
    renderPreview();
    const textarea = screen.getByPlaceholderText(/想讲清楚什么问题/) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "我的新文章" } });
    fireEvent.click(screen.getByRole("button", { name: /新建/ }));
    await waitFor(() =>
      expect(screen.getByTestId("article-editor")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("article-editor-title").textContent).toBe("我的新文章");
    expect(screen.getByLabelText("正文编辑")).toBeInTheDocument();
  });

  it("filters articles by status", async () => {
    renderPreview();
    expect(
      screen.getByText("从零构建 Agent 的记忆系统"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("status-filter-idea"));
    await waitFor(() =>
      expect(
        screen.queryByText("从零构建 Agent 的记忆系统"),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.getByText("沿着一次调用，读懂 Agent Loop"),
    ).toBeInTheDocument();
  });

  it("searches and shows empty result", async () => {
    renderPreview();
    const search = screen.getByLabelText(/搜索标题或摘要/);
    fireEvent.change(search, { target: { value: "不存在的文章" } });
    await waitFor(() =>
      expect(screen.getByTestId("article-list-empty")).toBeInTheDocument(),
    );
    expect(screen.getByText("没有符合条件的文章")).toBeInTheDocument();
  });

  it("keeps the saved body when navigating back to list and reopening", async () => {
    renderPreview();
    fireEvent.click(screen.getByText("从零构建 Agent 的记忆系统"));
    await waitFor(() => expect(screen.getByLabelText("正文编辑")).toBeInTheDocument());
    const editor = screen.getByLabelText("正文编辑") as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "draft edit" } });
    // 演示库的写路径就是 article.save 动作；离开前先落一次盘。
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    await waitFor(() =>
      expect(screen.getByTestId("save-phase-label").textContent).toContain("已保存"),
    );

    fireEvent.click(screen.getByLabelText("Back"));
    await waitFor(() =>
      expect(screen.getByText("从零构建 Agent 的记忆系统")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("从零构建 Agent 的记忆系统"));
    await waitFor(() => expect(screen.getByLabelText("正文编辑")).toBeInTheDocument());
    expect(screen.getByLabelText("正文编辑")).toHaveValue("draft edit");
  });
});
