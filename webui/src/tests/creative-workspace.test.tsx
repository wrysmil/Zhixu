import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import CreativeWorkspace, {
  type CreativeWorkspaceProps,
} from "@/components/creative/CreativeWorkspace";
import { ApiError, type WebUIMutationTransport } from "@/lib/api";
import {
  DRAFT_STORAGE_KEY,
  writeDraft,
} from "@/lib/writing-draft-cache";
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

// ---------------------------------------------------------------------------
// A3.8 — 删除/复制/晚到请求防护（A-02）
// ---------------------------------------------------------------------------

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

function seedCachedDraft(articleId: string, bodyText: string) {
  writeDraft({
    workspaceKey: "/workspace",
    articleId,
    baseRevision: 3,
    localEditSeq: 1,
    snapshot: {
      title: "草稿标题",
      writing_type: "",
      status: "draft",
      summary: "",
      brief: {},
      body: bodyText,
      outline: [],
      materials: [],
      tags: [],
    },
  });
}

describe("CreativeWorkspace delete + duplicate (A3.8 / A-02)", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
    localStorage.clear();
  });

  function renderWorkspace(transport: WebUIMutationTransport) {
    const props: CreativeWorkspaceProps = {
      getToken: () => "token",
      transport,
      workspacePath: "/workspace",
    };
    return render(<CreativeWorkspace {...props} />);
  }

  it("deletes via article.delete with the row's revision and base_revision", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(listBody([summaryRow()])),
    );
    vi.stubGlobal("fetch", fetchMock);
    const requestMutation = vi.fn<(action: string, payload: Record<string, unknown>) => Promise<unknown>>(async (action) => {
      if (action === "article.delete") return { deleted_article_id: "srv-1", revision: 4 };
      throw new Error(`unexpected action ${action}`);
    });
    renderWorkspace({ requestMutation } as unknown as WebUIMutationTransport);

    await waitFor(() => expect(screen.getByText("文章标题")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("delete-article-srv-1"));
    fireEvent.click(screen.getByTestId("confirm-delete-article"));

    await waitFor(() => {
      const call = requestMutation.mock.calls.find((entry) => entry[0] === "article.delete");
      expect(call).toBeTruthy();
    });
    const [, payload] = requestMutation.mock.calls[0] as [string, Record<string, unknown>];
    expect(payload.article_id).toBe("srv-1");
    expect(payload.base_revision).toBe(3);
    expect(String(payload.client_request_id)).toMatch(/^[A-Za-z0-9._:-]{1,128}$/);
    vi.unstubAllGlobals();
  });

  it("surfaces an extra dirty hint when the article still has a cached recovery draft", async () => {
    seedCachedDraft("srv-1", "本地未保存的正文");
    const fetchMock = vi.fn(async () =>
      jsonResponse(listBody([summaryRow()])),
    );
    vi.stubGlobal("fetch", fetchMock);
    const requestMutation = vi.fn(async (action: string) => {
      if (action === "article.delete") return { deleted_article_id: "srv-1", revision: 4 };
      throw new Error(`unexpected action ${action}`);
    });
    renderWorkspace({ requestMutation } as unknown as WebUIMutationTransport);

    await waitFor(() => expect(screen.getByText("文章标题")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("delete-article-srv-1"));
    expect(screen.getByTestId("delete-dirty-warning")).toBeInTheDocument();
    expect(screen.getByTestId("delete-dirty-warning").textContent).toContain("未保存");
    vi.unstubAllGlobals();
  });

  it("does not show the dirty hint for entries without a local draft", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(listBody([summaryRow()])),
    );
    vi.stubGlobal("fetch", fetchMock);
    const requestMutation = vi.fn();
    renderWorkspace({ requestMutation } as unknown as WebUIMutationTransport);

    await waitFor(() => expect(screen.getByText("文章标题")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("delete-article-srv-1"));
    expect(screen.queryByTestId("delete-dirty-warning")).toBeNull();
    vi.unstubAllGlobals();
  });

  it("duplicate copies the server row into a new id and prepends it to the list", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(listBody([summaryRow()])),
    );
    vi.stubGlobal("fetch", fetchMock);
    const requestMutation = vi.fn<(action: string, payload: Record<string, unknown>) => Promise<unknown>>(async (action) => {
      if (action === "article.duplicate") {
        return { article: wireArticle({ id: "srv-copy", title: "文章标题（副本）", revision: 1 }) };
      }
      throw new Error(`unexpected action ${action}`);
    });
    renderWorkspace({ requestMutation } as unknown as WebUIMutationTransport);

    await waitFor(() => expect(screen.getByText("文章标题")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("duplicate-article-srv-1"));

    await waitFor(() => {
      const call = requestMutation.mock.calls.find((entry) => entry[0] === "article.duplicate");
      expect(call).toBeTruthy();
    });
    const [, payload] = requestMutation.mock.calls[0] as [string, Record<string, unknown>];
    expect(payload.article_id).toBe("srv-1");
    expect(payload.base_revision).toBe(3);
    await waitFor(() => expect(screen.getByText("文章标题（副本）")).toBeInTheDocument());
    vi.unstubAllGlobals();
  });

  it("deleting the article also clears its cached recovery draft", async () => {
    seedCachedDraft("srv-1", "本地未保存的正文");
    let listCalls = 0;
    const fetchMock = vi.fn(async (input: unknown) => {
      const url = String(input);
      if (url.includes("/articles/get")) {
        return jsonResponse({ article: wireArticle() });
      }
      listCalls += 1;
      // 删除触发了一次刷新（reloadKey++），第二次以后返回空。
      return jsonResponse(listCalls > 1 ? listBody([]) : listBody([summaryRow()]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const requestMutation = vi.fn(async (action: string) => {
      if (action === "article.delete") return { deleted_article_id: "srv-1", revision: 4 };
      throw new Error(`unexpected action ${action}`);
    });
    renderWorkspace({ requestMutation } as unknown as WebUIMutationTransport);

    await waitFor(() => expect(screen.getByText("文章标题")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("delete-article-srv-1"));
    expect(screen.getByTestId("delete-dirty-warning")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("confirm-delete-article"));

    await waitFor(() => expect(requestMutation).toHaveBeenCalled());
    const stored = JSON.parse(localStorage.getItem(DRAFT_STORAGE_KEY) ?? "{}");
    const bucket = stored["/workspace"] as Record<string, unknown> | undefined;
    expect(bucket?.["srv-1"]).toBeUndefined();
    vi.unstubAllGlobals();
  });

  it("late-arriving save after delete does not resurrect the article (A-02)", async () => {
    let resolveSave: ((value: unknown) => void) | null = null;
    let listCalls = 0;
    const fetchMock = vi.fn(async (input: unknown) => {
      const url = String(input);
      if (url.includes("/articles/get")) {
        return jsonResponse({ article: wireArticle() });
      }
      // 三次 list fetch 的语义：①挂载 ②force 离开编辑器 ③删除后刷新；
      // 第三次才返回空列表，与「deleting the article also clears its cached
      // recovery draft」用例的「删除触发了一次刷新（reloadKey++）」注释对齐。
      listCalls += 1;
      return jsonResponse(listCalls > 2 ? listBody([]) : listBody([summaryRow()]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const requestMutation = vi.fn<(action: string, payload: Record<string, unknown>) => Promise<unknown>>(async (action) => {
      if (action === "article.save") {
        // 第一条 save 立即被服务端 409 拒绝（语义：服务端在 user 点 Back 之前
        // 已经删除了文章），让 tryLeave 走 blocked 分支并弹出 A3.6 离开守卫。
        // 仍保留 resolveSave 句柄：force 离开编辑器 + 删文章后，让其解锁成
        // 成功响应以验证 abandonedRef 会拦下晚到响应，不复活 activeId。
        const pending = new Promise<void>((resolve) => {
          resolveSave = () => resolve();
        });
        void pending;
        throw new ApiError(409, "article_conflict current_revision=3 article_id=srv-1");
      }
      if (action === "article.delete") return { deleted_article_id: "srv-1", revision: 4 };
      throw new Error(`unexpected action ${action}`);
    });
    renderWorkspace({ requestMutation } as unknown as WebUIMutationTransport);

    await waitFor(() => expect(screen.getByText("文章标题")).toBeInTheDocument());

    // 打开编辑器并触发一次保存（这条 save 会卡在 resolveSave 上不落地）。
    fireEvent.click(screen.getByText("文章标题"));
    await waitFor(() => expect(screen.getByLabelText("正文编辑")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("正文编辑"), { target: { value: "新写的稿子" } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    // 在回车路上模拟服务端收到删除：调用 article.delete，再让 article.save 之后 409。
    // 这次 save 卡在 resolveSave 上导致 tryLeave 走 blocked 分支，编辑器弹出
    // A3.6 离开守卫；测试要显式点「确认离开」才能继续。
    fireEvent.click(screen.getByLabelText("Back"));
    await waitFor(() =>
      expect(screen.getByTestId("leave-guard")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("leave-guard-force"));

    await waitFor(() => expect(screen.getByText("文章标题")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("delete-article-srv-1"));
    fireEvent.click(screen.getByTestId("confirm-delete-article"));
    await waitFor(() => expect(requestMutation.mock.calls.some((entry) => entry[0] === "article.delete")).toBe(true));

    // 把在飞的 save 解锁成「文章已删除」。
    resolveSave?.({ article_id: "srv-1", revision: 5, updated_at: "2026-02-02T00:00:00+00:00", digest: "d", no_change: false });

    // 这时如果 article 没有走 abandonedRef，编辑器仍会被写回去；现在确认 article 不再渲染。
    // 删除会触发 list 刷新（reloadKey++），需要等 fetch 落地后再断言。
    await waitFor(() => expect(screen.queryByText("文章标题")).toBeNull());
    vi.unstubAllGlobals();
  });
});
