import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import CreativeWorkspace, {
  type CreativeWorkspaceProps,
} from "@/components/creative/CreativeWorkspace";
import { ArticleEditor } from "@/components/creative/ArticleEditor";
import type { WebUIMutationTransport } from "@/lib/api";
import { preloadMarkdownText } from "@/components/MarkdownText";

/**
 * WU-13 集成验收：创作搭档侧栏必须真的出现在编辑器里。
 *
 * 此前 `WritingPartnerPanel` 只有单测引用、生产零挂载 —— 功能对用户完全不可见，
 * 尾盘代码审查把它列为独立发现。这里从两个方向锁死：
 * 1. 编辑器右栏渲染注入的面板，并且开关能收起/展开（窄屏默认收起由 matchMedia 决定）；
 * 2. 没有注入面板时保留阶段 A 的只读占位，预览入口不挂真面板。
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

function renderEditor(partnerPanel?: React.ReactNode) {
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
      partnerPanel={partnerPanel}
    />,
  );
}

describe("创作搭档侧栏挂载（WU-13）", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
    localStorage.clear();
  });

  it("右栏渲染注入的面板，开关可收起再展开", () => {
    renderEditor(<div data-testid="fake-partner">搭档内容</div>);

    expect(screen.getByTestId("creative-partner-column")).toBeInTheDocument();
    expect(screen.getByTestId("fake-partner")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("partner-toggle"));
    expect(screen.queryByTestId("creative-partner-column")).not.toBeInTheDocument();
    expect(screen.queryByTestId("fake-partner")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("partner-toggle"));
    expect(screen.getByTestId("creative-partner-column")).toBeInTheDocument();
  });

  it("没有注入面板时保留阶段 A 的只读占位，也不显示开关", () => {
    renderEditor();

    expect(screen.queryByTestId("creative-partner-column")).not.toBeInTheDocument();
    expect(screen.queryByTestId("partner-toggle")).not.toBeInTheDocument();
    expect(screen.getByTestId("suggestions-placeholder")).toBeInTheDocument();
  });

  it("真实工作区打开文章时挂上真面板（非预览入口）", async () => {
    const fetchMock = vi.fn(async (input: unknown) => {
      const url = String(input);
      // 列表与详情都走 /api/writing/...，只能按文章 id 判别，别按 "article" 子串。
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
    expect(document.querySelector("[data-writing-partner]")).not.toBeNull();
    vi.unstubAllGlobals();
  });
});
