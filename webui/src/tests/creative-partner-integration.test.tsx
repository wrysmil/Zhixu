import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import CreativeWorkspace, {
  type CreativeWorkspaceProps,
} from "@/components/creative/CreativeWorkspace";
import { ArticleEditor } from "@/components/creative/ArticleEditor";
import type { WebUIMutationTransport } from "@/lib/api";
import { preloadMarkdownText } from "@/components/MarkdownText";

/**
 * 创作团队侧栏挂载：
 * 1. 编辑器右栏渲染注入的团队会话，开关能收起/展开；
 * 2. 没有注入会话时既不显示右栏，也不显示开关；
 * 3. 真实工作区打开文章时挂上 WritingChatPanel（非预览入口）。
 */

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
    json: async () => body,
    headers: { get: () => "application/json" },
  } as unknown as Response;
}

function listBody(rows: Record<string, unknown>[] = []) {
  return { articles: rows, total: rows.length, limit: 50, offset: 0 };
}

function summaryRow(overrides: Record<string, unknown> = {}) {
  return {
    id: "srv-1",
    title: "文章标题",
    summary: "",
    status: "draft",
    writing_type: "",
    tags: [],
    revision: 3,
    created_at: "2026-01-01T00:00:00+00:00",
    updated_at: "2026-01-02T00:00:00+00:00",
    ...overrides,
  };
}

function wireArticle(overrides: Record<string, unknown> = {}) {
  return {
    id: "srv-1",
    title: "文章标题",
    writing_type: "",
    template_id: "",
    status: "draft",
    summary: "",
    brief: {},
    body: "正文",
    outline: [],
    materials: [],
    tags: [],
    revision: 3,
    created_at: "2026-01-01T00:00:00+00:00",
    updated_at: "2026-01-02T00:00:00+00:00",
    ...overrides,
  };
}

function editorDraft() {
  return {
    title: "文章标题",
    writing_type: "",
    template_id: "",
    status: "draft",
    summary: "",
    brief: {},
    body: "正文",
    outline: [],
    materials: [],
    tags: [],
  };
}

type WritingChatSlot =
  | React.ReactNode
  | ((controls: { onCollapse: () => void }) => React.ReactNode);

function renderEditor(writingChat?: WritingChatSlot) {
  return render(
    <ArticleEditor
      article={wireArticle() as never}
      draft={editorDraft() as never}
      dirty={false}
      saveState={{ phase: "saved", lastError: null } as never}
      setField={() => {}}
      onSaveNow={async () => {}}
      onAcknowledgeConflict={async () => {}}
      onFetchLatest={async () => wireArticle() as never}
      onSaveCopy={async () => {}}
      onBack={() => {}}
      writingChat={writingChat}
    />,
  );
}

describe("创作团队侧栏挂载", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
    localStorage.clear();
  });

  it("右栏渲染注入的会话，开关可收起再展开", () => {
    renderEditor(<div data-testid="fake-chat">团队内容</div>);

    expect(screen.getByTestId("creative-partner-column")).toBeInTheDocument();
    expect(screen.getByTestId("fake-chat")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("partner-toggle"));
    expect(screen.queryByTestId("creative-partner-column")).not.toBeInTheDocument();
    expect(screen.queryByTestId("fake-chat")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("partner-toggle"));
    expect(screen.getByTestId("creative-partner-column")).toBeInTheDocument();
  });

  it("函数式注入时由编辑器提供显式收起入口", () => {
    renderEditor(({ onCollapse }) => (
      <div data-testid="fake-chat">
        <button type="button" onClick={onCollapse}>收起会话</button>
      </div>
    ));

    fireEvent.click(screen.getByRole("button", { name: "收起会话" }));

    expect(screen.queryByTestId("creative-partner-column")).not.toBeInTheDocument();
    expect(screen.queryByTestId("fake-chat")).not.toBeInTheDocument();
  });

  it("没有注入会话时不显示右栏和开关", () => {
    renderEditor();

    expect(screen.queryByTestId("creative-partner-column")).not.toBeInTheDocument();
    expect(screen.queryByTestId("partner-toggle")).not.toBeInTheDocument();
  });

  it("真实工作区打开文章时挂上真团队会话面板（非预览入口）", async () => {
    const fetchMock = vi.fn(async (input: unknown) => {
      const url = String(input);
      if (url.includes("srv-1")) return jsonResponse({ article: wireArticle() });
      return jsonResponse(listBody([summaryRow()]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const props: CreativeWorkspaceProps = {
      getToken: () => "t",
      transport: {
        requestMutation: vi.fn(async () => ({})),
      } as unknown as WebUIMutationTransport,
      workspacePath: "/workspace",
    };

    render(<CreativeWorkspace {...props} />);
    fireEvent.click(await screen.findByText("文章标题"));

    expect(await screen.findByTestId("creative-partner-column")).toBeInTheDocument();
    // 真面板（不是替身节点）：根节点由面板自己打标。
    expect(await screen.findByTestId("writing-chat-panel")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
