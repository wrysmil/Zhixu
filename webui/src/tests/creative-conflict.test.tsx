import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import CreativeWorkspace, {
  type CreativeWorkspaceProps,
} from "@/components/creative/CreativeWorkspace";
import { ConflictDialog } from "@/components/creative/ConflictDialog";
import type { WebUIMutationTransport } from "@/lib/api";
import { DRAFT_STORAGE_KEY } from "@/lib/writing-draft-cache";
import type { Article } from "@/components/creative/types";
import { preloadMarkdownText } from "@/components/MarkdownText";

const LOCAL_BODY = "本地还没保存的正文";

function article(overrides: Partial<Article> = {}): Article {
  return {
    id: "srv-1",
    title: "服务器上的文章",
    writingType: "",
    status: "draft",
    summary: "",
    brief: {},
    body: "服务器上的最新正文",
    outline: [],
    materials: [],
    tags: [],
    revision: 5,
    createdAt: "2026-01-01T00:00:00+00:00",
    updatedAt: "2026-01-05T00:00:00+00:00",
    ...overrides,
  };
}

describe("ConflictDialog (A-08 three resolutions)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("shows the server version and the local draft side by side", () => {
    render(
      <ConflictDialog
        conflict={{ currentRevision: 5, articleId: "srv-1" }}
        latest={article()}
        localBody={LOCAL_BODY}
        onReloadLatest={vi.fn()}
        onSaveCopy={vi.fn()}
        onDiscardLocal={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByTestId("conflict-remote").textContent).toBe("服务器上的最新正文");
    expect(screen.getByTestId("conflict-local").textContent).toBe("本地还没保存的正文");
    expect(screen.getByTestId("conflict-dialog")).toBeInTheDocument();
  });

  it("offers the three resolutions and never a forced overwrite", async () => {
    const onReloadLatest = vi.fn();
    const onSaveCopy = vi.fn();
    const onDiscardLocal = vi.fn();
    render(
      <ConflictDialog
        conflict={{ currentRevision: 5, articleId: "srv-1" }}
        latest={article()}
        localBody={LOCAL_BODY}
        onReloadLatest={onReloadLatest}
        onSaveCopy={onSaveCopy}
        onDiscardLocal={onDiscardLocal}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /overwrite|replace server|强制|覆盖/i })).toBeNull();

    fireEvent.click(screen.getByTestId("conflict-reload"));
    expect(onReloadLatest).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("conflict-save-copy"));
    await waitFor(() => expect(onSaveCopy).toHaveBeenCalledTimes(1));

    // 放弃本地修改需要二次确认。
    fireEvent.click(screen.getByTestId("conflict-discard"));
    expect(onDiscardLocal).not.toHaveBeenCalled();
    expect(screen.getByTestId("conflict-discard-confirm")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("conflict-discard-confirm"));
    await waitFor(() => expect(onDiscardLocal).toHaveBeenCalledTimes(1));
  });

  it("reports a failed resolution instead of closing silently", async () => {
    render(
      <ConflictDialog
        conflict={{ currentRevision: 5, articleId: "srv-1" }}
        latest={article()}
        localBody={LOCAL_BODY}
        onReloadLatest={vi.fn()}
        onSaveCopy={vi.fn(async () => {
          throw new Error("article snapshot is too large");
        })}
        onDiscardLocal={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTestId("conflict-save-copy"));
    await waitFor(() => expect(screen.getByTestId("conflict-error")).toBeInTheDocument());
    expect(screen.getByTestId("conflict-error").textContent).toContain("article snapshot is too large");
    expect(screen.getByTestId("conflict-dialog")).toBeInTheDocument();
  });

  it("treats a deleted article as save-a-copy only, without crashing", () => {
    const onReloadLatest = vi.fn();
    const onDiscardLocal = vi.fn();
    render(
      <ConflictDialog
        conflict={{ currentRevision: -1, articleId: "srv-1" }}
        latest={null}
        localBody={LOCAL_BODY}
        onReloadLatest={onReloadLatest}
        onSaveCopy={vi.fn()}
        onDiscardLocal={onDiscardLocal}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByTestId("conflict-deleted")).toBeInTheDocument();
    expect(screen.getByTestId("conflict-save-copy")).toBeInTheDocument();
    expect(screen.queryByTestId("conflict-reload")).toBeNull();
    expect(screen.queryByTestId("conflict-discard")).toBeNull();

    // 本地稿仍然看得见，服务端那份已经不存在所以不显示远端正文。
    expect(screen.getByTestId("conflict-local").textContent).toBe("本地还没保存的正文");
    expect(onReloadLatest).not.toHaveBeenCalled();
    expect(onDiscardLocal).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 容器接线：冲突由 article.save 的 409 触发，三条分支各自走 acknowledgeConflict。
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

function wireArticle(overrides: Record<string, unknown> = {}) {
  return {
    id: "srv-1",
    title: "服务器上的文章",
    writing_type: "",
    template_id: "",
    status: "draft",
    summary: "",
    brief: {},
    body: "服务器上的最新正文",
    outline: [],
    materials: [],
    tags: [],
    revision: 3,
    created_at: "2026-01-01T00:00:00+00:00",
    updated_at: "2026-01-02T00:00:00+00:00",
    ...overrides,
  };
}

const CONFLICT_MESSAGE = "article_conflict current_revision=5 article_id=srv-1";

function renderWorkspace(transport: WebUIMutationTransport) {
  const props: CreativeWorkspaceProps = {
    getToken: () => "token",
    transport,
    workspacePath: "/conflict-workspace",
  };
  return { props, ...render(<CreativeWorkspace {...props} />) };
}

function calls(requestMutation: ReturnType<typeof vi.fn>, action: string) {
  return requestMutation.mock.calls.filter(
    (entry: unknown[]) => entry[0] === action,
  ) as unknown as Array<[string, Record<string, unknown>]>;
}

function conflictTransport(overrides: Record<string, unknown> = {}) {
  const requestMutation = vi.fn(async (action: string, payload: Record<string, unknown>) => {
    if (action === "article.save") {
      const base = Number(payload.base_revision);
      if (base === 3) {
        throw Object.assign(new Error(overrides.deleted ? "article deleted" : CONFLICT_MESSAGE), {
          status: 409,
        });
      }
      return {
        article_id: String(payload.article_id),
        revision: base + 1,
        updated_at: "2026-02-02T00:00:00+00:00",
        digest: "digest",
        no_change: false,
      };
    }
    if (action === "article.create") {
      return {
        article: wireArticle({
          id: "srv-copy",
          title: String(payload.title),
          revision: 1,
          body: "",
          outline: payload.outline ?? [],
          materials: payload.materials ?? [],
        }),
      };
    }
    throw new Error(`unexpected action ${action}`);
  });
  return { requestMutation, transport: { requestMutation } as unknown as WebUIMutationTransport };
}

async function openEditorWithLocalEdit() {
  const setup = conflictTransport();
  let getCalls = 0;
  const fetchMock = vi.fn(async (input: string) => {
    const url = String(input);
    if (url.includes("/articles/get")) {
      getCalls += 1;
      // 打开时是版本 3；冲突后再读就是服务器上的版本 5。
      return jsonResponse({ article: wireArticle(getCalls > 1 ? { revision: 5 } : {}) });
    }
    return jsonResponse({
      articles: [
        {
          id: "srv-1",
          title: "服务器上的文章",
          summary: "",
          status: "draft",
          writing_type: "",
          tags: [],
          revision: 3,
          created_at: "2026-01-01T00:00:00+00:00",
          updated_at: "2026-01-02T00:00:00+00:00",
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  renderWorkspace(setup.transport);
  fireEvent.click(await screen.findByTestId("open-article-srv-1"));
  await waitFor(() => expect(screen.getByTestId("article-body-input")).toBeInTheDocument());
  fireEvent.change(screen.getByTestId("article-body-input"), { target: { value: "本地写的正文" } });
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));
  await waitFor(() => expect(screen.getByTestId("conflict-dialog")).toBeInTheDocument());
  return { ...setup, fetchMock };
}

describe("article conflict handling wired into the editor (A3.5)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    await preloadMarkdownText();
    localStorage.clear();
    fetchMock = vi.fn(async () => jsonResponse({ articles: [], total: 0, limit: 50, offset: 0 }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows both versions once article.save returns 409", async () => {
    const { fetchMock: get } = await openEditorWithLocalEdit();
    expect(screen.getByTestId("conflict-local").textContent).toBe("本地写的正文");
    await waitFor(() =>
      expect(screen.getByTestId("conflict-remote").textContent).toBe("服务器上的最新正文"),
    );
    expect(get.mock.calls.filter((entry: unknown[]) => String(entry[0]).includes("/articles/get")))
      .toHaveLength(2);
  });

  it("stops autosaving until the conflict is resolved", async () => {
    const { requestMutation } = await openEditorWithLocalEdit();
    const savedSoFar = calls(requestMutation, "article.save").length;

    fireEvent.change(screen.getByTestId("article-body-input"), { target: { value: "冲突期间继续写" } });
    await new Promise((resolve) => {
      setTimeout(resolve, 1700);
    });

    expect(calls(requestMutation, "article.save")).toHaveLength(savedSoFar);
    expect(screen.getByTestId("save-phase-label").textContent).toContain("版本冲突");
    expect(screen.getByTestId("conflict-dialog")).toBeInTheDocument();
  });

  it("keeps the local draft as a new article and leaves the server row alone", async () => {
    const { requestMutation } = await openEditorWithLocalEdit();

    fireEvent.click(screen.getByTestId("conflict-save-copy"));

    await waitFor(() => expect(calls(requestMutation, "article.create").length).toBe(1));
    const create = calls(requestMutation, "article.create")[0][1];
    expect(String(create.title)).toContain("服务器上的文章");
    expect(String(create.title)).toContain("副本");
    expect(Array.isArray(create.outline)).toBe(true);
    expect(Array.isArray(create.materials)).toBe(true);
    expect(String(create.client_request_id)).toMatch(/^[A-Za-z0-9._:-]{1,128}$/);

    // 新建的文章紧接着按本地草稿落一次盘，正文不会留在半保存状态。
    await waitFor(() =>
      expect(calls(requestMutation, "article.save").some((entry) => entry[1].article_id === "srv-copy")),
    );
    const copySave = calls(requestMutation, "article.save").find(
      (entry) => entry[1].article_id === "srv-copy",
    );
    expect((copySave?.[1].snapshot as Record<string, unknown>).body).toBe("本地写的正文");
    expect(copySave?.[1].base_revision).toBe(1);

    await waitFor(() =>
      expect(screen.getByTestId("article-editor-title").textContent).toContain("副本"),
    );
    expect(screen.queryByTestId("conflict-dialog")).toBeNull();
    expect(screen.getByTestId("save-phase-label").textContent).toContain("已保存");

    // 服务端那一版从未被本地稿覆盖。
    const savesToOriginal = calls(requestMutation, "article.save").filter(
      (entry) => entry[1].article_id === "srv-1",
    );
    expect(savesToOriginal).toHaveLength(1);
    expect(savesToOriginal[0][1].base_revision).toBe(3);
  });

  it("merges by hand from the latest server version and saves on top of it", async () => {
    const { requestMutation } = await openEditorWithLocalEdit();

    fireEvent.click(screen.getByTestId("conflict-reload"));

    await waitFor(() =>
      expect((screen.getByTestId("article-body-input") as HTMLTextAreaElement).value).toBe(
        "服务器上的最新正文",
      ),
    );
    expect(screen.queryByTestId("conflict-dialog")).toBeNull();

    fireEvent.change(screen.getByTestId("article-body-input"), {
      target: { value: "服务器上的最新正文 + 我的补充" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      const latest = calls(requestMutation, "article.save").at(-1);
      expect(latest?.[1].base_revision).toBe(5);
    });
    const snapshot = calls(requestMutation, "article.save").at(-1)?.[1].snapshot as Record<string, unknown>;
    expect(snapshot.body).toBe("服务器上的最新正文 + 我的补充");
  });

  it("reloads the server copy only after a second confirmation when discarding", async () => {
    const { requestMutation } = await openEditorWithLocalEdit();
    const savedSoFar = calls(requestMutation, "article.save").length;

    fireEvent.click(screen.getByTestId("conflict-discard"));
    expect(screen.getByTestId("conflict-discard-confirm")).toBeInTheDocument();
    expect(calls(requestMutation, "article.save")).toHaveLength(savedSoFar);

    // 「继续编辑」退回确认步骤，什么都还没发生，对话框仍开着。
    fireEvent.click(screen.getByTestId("conflict-discard-back"));
    expect(screen.queryByTestId("conflict-discard-confirm")).toBeNull();
    expect(screen.getByTestId("conflict-dialog")).toBeInTheDocument();
    expect(calls(requestMutation, "article.save")).toHaveLength(savedSoFar);

    fireEvent.click(screen.getByTestId("conflict-discard"));
    fireEvent.click(screen.getByTestId("conflict-discard-confirm"));
    await waitFor(() =>
      expect((screen.getByTestId("article-body-input") as HTMLTextAreaElement).value).toBe(
        "服务器上的最新正文",
      ),
    );
    expect(screen.queryByTestId("conflict-dialog")).toBeNull();
    expect(localStorage.getItem(DRAFT_STORAGE_KEY) ?? "").not.toContain("srv-1");
  });

  it("handles a deleted article by offering a copy instead of crashing", async () => {
    const setup = conflictTransport({ deleted: true });
    let getCalls = 0;
    fetchMock.mockImplementation(async (input: string) => {
      const url = String(input);
      if (url.includes("/articles/get")) {
        getCalls += 1;
        return jsonResponse({ article: wireArticle(getCalls > 1 ? { revision: 5 } : {}) });
      }
      return jsonResponse({
        articles: [
          {
            id: "srv-1",
            title: "服务器上的文章",
            summary: "",
            status: "draft",
            writing_type: "",
            tags: [],
            revision: 3,
            created_at: "2026-01-01T00:00:00+00:00",
            updated_at: "2026-01-02T00:00:00+00:00",
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      });
    });
    renderWorkspace(setup.transport);
    fireEvent.click(await screen.findByTestId("open-article-srv-1"));
    await waitFor(() => expect(screen.getByTestId("article-body-input")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("article-body-input"), { target: { value: "还没保存的稿子" } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => expect(screen.getByTestId("conflict-dialog")).toBeInTheDocument());
    expect(screen.getByTestId("conflict-deleted")).toBeInTheDocument();
    expect(screen.getByTestId("conflict-local").textContent).toBe("还没保存的稿子");
    expect(screen.queryByTestId("conflict-reload")).toBeNull();
    expect(screen.queryByTestId("conflict-discard")).toBeNull();
    // 已删除的文章不需要再去读一次服务端。
    expect(getCalls).toBe(1);

    fireEvent.click(screen.getByTestId("conflict-save-copy"));
    await waitFor(() => expect(calls(setup.requestMutation, "article.create").length).toBe(1));
    await waitFor(() =>
      expect(screen.getByTestId("article-editor-title").textContent).toContain("副本"),
    );
  });
});
