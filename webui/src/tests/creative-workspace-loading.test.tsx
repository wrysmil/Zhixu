import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import CreativeWorkspace, {
  type CreativeWorkspaceProps,
} from "@/components/creative/CreativeWorkspace";
import type { WebUIMutationTransport } from "@/lib/api";
import { preloadMarkdownText } from "@/components/MarkdownText";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
    json: async () => body,
    headers: { get: () => "application/json" },
  } as unknown as Response;
}

function listBody(articles: unknown[] = [], total = articles.length) {
  return { articles, total, limit: 50, offset: 0 };
}

function wireArticle(overrides: Record<string, unknown> = {}) {
  return {
    id: "srv-1",
    title: "真实文章",
    writing_type: "",
    template_id: "",
    status: "draft",
    summary: "来自服务端",
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
    title: "真实文章",
    summary: "来自服务端",
    status: "draft",
    writing_type: "",
    tags: [],
    revision: 3,
    created_at: "2026-01-01T00:00:00+00:00",
    updated_at: "2026-01-02T00:00:00+00:00",
    ...overrides,
  };
}

function renderWorkspace(overrides: Partial<CreativeWorkspaceProps> = {}) {
  const transport: WebUIMutationTransport = { requestMutation: vi.fn() };
  const props: CreativeWorkspaceProps = {
    getToken: () => "token",
    transport,
    workspacePath: "/workspace",
    ...overrides,
  };
  return { props, transport, ...render(<CreativeWorkspace {...props} />) };
}

/** `mutation()` 还会传第三个参数（超时），所以只按 action 取 payload 来断言。 */
function mutationCall(
  spy: ReturnType<typeof vi.fn>,
  action: string,
): Record<string, unknown> | null {
  const call = spy.mock.calls.find((entry: unknown[]) => entry[0] === action);
  return call ? (call[1] as Record<string, unknown>) : null;
}

