import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import CreativeWorkspace, {
  type CreativeWorkspaceProps,
} from "@/components/creative/CreativeWorkspace";
import type { WebUIMutationTransport } from "@/lib/api";
import {
  DRAFT_STORAGE_KEY,
  writeDraft,
  type LocalArticleDraft,
} from "@/lib/writing-draft-cache";
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

function listBody(rows: Record<string, unknown>[] = []) {
  return { articles: rows, total: rows.length, limit: 50, offset: 0 };
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
    body: "服务器上的正文",
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
    title: "服务器上的文章",
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

const WORKSPACE = "/recovery-workspace";

function renderWorkspace(overrides: Partial<CreativeWorkspaceProps> = {}) {
  const transport: WebUIMutationTransport = { requestMutation: vi.fn() };
  const props: CreativeWorkspaceProps = {
    getToken: () => "token",
    transport,
    workspacePath: WORKSPACE,
    ...overrides,
  };
  return { props, transport, ...render(<CreativeWorkspace {...props} />) };
}

function seedDraft(draft: Partial<LocalArticleDraft> & { articleId: string }) {
  writeDraft({
    workspaceKey: WORKSPACE,
    articleId: draft.articleId,
    baseRevision: draft.baseRevision ?? 3,
    localEditSeq: draft.localEditSeq ?? 1,
    snapshot: draft.snapshot ?? {
      title: draft.title ?? "草稿标题",
      writing_type: "",
      status: "draft",
      summary: "",
      brief: {},
      body: "本地未保存的正文",
      outline: [],
      materials: [],
      tags: [],
    },
  });
}

describe("ArticleRecoveryBanner + leave guard (A3.6 / A-07)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    await preloadMarkdownText();
    localStorage.clear();
    fetchMock = vi.fn(async (input: unknown) => {
      const url = String(input);
      if (url.includes("/articles/get")) {
        return jsonResponse({ article: wireArticle() });
      }
      return jsonResponse(listBody([summaryRow()]));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows the recovery banner on reopen when a draft is cached and the server revision matches", async () => {
    seedDraft({ articleId: "srv-1", baseRevision: 3 });

    renderWorkspace();

    fireEvent.click(await screen.findByTestId("open-article-srv-1"));
    await waitFor(() =>
      expect(screen.getByTestId("article-recovery-banner")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("recovery-recover")).toBeInTheDocument();
    expect(screen.getByTestId("recovery-discard")).toBeInTheDocument();
    // 默认以服务端为准，本地稿不会自动覆盖编辑器。
    expect((screen.getByTestId("article-body-input") as HTMLTextAreaElement).value)
      .toBe("服务器上的正文");
    expect(screen.getByTestId("save-phase-label").textContent).toContain("已保存");
  });

  it("recover keeps the draft in the editor and re-uses server revision as the new base", async () => {
    seedDraft({ articleId: "srv-1", baseRevision: 3 });

    const requestMutation = vi.fn(async (action: string, payload: Record<string, unknown>) => {
      if (action === "article.save") {
        return {
          article_id: String(payload.article_id),
          revision: 4,
          updated_at: "2026-02-02T00:00:00+00:00",
          digest: "digest",
          no_change: false,
        };
      }
      throw new Error(`unexpected action ${action}`);
    });
    renderWorkspace({ transport: { requestMutation } as unknown as WebUIMutationTransport });

    fireEvent.click(await screen.findByTestId("open-article-srv-1"));
    fireEvent.click(await screen.findByTestId("recovery-recover"));

    await waitFor(() => {
      const body = screen.getByTestId("article-body-input") as HTMLTextAreaElement;
      expect(body.value).toBe("本地未保存的正文");
    });
    // 没有自动覆盖，banner 消失，但本地稿不再作为「保存成功」证据。
    expect(screen.queryByTestId("article-recovery-banner")).toBeNull();
    expect(screen.getByTestId("save-phase-label").textContent).toContain("有未保存的修改");

    // Apply 触发自动保存；以服务端当前 revision 作为 base。
    await waitFor(() => expect(requestMutation).toHaveBeenCalled(), { timeout: 4000 });
    const [, payload] = requestMutation.mock.calls[0] as [string, Record<string, unknown>];
    expect(payload.article_id).toBe("srv-1");
    expect(payload.base_revision).toBe(3);
    expect((payload.snapshot as Record<string, unknown>).body).toBe("本地未保存的正文");
  });

  it("discard clears the cached draft and keeps the server content", async () => {
    seedDraft({ articleId: "srv-1", baseRevision: 3 });

    renderWorkspace();
    fireEvent.click(await screen.findByTestId("open-article-srv-1"));
    fireEvent.click(await screen.findByTestId("recovery-discard"));

    await waitFor(() => {
      expect(screen.queryByTestId("article-recovery-banner")).toBeNull();
    });
    expect((screen.getByTestId("article-body-input") as HTMLTextAreaElement).value)
      .toBe("服务器上的正文");
    expect(localStorage.getItem(DRAFT_STORAGE_KEY) ?? "").not.toContain("srv-1");
  });

  it("takes the A3.5 conflict flow when the server revision has moved past the cached base", async () => {
    seedDraft({ articleId: "srv-1", baseRevision: 2 });
    fetchMock.mockImplementation(async (input: string) => {
      const url = String(input);
      if (url.includes("/articles/get")) {
        return jsonResponse({ article: wireArticle({ revision: 4 }) });
      }
      return jsonResponse(listBody([summaryRow({ revision: 4 })]));
    });

    renderWorkspace();
    fireEvent.click(await screen.findByTestId("open-article-srv-1"));

    // 服务器侧已经更新：本地稿不能直接刷给用户，必须先弹冲突对话框（A-08）。
    await waitFor(() =>
      expect(screen.getByTestId("conflict-dialog")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("conflict-local").textContent).toBe("本地未保存的正文");
    expect(screen.getByTestId("conflict-remote").textContent).toBe("服务器上的正文");
  });

  it("leave guard: dirty editor triggers a save attempt before navigating", async () => {
    const requestMutation = vi.fn(async (action: string, payload: Record<string, unknown>) => {
      if (action === "article.save") {
        return {
          article_id: String(payload.article_id),
          revision: 4,
          updated_at: "2026-02-02T00:00:00+00:00",
          digest: "digest",
          no_change: false,
        };
      }
      throw new Error(`unexpected action ${action}`);
    });
    renderWorkspace({ transport: { requestMutation } as unknown as WebUIMutationTransport });

    fireEvent.click(await screen.findByTestId("open-article-srv-1"));
    await waitFor(() =>
      expect(screen.getByTestId("article-body-input")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByTestId("article-body-input"), { target: { value: "新写的稿子" } });

    fireEvent.click(screen.getByLabelText("Back"));

    // 自动保存成功 → 不弹对话框，直接回到列表。
    await waitFor(() => expect(requestMutation).toHaveBeenCalled(), { timeout: 4000 });
    expect(screen.queryByTestId("leave-guard")).toBeNull();
    await waitFor(() => expect(screen.getByText("服务器上的文章")).toBeInTheDocument());
  });

  it("leave guard: when the save fails the three-option dialog shows up", async () => {
    const requestMutation = vi.fn(async (action: string) => {
      if (action === "article.save") {
        throw Object.assign(new Error("article_conflict current_revision=5 article_id=srv-1"), {
          status: 409,
        });
      }
      throw new Error(`unexpected action ${action}`);
    });
    renderWorkspace({ transport: { requestMutation } as unknown as WebUIMutationTransport });

    fireEvent.click(await screen.findByTestId("open-article-srv-1"));
    await waitFor(() =>
      expect(screen.getByTestId("article-body-input")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByTestId("article-body-input"), { target: { value: "新写的稿子" } });

    fireEvent.click(screen.getByLabelText("Back"));

    await waitFor(() =>
      expect(screen.getByTestId("leave-guard")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("leave-guard-retry")).toBeInTheDocument();
    expect(screen.getByTestId("leave-guard-stay")).toBeInTheDocument();
    expect(screen.getByTestId("leave-guard-force")).toBeInTheDocument();
    // 仍在编辑器。
    expect(screen.getByTestId("article-body-input")).toBeInTheDocument();

    // 强制离开：跳到列表，本地稿保留（cacheDraft 在 setField 时已经写入）。
    fireEvent.click(screen.getByTestId("leave-guard-force"));
    await waitFor(() => expect(screen.getByText("服务器上的文章")).toBeInTheDocument());
    const stored = JSON.parse(localStorage.getItem(DRAFT_STORAGE_KEY) ?? "{}");
    const bucket = stored[WORKSPACE] as Record<string, unknown> | undefined;
    expect(bucket?.["srv-1"]).toBeTruthy();
  });

  it("leave guard: stay keeps the user in the editor", async () => {
    const requestMutation = vi.fn(async (action: string) => {
      if (action === "article.save") {
        throw Object.assign(new Error("article_conflict current_revision=5 article_id=srv-1"), {
          status: 409,
        });
      }
      throw new Error(`unexpected action ${action}`);
    });
    renderWorkspace({ transport: { requestMutation } as unknown as WebUIMutationTransport });

    fireEvent.click(await screen.findByTestId("open-article-srv-1"));
    await waitFor(() =>
      expect(screen.getByTestId("article-body-input")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByTestId("article-body-input"), { target: { value: "新写的稿子" } });

    fireEvent.click(screen.getByLabelText("Back"));
    await waitFor(() => expect(screen.getByTestId("leave-guard")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("leave-guard-stay"));

    await waitFor(() => expect(screen.queryByTestId("leave-guard")).toBeNull());
    expect(screen.getByTestId("article-body-input")).toBeInTheDocument();
    expect(requestMutation.mock.calls.filter(
      (entry: unknown[]) => entry[0] === "article.save",
    )).toHaveLength(1);
  });

  it("beforeunload prompts the browser when the draft is dirty and is silent when clean", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByTestId("open-article-srv-1"));
    await waitFor(() =>
      expect(screen.getByTestId("article-body-input")).toBeInTheDocument(),
    );
    const event = new Event("beforeunload") as BeforeUnloadEvent;
    const preventDefault = vi.spyOn(event, "preventDefault");
    Object.defineProperty(event, "returnValue", { writable: true, value: undefined });

    window.dispatchEvent(event);
    expect(preventDefault).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("article-body-input"), { target: { value: "脏稿" } });
    const dirtyEvent = new Event("beforeunload") as BeforeUnloadEvent;
    const dirtyPreventDefault = vi.spyOn(dirtyEvent, "preventDefault");
    window.dispatchEvent(dirtyEvent);
    expect(dirtyPreventDefault).toHaveBeenCalled();
  });

  it("shows the storage-quota hint when writeDraft returns false on every call", async () => {
    const originalSetItem = window.localStorage.setItem.bind(window.localStorage);
    const setItemSpy = vi.spyOn(window.localStorage, "setItem")
      .mockImplementation((key: string, value: string) => {
        if (key === DRAFT_STORAGE_KEY) {
          throw new Error("quota exceeded");
        }
        // 不挡其他 key（如 react-i18next 自己的 namespace key），避免破坏测试环境。
        originalSetItem(key, value);
      });

    try {
      renderWorkspace();
      fireEvent.click(await screen.findByTestId("open-article-srv-1"));
      await waitFor(() =>
        expect(screen.getByTestId("article-body-input")).toBeInTheDocument(),
      );

      // 触发一次 setField 让 cacheDraft 跑一遍 writeDraft → 触发 quota 路径。
      fireEvent.change(screen.getByTestId("article-body-input"), {
        target: { value: "hello" },
      });
      await waitFor(() =>
        expect(screen.getByTestId("recovery-storage-hint")).toBeInTheDocument(),
      );
      expect(screen.getByTestId("recovery-storage-hint").textContent).toContain("恢复保障不可用");
    } finally {
      setItemSpy.mockRestore();
    }
  });
});
