import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { readDraft } from "@/lib/writing-draft-cache";
import type { Article } from "@/components/creative/types";
import type { SaveArticleResult } from "@/lib/writing-api";
import {
  AUTOSAVE_DEBOUNCE_MS,
  ARTICLE_DELETED_REVISION,
  useArticleSaveFlow,
} from "@/components/creative/use-article-save-flow";

const WORKSPACE = "/workspace-flow";

function articleFixture(overrides: Partial<Article> = {}): Article {
  return {
    id: "a1",
    title: "标题",
    writingType: "",
    status: "draft",
    summary: "",
    brief: {},
    body: "正文",
    outline: [],
    materials: [],
    tags: [],
    revision: 1,
    createdAt: "2026-01-01T00:00:00+00:00",
    updatedAt: "2026-01-02T00:00:00+00:00",
    ...overrides,
  };
}

function saveOk(overrides: Partial<SaveArticleResult> = {}): SaveArticleResult {
  return {
    article_id: "a1",
    revision: 2,
    updated_at: "2026-01-03T00:00:00+00:00",
    digest: "digest-2",
    no_change: false,
    ...overrides,
  };
}

type MutationSpy = ReturnType<typeof vi.fn>;

function renderFlow(article: Article, requestMutation: MutationSpy) {
  const transport = { requestMutation };
  return renderHook(() => useArticleSaveFlow({
    getToken: () => "token",
    transport,
    article,
    workspacePath: WORKSPACE,
  }));
}

