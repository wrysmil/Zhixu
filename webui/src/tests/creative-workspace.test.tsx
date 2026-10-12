import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import CreativeWorkspace from "@/components/creative/CreativeWorkspace";
import { preloadMarkdownText } from "@/components/MarkdownText";

describe("CreativeWorkspace", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
  });

  it("renders the demo articles list", async () => {
    render(<CreativeWorkspace />);
    expect(
      await screen.findByText("从零构建 Agent 的记忆系统"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("沿着一次调用，读懂 Agent Loop"),
    ).toBeInTheDocument();
  });

  it("rejects blank title on new article", async () => {
    render(<CreativeWorkspace />);
    const btn = screen.getByRole("button", { name: /新建/ });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(screen.getByText("请输入文章主题")).toBeInTheDocument(),
    );
  });

  it("creates a new article and enters editor", async () => {
    render(<CreativeWorkspace />);
    const textarea = screen.getByPlaceholderText(/想讲清楚什么问题/) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "我的新文章" } });
    fireEvent.click(screen.getByRole("button", { name: /新建/ }));
    await waitFor(() =>
      expect(screen.getByText("我的新文章")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("正文编辑")).toBeInTheDocument();
  });

  it("filters articles by status", async () => {
    render(<CreativeWorkspace />);
    expect(
      screen.getByText("从零构建 Agent 的记忆系统"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Idea" }));
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
    render(<CreativeWorkspace />);
    const search = screen.getByPlaceholderText(/搜索标题/);
    fireEvent.change(search, { target: { value: "不存在的文章" } });
    await waitFor(() =>
      expect(screen.getByText("没有符合条件的文章")).toBeInTheDocument(),
    );
  });

  it("preserves body draft when navigating back to list and reopening", async () => {
    render(<CreativeWorkspace />);
    // open first article
    fireEvent.click(screen.getByText("从零构建 Agent 的记忆系统"));
    await waitFor(() => expect(screen.getByLabelText("正文编辑")).toBeInTheDocument());
    // edit the body
    const editor = screen.getByLabelText("正文编辑") as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "draft edit" } });
    // go back via the back button (ArrowLeft icon in header)
    const backBtn = document.querySelector('[aria-label="Back"]') as HTMLElement;
    if (backBtn) {
      fireEvent.click(backBtn);
    } else {
      const headerBtns = document.querySelectorAll(".flex.h-\\[52px\\] button");
      if (headerBtns.length > 0) fireEvent.click(headerBtns[0]);
    }
    // should be on list view now
    await waitFor(() =>
      expect(screen.getByText("从零构建 Agent 的记忆系统")).toBeInTheDocument(),
    );
    // reopen same article
    fireEvent.click(screen.getByText("从零构建 Agent 的记忆系统"));
    await waitFor(() => expect(screen.getByLabelText("正文编辑")).toBeInTheDocument());
    expect(screen.getByLabelText("正文编辑")).toHaveValue("draft edit");
  });

  it("rejects outdated suggestion after article revision changes", async () => {
    render(<CreativeWorkspace />);
    // open a1 (revision 0, suggestion has baseRevision 0)
    fireEvent.click(screen.getByText("从零构建 Agent 的记忆系统"));
    await waitFor(() => expect(screen.getByLabelText("正文编辑")).toBeInTheDocument());
    // bump revision by editing body
    const editor = screen.getByLabelText("正文编辑") as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "changed to bump revision" } });
    // the suggestion panel is in the right column — find "采用" button
    await waitFor(() => expect(screen.getByText(/补充/)).toBeInTheDocument());
    const acceptBtn = screen.getByRole("button", { name: /采用/ });
    fireEvent.click(acceptBtn);
    await waitFor(() =>
      expect(screen.getByText(/建议已过期/)).toBeInTheDocument(),
    );
  });
});