describe("CreativeWorkspace loading (A3.1)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    await preloadMarkdownText();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows four distinct list states", async () => {
    // loading: the request never settles before the first assertion.
    let resolveFirst: ((value: Response) => void) | null = null;
    fetchMock.mockImplementationOnce(
      () => new Promise<Response>((resolve) => { resolveFirst = resolve; }),
    );
    const view = renderWorkspace();
    expect(await screen.findByTestId("article-list-loading")).toBeInTheDocument();
    resolveFirst?.(jsonResponse(listBody([])));
    view.rerender(<CreativeWorkspace {...view.props} />);
    expect(await screen.findByTestId("article-list-empty")).toBeInTheDocument();

    // error: a non-2xx response surfaces the failure state.
    fetchMock.mockImplementationOnce(async () => jsonResponse({}, 500));
    fireEvent.click(screen.getByTestId("status-filter-idea"));
    expect(await screen.findByTestId("article-list-error")).toBeInTheDocument();
    expect(screen.getByText("Could not load articles")).toBeInTheDocument();

    // ready: rows come from the server payload.
    fetchMock.mockImplementation(async () =>
      jsonResponse(listBody([summaryRow(), summaryRow({ id: "srv-2", title: "第二篇" })], 2)),
    );
    fireEvent.click(screen.getByTestId("status-filter-all"));
    expect(await screen.findByText("真实文章")).toBeInTheDocument();
    expect(screen.getByText("第二篇")).toBeInTheDocument();
    expect(screen.queryByTestId("article-list-empty")).not.toBeInTheDocument();
  });

  it("never auto-inserts demo articles into the real workspace", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(listBody([])));
    renderWorkspace();
    await waitFor(() =>
      expect(screen.getByTestId("article-list-empty")).toBeInTheDocument(),
    );
    expect(screen.queryByText(/为什么需要记忆/)).not.toBeInTheDocument();
    expect(screen.queryByText("从零构建 Agent 的记忆系统")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "恢复示例" })).not.toBeInTheDocument();
  });

  it("re-requests the server when the status filter changes", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(listBody([])));
    renderWorkspace();
    await waitFor(() =>
      expect(screen.getByTestId("article-list-empty")).toBeInTheDocument(),
    );
    const firstCalls = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByTestId("status-filter-review"));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(firstCalls));
    const url = String(fetchMock.mock.calls.at(-1)?.[0]);
    expect(url).toContain("/api/writing/articles");
    expect(url).toContain("status=review");
  });

  it("sends the search term to the server instead of filtering locally", async () => {
    fetchMock.mockImplementation(async (input: string) => {
      if (String(input).includes("q=nope")) return jsonResponse(listBody([], 0));
      return jsonResponse(listBody([summaryRow()]));
    });
    renderWorkspace();
    await waitFor(() => expect(screen.getByText("真实文章")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Search titles or summaries"), {
      target: { value: "nope" },
    });
    await waitFor(() =>
      expect(screen.getByTestId("article-list-empty")).toBeInTheDocument(),
    );
    expect(String(fetchMock.mock.calls.at(-1)?.[0])).toContain("q=nope");
  });

  it("loads demo data without any request in preview mode", async () => {
    renderWorkspace({ preview: true });
    expect(await screen.findByText("从零构建 Agent 的记忆系统")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("creates through article.create with the template outline and opens the editor", async () => {
    const created = wireArticle({ id: "srv-new", title: "我的新文章", revision: 1 });
    const requestMutation = vi.fn(async () => ({ article: created }));
    fetchMock.mockImplementation(async () => jsonResponse(listBody([summaryRow()])));
    renderWorkspace({
      transport: { requestMutation } as unknown as WebUIMutationTransport,
    });
    await waitFor(() => expect(screen.getByText("真实文章")).toBeInTheDocument());

    fireEvent.change(screen.getByRole("textbox", { name: "Article title" }), {
      target: { value: "我的新文章" },
    });
    fireEvent.click(screen.getByTestId("template-tech-explainer"));
    fireEvent.click(screen.getByRole("button", { name: "Create draft" }));

    await waitFor(() => expect(requestMutation).toHaveBeenCalled());
    const [action, payload] = requestMutation.mock.calls[0] as [string, Record<string, unknown>];
    expect(action).toBe("article.create");
    expect(payload.title).toBe("我的新文章");
    expect(payload.template_id).toBe("tech-explainer");
    expect(String(payload.client_request_id)).toMatch(/^[A-Za-z0-9._:-]{1,128}$/);
    expect(Array.isArray(payload.outline)).toBe(true);
    expect((payload.outline as unknown[]).length).toBeGreaterThan(0);

    // 服务端返回的文章接管编辑器（正文 DOM id 由 A3.3 固定）。
    await waitFor(() =>
      expect(screen.getByTestId("article-editor")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("article-editor-title").textContent).toBe("我的新文章");
  });

  it("keeps the typed title when article.create fails", async () => {
    const requestMutation = vi.fn(async () => {
      throw Object.assign(new Error("article title is too long"), { status: 400 });
    });
    fetchMock.mockImplementation(async () => jsonResponse(listBody([])));
    renderWorkspace({
      transport: { requestMutation } as unknown as WebUIMutationTransport,
    });
    await waitFor(() =>
      expect(screen.getByTestId("article-list-empty")).toBeInTheDocument(),
    );

    const titleInput = screen.getByRole("textbox", { name: "Article title" });
    fireEvent.change(titleInput, { target: { value: "失败也要留着" } });
    fireEvent.click(screen.getByRole("button", { name: "Create draft" }));

    await waitFor(() =>
      expect(screen.getByTestId("create-error")).toBeInTheDocument(),
    );
    expect((screen.getByRole("textbox", { name: "Article title" }) as HTMLInputElement).value)
      .toBe("失败也要留着");
  });

  it("surfaces a missing article instead of falling back to demo data", async () => {
    fetchMock.mockImplementation(async (input: string) => {
      if (String(input).includes("/articles/get")) return jsonResponse({}, 404);
      return jsonResponse(listBody([summaryRow()]));
    });
    renderWorkspace();
    fireEvent.click(await screen.findByTestId("open-article-srv-1"));
    expect(await screen.findByTestId("article-editor-error")).toBeInTheDocument();
    expect(screen.getByText("文章已不存在")).toBeInTheDocument();
    expect(screen.queryByText("从零构建 Agent 的记忆系统")).not.toBeInTheDocument();
  });

  it("surfaces the deleted-article message for a late 409", async () => {
    fetchMock.mockImplementation(async (input: string) => {
      if (String(input).includes("/articles/get")) {
        return jsonResponse({ error: "article deleted" }, 409);
      }
      return jsonResponse(listBody([summaryRow()]));
    });
    renderWorkspace();
    fireEvent.click(await screen.findByTestId("open-article-srv-1"));
    expect(await screen.findByText("文章已被删除")).toBeInTheDocument();
  });

  it("deletes and duplicate through the mutation actions and refreshes the list", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(listBody([summaryRow(), summaryRow({ id: "srv-2", title: "第二篇" })], 2)),
    );
    const requestMutation = vi.fn(async (action: string) => {
      if (action === "article.delete") return { deleted_article_id: "srv-1", revision: 4 };
      return { article: wireArticle({ id: "srv-copy", title: "真实文章（副本）", revision: 1 }) };
    });
    renderWorkspace({
      transport: { requestMutation } as unknown as WebUIMutationTransport,
    });
    await waitFor(() => expect(screen.getByText("真实文章")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("duplicate-article-srv-1"));
    await waitFor(() => expect(mutationCall(requestMutation, "article.duplicate")).not.toBeNull());
    const duplicate = mutationCall(requestMutation, "article.duplicate");
    expect(duplicate?.article_id).toBe("srv-1");
    expect(duplicate?.base_revision).toBe(3);
    expect(await screen.findByText("真实文章（副本）")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("delete-article-srv-1"));
    fireEvent.click(await screen.findByTestId("confirm-delete-article"));
    await waitFor(() => expect(mutationCall(requestMutation, "article.delete")).not.toBeNull());
    const removal = mutationCall(requestMutation, "article.delete");
    expect(removal?.article_id).toBe("srv-1");
    expect(removal?.base_revision).toBe(3);
  });
});