function payloadOf(spy: MutationSpy, index: number): Record<string, unknown> {
  const call = spy.mock.calls[index] as [string, Record<string, unknown>];
  return call[1];
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
  });
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useArticleSaveFlow (A3.2)", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("merges edits made while a save is in flight instead of overwriting them", async () => {
    const requestMutation = vi.fn<MutationSpy>()
      .mockImplementationOnce(async () => saveOk({ revision: 2 }))
      .mockImplementationOnce(async () => saveOk({ revision: 3 }));
    const { result } = renderFlow(articleFixture({ revision: 1 }), requestMutation);

    act(() => result.current.setField("body", "第一稿"));
    let pending: Promise<void> | undefined;
    act(() => { pending = result.current.saveNow(); });
    // A-T03：保存中继续输入，内容必须留在编辑器里。
    act(() => result.current.setField("body", "保存中又改了"));
    await act(async () => { await pending; });

    expect(result.current.state.baseRevision).toBe(2);
    expect(result.current.state.phase).toBe("dirty");
    expect(result.current.dirty).toBe(true);
    expect(result.current.draft.body).toBe("保存中又改了");

    await advance(AUTOSAVE_DEBOUNCE_MS);
    expect(requestMutation).toHaveBeenCalledTimes(2);
    expect(payloadOf(requestMutation, 1).snapshot).toMatchObject({ body: "保存中又改了" });
    expect(result.current.state.baseRevision).toBe(3);
    expect(result.current.state.phase).toBe("saved");
  });

  it("does not advance revision when the content is unchanged", async () => {
    const requestMutation = vi.fn<MutationSpy>(async () =>
      saveOk({ revision: 1, no_change: true, updated_at: "2026-01-09T00:00:00+00:00" }));
    const { result } = renderFlow(articleFixture({ revision: 1 }), requestMutation);

    act(() => result.current.setField("title", "只是改了标题又改回来"));
    const seq = result.current.state.localEditSeq;
    await act(async () => { await result.current.saveNow(); });

    expect(result.current.state.phase).toBe("saved");
    expect(result.current.state.localEditSeq).toBe(seq);
    expect(result.current.state.baseRevision).toBe(1);
    expect(result.current.state.lastSavedAt).toBe("2026-01-09T00:00:00+00:00");
    // 服务端确认后恢复稿就不再是「未保存内容」的证据。
    expect(readDraft(WORKSPACE, "a1")).toBeNull();
  });

  it("stops autosave and keeps the local draft on 409 conflict", async () => {
    const requestMutation = vi.fn<MutationSpy>(async () => {
      throw new ApiError(409, "article_conflict current_revision=7 article_id=a1");
    });
    const { result } = renderFlow(articleFixture({ revision: 1 }), requestMutation);

    act(() => result.current.setField("body", "本地改动"));
    await act(async () => { await result.current.saveNow(); });

    expect(result.current.state.phase).toBe("conflict");
    expect(result.current.state.conflict).toEqual({ currentRevision: 7, articleId: "a1" });
    expect(readDraft(WORKSPACE, "a1")?.snapshot.body).toBe("本地改动");

    const calls = requestMutation.mock.calls.length;
    await advance(AUTOSAVE_DEBOUNCE_MS * 3);
    expect(requestMutation.mock.calls.length).toBe(calls);

    // 冲突期间继续编辑：保留本地内容，也不重启自动保存。
    act(() => result.current.setField("body", "本地改动二"));
    await advance(AUTOSAVE_DEBOUNCE_MS * 3);
    expect(requestMutation.mock.calls.length).toBe(calls);
    expect(result.current.draft.body).toBe("本地改动二");
    // 恢复稿跟着最新编辑走，冲突暂停期间也不会丢内容。
    expect(readDraft(WORKSPACE, "a1")?.snapshot.body).toBe("本地改动二");
  });

  it("marks an already-deleted article as a conflict without a server revision", async () => {
    const requestMutation = vi.fn<MutationSpy>(async () => {
      throw new ApiError(404, "article deleted");
    });
    const { result } = renderFlow(articleFixture(), requestMutation);

    act(() => result.current.setField("body", "还想再写一点"));
    await act(async () => { await result.current.saveNow(); });

    expect(result.current.state.phase).toBe("conflict");
    expect(result.current.state.conflict?.currentRevision).toBe(ARTICLE_DELETED_REVISION);
    expect(result.current.state.conflict?.articleId).toBe("a1");
  });

  it("reuses one client_request_id across retries of the same draft", async () => {
    const requestMutation = vi.fn<MutationSpy>()
      .mockImplementationOnce(async () => { throw new ApiError(500, "article save failed"); })
      .mockImplementationOnce(async () => saveOk({ revision: 2 }))
      .mockImplementationOnce(async () => saveOk({ revision: 3 }));
    const { result } = renderFlow(articleFixture({ revision: 1 }), requestMutation);

    act(() => result.current.setField("body", "同一份稿子"));
    await act(async () => { await result.current.saveNow(); });
    expect(result.current.state.phase).toBe("failed");

    await act(async () => { await result.current.saveNow(); });
    const first = payloadOf(requestMutation, 0).client_request_id;
    const second = payloadOf(requestMutation, 1).client_request_id;
    expect(typeof first).toBe("string");
    expect(second).toBe(first);

    // 草稿真的又变了，才换一个新的业务幂等 id。
    act(() => result.current.setField("body", "再改一点"));
    await advance(AUTOSAVE_DEBOUNCE_MS);
    expect(payloadOf(requestMutation, 2).client_request_id).not.toBe(first);
  });

  it("falls back to offline phase when the transport rejects with 503", async () => {
    const requestMutation = vi.fn<MutationSpy>(async () => {
      throw new ApiError(503, "writing store unavailable");
    });
    const { result } = renderFlow(articleFixture(), requestMutation);

    act(() => result.current.setField("body", "离线也要留着"));
    await act(async () => { await result.current.saveNow(); });

    expect(result.current.state.phase).toBe("offline");
    expect(result.current.state.lastError).toBe("writing store unavailable");
    expect(readDraft(WORKSPACE, "a1")?.snapshot.body).toBe("离线也要留着");

    const calls = requestMutation.mock.calls.length;
    await advance(AUTOSAVE_DEBOUNCE_MS * 2);
    expect(requestMutation.mock.calls.length).toBe(calls);
  });

  it("does not schedule a send while the offline pause is in effect", async () => {
    const requestMutation = vi.fn<MutationSpy>(async () => {
      throw new ApiError(503, "writing store unavailable");
    });
    const { result } = renderFlow(articleFixture(), requestMutation);

    act(() => result.current.setField("body", "第一次尝试"));
    await act(async () => { await result.current.saveNow(); });
    expect(result.current.state.phase).toBe("offline");

    // 离线期间继续输入仍会排上自动保存定时器，到点必须不发请求（A-08）。
    const calls = requestMutation.mock.calls.length;
    act(() => result.current.setField("body", "离线继续写"));
    await advance(AUTOSAVE_DEBOUNCE_MS * 2);
    expect(requestMutation.mock.calls.length).toBe(calls);
    expect(result.current.state.phase).toBe("offline");
    expect(readDraft(WORKSPACE, "a1")?.snapshot.body).toBe("离线继续写");
  });

  it("shows 已保存 only after the server confirms", async () => {
    let release: ((value: SaveArticleResult) => void) | null = null;
    const requestMutation = vi.fn<MutationSpy>(
      () => new Promise<SaveArticleResult>((resolve) => { release = resolve; }),
    );
    const { result } = renderFlow(articleFixture({ revision: 1 }), requestMutation);

    act(() => result.current.setField("body", "刚写完"));
    let pending: Promise<void> | undefined;
    act(() => { pending = result.current.saveNow(); });
    await settle();

    expect(result.current.state.phase).toBe("saving");
    expect(result.current.state.lastSavedAt).not.toBe("2026-01-04T00:00:00+00:00");

    release?.(saveOk({ revision: 2, updated_at: "2026-01-04T00:00:00+00:00" }));
    await act(async () => { await pending; });

    expect(result.current.state.phase).toBe("saved");
    expect(result.current.state.baseRevision).toBe(2);
    expect(result.current.state.lastSavedAt).toBe("2026-01-04T00:00:00+00:00");
  });

  it("sends every mutation as article.save with snake_case keys", async () => {
    const requestMutation = vi.fn<MutationSpy>(async () => saveOk({ revision: 2 }));
    const { result } = renderFlow(articleFixture({ revision: 4 }), requestMutation);

    act(() => result.current.setField("summary", "摘要"));
    await advance(AUTOSAVE_DEBOUNCE_MS);

    expect(requestMutation).toHaveBeenCalledTimes(1);
    const [action, payload] = requestMutation.mock.calls[0] as [string, Record<string, unknown>];
    expect(action).toBe("article.save");
    expect(Object.keys(payload).sort()).toEqual(
      ["article_id", "base_revision", "client_request_id", "snapshot"].sort(),
    );
    expect(payload.article_id).toBe("a1");
    expect(payload.base_revision).toBe(4);
    expect(payload.snapshot).toMatchObject({ summary: "摘要" });
  });
});
