import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyCollabSnapshot,
  collabApi,
  createCollabApi,
  createCollabReducer,
  createRequestIdFactory,
  utf16RangeToCodepoints,
  type CollabSnapshot,
} from "@/lib/writing-collab-api";
import type { WebUIMutationTransport } from "@/lib/api";

/** 帧字段与契约 §5 一致：snake_case 只存在于传输层。 */
function collabFrame(
  event: string,
  body: Record<string, unknown>,
  chatId = "chat-1",
): Record<string, unknown> {
  return { event, chat_id: chatId, ...body };
}

function artifactFrame(artifactId: string, version: number, summary: string): Record<string, unknown> {
  return collabFrame("writing_artifact", {
    collaboration_id: "c1",
    artifact_id: artifactId,
    // 归属批次：重连 reset 之后旧批次的迟到帧必须被丢掉。
    batch_id: "b1",
    kind: "outline",
    version,
    summary,
  });
}

function snapshotWith(overrides: Partial<CollabSnapshot> = {}): CollabSnapshot {
  return {
    collaboration: null,
    batches: [],
    artifacts: [],
    handoffs: [],
    suggestions: [],
    lastVersion: 0,
    lastSeq: 0,
    nextActions: [],
    blockedReason: null,
    ...overrides,
  };
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("collabApi reads", () => {
  it("test_snapshot_maps_snake_case", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      collaboration: {
        id: "c1",
        article_id: "a1",
        chat_id: "chat-1",
        session_key: "websocket:chat-1",
        goal: { topic: "记忆" },
        status: "running",
        stage: "draft",
        active_batch_id: "b2",
        cancel_generation: 2,
        version: 7,
        seq: 12,
        last_handoff_id: "h1",
        created_at: "2026-10-17T00:00:00+00:00",
        updated_at: "2026-10-17T00:05:00+00:00",
      },
      batches: [
        {
          id: "b2",
          collaboration_id: "c1",
          attempt: 2,
          team_run_id: "run-9",
          base_revision: 4,
          snapshot_digest: "d1",
          action: "rewrite_selection",
          instruction: "收紧结论",
          status: "running",
          cancel_generation: 0,
          frozen: { model_id: "m1" },
          error_message: "",
          created_at: "2026-10-17T00:01:00+00:00",
          finished_at: null,
        },
      ],
      artifacts: [
        {
          id: "art1",
          collaboration_id: "c1",
          batch_id: "b2",
          attempt: 2,
          kind: "outline",
          role_id: "writing-architect",
          version: 3,
          content: "",
          content_digest: "cd",
          source_ids: ["m1"],
          gaps: ["缺反例"],
          status: "ready",
          created_at: "2026-10-17T00:02:00+00:00",
        },
      ],
      handoffs: [
        {
          id: "h1",
          collaboration_id: "c1",
          batch_id: "b2",
          artifact_id: "art1",
          kind: "outline_confirm",
          artifact_version: 3,
          status: "pending",
          created_at: "2026-10-17T00:03:00+00:00",
          resolved_at: null,
        },
      ],
      suggestions: [
        {
          id: "s1",
          collaboration_id: "c1",
          artifact_id: "art1",
          article_id: "a1",
          base_revision: 4,
          snapshot_digest: "d1",
          target: { kind: "replace_range", start: 0, end: 2 },
          before: "旧",
          after: "新",
          summary: "更直接",
          notes: "依据材料 m1",
          source_material_ids: ["m1"],
          status: "pending",
          created_at: "2026-10-17T00:04:00+00:00",
          applied_revision: 6,
          brand_new_field: "ignored",
        },
      ],
      last_version: 7,
      last_seq: 12,
      next_actions: ["outline", "review_all"],
      blocked_reason: "selection is empty",
    }));
    vi.stubGlobal("fetch", fetchMock);

    const snapshot = await collabApi.getSnapshot("a1");

    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/writing/collab?article_id=a1");
    expect(snapshot.lastVersion).toBe(7);
    expect(snapshot.lastSeq).toBe(12);
    expect(snapshot.nextActions).toEqual(["outline", "review_all"]);
    expect(snapshot.blockedReason).toBe("selection is empty");
    expect(snapshot.collaboration).toEqual({
      id: "c1",
      articleId: "a1",
      chatId: "chat-1",
      sessionKey: "websocket:chat-1",
      goal: { topic: "记忆" },
      status: "running",
      stage: "draft",
      activeBatchId: "b2",
      cancelGeneration: 2,
      version: 7,
      seq: 12,
      lastHandoffId: "h1",
      createdAt: "2026-10-17T00:00:00+00:00",
      updatedAt: "2026-10-17T00:05:00+00:00",
    });
    expect(snapshot.batches[0]).toEqual({
      id: "b2",
      collaborationId: "c1",
      attempt: 2,
      teamRunId: "run-9",
      baseRevision: 4,
      snapshotDigest: "d1",
      action: "rewrite_selection",
      instruction: "收紧结论",
      status: "running",
      cancelGeneration: 0,
      frozen: { model_id: "m1" },
      errorMessage: "",
      createdAt: "2026-10-17T00:01:00+00:00",
      finishedAt: null,
    });
    expect(snapshot.artifacts[0]).toEqual({
      id: "art1",
      collaborationId: "c1",
      batchId: "b2",
      attempt: 2,
      kind: "outline",
      roleId: "writing-architect",
      version: 3,
      content: "",
      contentDigest: "cd",
      sourceIds: ["m1"],
      gaps: ["缺反例"],
      status: "ready",
      createdAt: "2026-10-17T00:02:00+00:00",
    });
    expect(snapshot.handoffs[0]).toEqual({
      id: "h1",
      collaborationId: "c1",
      batchId: "b2",
      artifactId: "art1",
      kind: "outline_confirm",
      artifactVersion: 3,
      status: "pending",
      createdAt: "2026-10-17T00:03:00+00:00",
      resolvedAt: null,
    });
    expect(snapshot.suggestions[0]).toEqual({
      id: "s1",
      collaborationId: "c1",
      artifactId: "art1",
      articleId: "a1",
      baseRevision: 4,
      snapshotDigest: "d1",
      target: { kind: "replace_range", start: 0, end: 2 },
      before: "旧",
      after: "新",
      summary: "更直接",
      notes: "依据材料 m1",
      sourceMaterialIds: ["m1"],
      status: "pending",
      createdAt: "2026-10-17T00:04:00+00:00",
      appliedRevision: 6,
    });
    expect(JSON.stringify(snapshot)).not.toContain("base_revision");
    expect(JSON.stringify(snapshot)).not.toContain("brand_new_field");
  });

  it("tolerates a broken snapshot body without throwing", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);

    const snapshot = await collabApi.getSnapshot("a1");

    expect(snapshot.collaboration).toBeNull();
    expect(snapshot.batches).toEqual([]);
    expect(snapshot.artifacts).toEqual([]);
    expect(snapshot.handoffs).toEqual([]);
    expect(snapshot.suggestions).toEqual([]);
    expect(snapshot.lastVersion).toBe(0);
    expect(snapshot.lastSeq).toBe(0);
    expect(snapshot.nextActions).toEqual([]);
    expect(snapshot.blockedReason).toBeNull();
  });

  it("fetches one artifact by id and maps it", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      artifact: {
        id: "art1",
        collaboration_id: "c1",
        batch_id: "b2",
        attempt: 1,
        kind: "draft",
        role_id: "writing-author",
        version: 2,
        content: { body: "正文" },
        content_digest: "cd",
        source_ids: [],
        gaps: [],
        status: "ready",
        created_at: "2026-10-17T00:02:00+00:00",
      },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const artifact = await collabApi.getArtifact("art1");

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/api/writing/collab/artifact?artifact_id=art1",
    );
    expect(artifact.id).toBe("art1");
    expect(artifact.kind).toBe("draft");
    expect(artifact.roleId).toBe("writing-author");
    expect(artifact.content).toEqual({ body: "正文" });
    expect(artifact.contentDigest).toBe("cd");
  });
});

describe("collabApi writes", () => {
  function transport(): WebUIMutationTransport & { requestMutation: ReturnType<typeof vi.fn> } {
    return {
      requestMutation: vi.fn().mockResolvedValue({
        collaboration_id: "c1",
        batch_id: "b1",
        status: "queued",
        stage: "brief",
        session_key: "websocket:chat-1",
        active_batch_id: "b1",
      }),
    } as unknown as WebUIMutationTransport & { requestMutation: ReturnType<typeof vi.fn> };
  }

  it("test_start_sends_client_request_id", async () => {
    const fake = transport();
    const api = createCollabApi({
      transport: fake,
      requestIds: createRequestIdFactory(() => "uuid-fixed-1"),
    });

    await api.start({
      articleId: "a1",
      chatId: "chat-1",
      action: "rewrite_selection",
      baseRevision: 4,
      snapshotDigest: "d1",
      instruction: "收紧结论",
      selection: { start: 1, end: 5, text: "旧正文" },
      materialIds: ["m1"],
    });

    expect(fake.requestMutation.mock.calls[0][0]).toBe("writing.start");
    const payload = fake.requestMutation.mock.calls[0][1] as Record<string, unknown>;
    expect(payload.client_request_id).toBe("uuid-fixed-1");
    expect(payload).toMatchObject({
      article_id: "a1",
      chat_id: "chat-1",
      action: "rewrite_selection",
      base_revision: 4,
      snapshot_digest: "d1",
      instruction: "收紧结论",
      selection: { start: 1, end: 5, text: "旧正文" },
      material_ids: ["m1"],
    });
    expect(payload.clientRequestId).toBeUndefined();
    expect(payload.articleId).toBeUndefined();
    expect(payload.baseRevision).toBeUndefined();

    // 重试：调用方必须能拿同一个 id 重放，服务端靠它幂等。
    fake.requestMutation.mockResolvedValue({
      collaboration_id: "c1",
      batch_id: "b1",
      status: "queued",
    });
    await api.start({
      clientRequestId: "uuid-fixed-1",
      articleId: "a1",
      chatId: "chat-1",
      action: "rewrite_selection",
      baseRevision: 4,
      snapshotDigest: "d1",
      instruction: "收紧结论",
      selection: { start: 1, end: 5, text: "旧正文" },
      materialIds: ["m1"],
    });
    expect(fake.requestMutation.mock.calls[1][1]).toMatchObject({
      client_request_id: "uuid-fixed-1",
    });
  });

  it("every call without a reused id gets a fresh uuid", async () => {
    let counter = 0;
    const fake = transport();
    const api = createCollabApi({
      transport: fake,
      requestIds: createRequestIdFactory(() => `uuid-${++counter}`),
    });

    await api.cancel({ collaborationId: "c1", activeBatchId: "b1" });
    await api.cancel({ collaborationId: "c1", activeBatchId: "b1" });

    expect(fake.requestMutation.mock.calls[0][1].client_request_id).toBe("uuid-1");
    expect(fake.requestMutation.mock.calls[1][1].client_request_id).toBe("uuid-2");
  });

  it("reuses one request id per logical operation across a manual retry", () => {
    let counter = 0;
    const requestIds = createRequestIdFactory(() => `uuid-${++counter}`);

    const first = requestIds.for("start:a1");
    const retry = requestIds.for("start:a1");
    const other = requestIds.for("start:a2");
    requestIds.clear("start:a1");
    const afterClear = requestIds.for("start:a1");

    expect(retry).toBe(first);
    expect(other).not.toBe(first);
    expect(afterClear).not.toBe(first);
  });

  it("sends the reply / confirm / cancel / retry contract fields", async () => {
    const fake = transport();
    const api = createCollabApi({
      transport: fake,
      requestIds: createRequestIdFactory(() => "uuid-fixed"),
    });

    await api.reply({ collaborationId: "c1", feedback: "改成第二人称", expectVersion: 7 });
    await api.confirm({
      collaborationId: "c1",
      handoffId: "h1",
      expectedHandoffVersion: 3,
      outline: [{ id: "o1", text: "x", order: 0 }],
      articleRevision: 4,
      articleDigest: "d1",
    });
    await api.cancel({ collaborationId: "c1", activeBatchId: "b1" });
    await api.retry({ collaborationId: "c1", batchId: "b1", expectVersion: 7 });

    const actions = fake.requestMutation.mock.calls.map((call) => call[0]);
    expect(actions).toEqual([
      "writing.reply",
      "writing.confirm",
      "writing.cancel",
      "writing.retry",
    ]);
    expect(fake.requestMutation.mock.calls[0][1]).toEqual({
      client_request_id: "uuid-fixed",
      collaboration_id: "c1",
      feedback: "改成第二人称",
      expect_version: 7,
    });
    expect(fake.requestMutation.mock.calls[1][1]).toEqual({
      client_request_id: "uuid-fixed",
      collaboration_id: "c1",
      handoff_id: "h1",
      expected_handoff_version: 3,
      outline: [{ id: "o1", text: "x", order: 0 }],
      article_revision: 4,
      article_digest: "d1",
    });
    expect(fake.requestMutation.mock.calls[2][1]).toEqual({
      client_request_id: "uuid-fixed",
      collaboration_id: "c1",
      active_batch_id: "b1",
    });
    expect(fake.requestMutation.mock.calls[3][1]).toEqual({
      client_request_id: "uuid-fixed",
      collaboration_id: "c1",
      batch_id: "b1",
      expect_version: 7,
    });
  });

  it("test_apply_and_undo_payloads", async () => {
    const fake = transport();
    const api = createCollabApi({
      transport: fake,
      requestIds: createRequestIdFactory(() => "uuid-fixed"),
    });

    await api.apply({ suggestionId: "s1", articleId: "a1", baseRevision: 4 });
    await api.undo({ undoId: "u1", baseRevision: 6 });

    expect(fake.requestMutation.mock.calls[0][0]).toBe("writing.apply");
    expect(fake.requestMutation.mock.calls[0][1]).toEqual({
      client_request_id: "uuid-fixed",
      suggestion_id: "s1",
      article_id: "a1",
      base_revision: 4,
    });
    expect(fake.requestMutation.mock.calls[1][0]).toBe("writing.undo");
    expect(fake.requestMutation.mock.calls[1][1]).toEqual({
      client_request_id: "uuid-fixed",
      undo_id: "u1",
      base_revision: 6,
    });
  });

  it("maps write results to camelCase and omits absent optional inputs", async () => {
    const fake = transport();
    // 按调用顺序排队：start → cancel → confirm → apply → undo → retry。
    fake.requestMutation.mockResolvedValueOnce({
      collaboration_id: "c1",
      batch_id: "b1",
      status: "queued",
      stage: "brief",
      session_key: "websocket:chat-1",
      active_batch_id: "b1",
    });
    fake.requestMutation.mockResolvedValueOnce({
      collaboration_id: "c1",
      status: "cancelled",
      cancel_generation: 3,
    });
    fake.requestMutation.mockResolvedValueOnce({
      collaboration_id: "c1",
      batch_id: "b1",
      article_revision: 5,
      article_digest: "d2",
      handoff_status: "confirmed",
    });
    fake.requestMutation.mockResolvedValueOnce({
      suggestion_id: "s1",
      article_id: "a1",
      revision: 7,
      digest: "d3",
      staled_suggestion_ids: ["s2"],
      undo_id: "u1",
    });
    fake.requestMutation.mockResolvedValueOnce({
      undo_id: "u1",
      article_id: "a1",
      revision: 8,
      digest: "d4",
      suggestion_id: "s1",
    });
    fake.requestMutation.mockResolvedValueOnce({
      collaboration_id: "c1",
      batch_id: "b1",
      attempt: 2,
      status: "queued",
    });
    const api = createCollabApi({
      transport: fake,
      requestIds: createRequestIdFactory(() => "uuid-fixed"),
    });

    await expect(
      api.start({
        articleId: "a1",
        chatId: "chat-1",
        action: "ideate",
        baseRevision: 1,
        snapshotDigest: "d1",
      }),
    ).resolves.toEqual({
      collaborationId: "c1",
      batchId: "b1",
      status: "queued",
      stage: "brief",
      sessionKey: "websocket:chat-1",
      activeBatchId: "b1",
    });
    expect(fake.requestMutation.mock.calls[0][1]).not.toHaveProperty("instruction");
    expect(fake.requestMutation.mock.calls[0][1]).not.toHaveProperty("selection");
    expect(fake.requestMutation.mock.calls[0][1]).not.toHaveProperty("material_ids");

    await expect(api.cancel({ collaborationId: "c1", activeBatchId: "b1" })).resolves.toEqual({
      collaborationId: "c1",
      status: "cancelled",
      cancelGeneration: 3,
    });
    await expect(
      api.confirm({
        collaborationId: "c1",
        handoffId: "h1",
        expectedHandoffVersion: 3,
        articleRevision: 4,
        articleDigest: "d1",
      }),
    ).resolves.toEqual({
      collaborationId: "c1",
      batchId: "b1",
      articleRevision: 5,
      articleDigest: "d2",
      handoffStatus: "confirmed",
    });
    expect(fake.requestMutation.mock.calls[2][1]).not.toHaveProperty("outline");
    await expect(api.apply({ suggestionId: "s1", articleId: "a1", baseRevision: 4 })).resolves
      .toEqual({
        suggestionId: "s1",
        articleId: "a1",
        revision: 7,
        digest: "d3",
        staledSuggestionIds: ["s2"],
        undoId: "u1",
      });
    await expect(api.undo({ undoId: "u1", baseRevision: 7 })).resolves.toEqual({
      undoId: "u1",
      articleId: "a1",
      revision: 8,
      digest: "d4",
      suggestionId: "s1",
    });
    await expect(
      api.retry({ collaborationId: "c1", batchId: "b1", expectVersion: 7 }),
    ).resolves.toEqual({
      collaborationId: "c1",
      batchId: "b1",
      attempt: 2,
      status: "queued",
    });
  });
});

describe("utf16RangeToCodepoints", () => {
  it("test_utf16_to_codepoint_selection", () => {
    // 中文：UTF-16 码元数 == 码点数。
    const cjk = "记忆系统设计";
    expect(utf16RangeToCodepoints(cjk, 0, 2)).toEqual({ start: 0, end: 2 });
    expect(utf16RangeToCodepoints(cjk, 2, 4)).toEqual({ start: 2, end: 4 });

    // 每个 emoji 在 JS 里占 2 个 UTF-16 码元，在 Python 里只占 1 个码点。
    const emoji = "😀ab";
    expect(utf16RangeToCodepoints(emoji, 2, 3)).toEqual({ start: 1, end: 2 });
    expect(utf16RangeToCodepoints(emoji, 0, 4)).toEqual({ start: 0, end: 3 });

    const mixed = "前😀后";
    expect([...mixed]).toHaveLength(3);
    expect(mixed.length).toBe(4);
    // UTF-16 [2,4) == 码点 [1,3)：emoji 占一个码点，后面那个字占一个。
    expect(utf16RangeToCodepoints(mixed, 2, 4)).toEqual({ start: 1, end: 3 });
    expect(utf16RangeToCodepoints(mixed, 0, 4)).toEqual({ start: 0, end: 3 });
    // 逐个偏移：切在代理对中间（offset 2）时向下取整，不产生越界码点。
    expect([0, 1, 2, 3, 4].map((offset) => utf16RangeToCodepoints(mixed, offset, offset).start))
      .toEqual([0, 1, 1, 2, 3]);

    // 代理对只有**整个**落在区间内才折叠：切在中间时前半算 0 个码点（向下取整）。
    expect(utf16RangeToCodepoints(emoji, 1, 4)).toEqual({ start: 0, end: 3 });
    expect(utf16RangeToCodepoints(emoji, 1, 2)).toEqual({ start: 0, end: 1 });

    // 转换后必须能用 Python 的码点切片还原出作者真正选中的文字。
    const points = [...mixed];
    const range = utf16RangeToCodepoints(mixed, 2, 4);
    expect(points.slice(range.start, range.end).join("")).toBe("😀后");
    const beforeEmoji = utf16RangeToCodepoints(mixed, 0, 2);
    expect(points.slice(beforeEmoji.start, beforeEmoji.end).join("")).toBe("前");
    const insideEmoji = utf16RangeToCodepoints(emoji, 2, 3);
    expect([...emoji].slice(insideEmoji.start, insideEmoji.end).join("")).toBe("a");

    expect(utf16RangeToCodepoints("abc", 1, 1)).toEqual({ start: 1, end: 1 });
    expect(utf16RangeToCodepoints("", 0, 0)).toEqual({ start: 0, end: 0 });
    expect(utf16RangeToCodepoints("abc", 2, 3)).toEqual({ start: 2, end: 3 });
    expect(utf16RangeToCodepoints("abc", 9, 12)).toEqual({ start: 3, end: 3 });
    expect(utf16RangeToCodepoints("abc", -5, 2)).toEqual({ start: 0, end: 2 });
    // 反向区间按空选区处理：落点停在 start 那一端，不悄悄扩成一段。
    expect(utf16RangeToCodepoints("abc", 2, 1)).toEqual({ start: 2, end: 2 });
  });
});

describe("collab event merge", () => {
  it("test_event_merge_ignores_stale_version", () => {
    const reducer = createCollabReducer();
    let state = reducer.applyEvent(
      snapshotWith(),
      collabFrame("writing_collab", {
        collaboration_id: "c1",
        status: "running",
        stage: "draft",
        active_batch_id: "b1",
        version: 5,
        seq: 5,
      }),
    );
    expect(state.collaboration?.status).toBe("running");
    expect(state.lastVersion).toBe(5);

    state = reducer.applyEvent(
      state,
      collabFrame("writing_collab", {
        collaboration_id: "c1",
        status: "queued",
        stage: "brief",
        active_batch_id: "b1",
        version: 4,
        seq: 4,
      }),
    );
    expect(state.collaboration?.status).toBe("running");
    expect(state.collaboration?.stage).toBe("draft");
    expect(state.lastVersion).toBe(5);

    // 同版本重放同样不倒退（严格大于才归并）。
    state = reducer.applyEvent(
      state,
      collabFrame("writing_collab", {
        collaboration_id: "c1",
        status: "failed",
        stage: "draft",
        version: 5,
        seq: 5,
      }),
    );
    expect(state.collaboration?.status).toBe("running");

    // 旧 artifact 帧不得覆盖新版本。
    state = reducer.applyEvent(state, artifactFrame("art1", 3, "提纲 v3"));
    state = reducer.applyEvent(state, artifactFrame("art1", 2, "提纲 v2"));
    expect(state.artifacts).toHaveLength(1);
    expect(state.artifacts[0]).toMatchObject({ version: 3, summary: "提纲 v3" });
  });

  it("test_event_merge_is_idempotent", () => {
    const reducer = createCollabReducer();
    let state = reducer.applyEvent(
      snapshotWith(),
      collabFrame("writing_collab", {
        collaboration_id: "c1",
        status: "running",
        stage: "outline",
        active_batch_id: "b1",
        version: 2,
        seq: 2,
      }),
    );
    const frame = artifactFrame("art1", 1, "提纲");
    state = reducer.applyEvent(state, frame);
    state = reducer.applyEvent(state, frame);
    state = reducer.applyEvent(state, { ...frame });
    state = reducer.applyEvent(state, collabFrame("writing_handoff", {
      collaboration_id: "c1",
      handoff_id: "h1",
      kind: "outline_confirm",
      artifact_id: "art1",
      artifact_version: 1,
      status: "pending",
      version: 2,
      seq: 2,
    }));
    state = reducer.applyEvent(state, collabFrame("writing_handoff", {
      collaboration_id: "c1",
      handoff_id: "h1",
      kind: "outline_confirm",
      artifact_id: "art1",
      artifact_version: 1,
      status: "pending",
      version: 2,
      seq: 2,
    }));

    expect(state.artifacts).toHaveLength(1);
    expect(state.handoffs).toHaveLength(1);
    expect(state.artifacts[0]).toEqual({
      id: "art1",
      collaborationId: "c1",
      batchId: "b1",
      kind: "outline",
      version: 1,
      summary: "提纲",
    });
    expect(state.lastVersion).toBe(2);
    expect(state.lastSeq).toBe(2);
  });

  it("appends a newer artifact version instead of duplicating the card", () => {
    const reducer = createCollabReducer();
    // 先有活动批次，产物帧才归得到它的名下。
    let state = reducer.applyEvent(
      snapshotWith(),
      collabFrame("writing_collab", {
        collaboration_id: "c1",
        status: "running",
        stage: "draft",
        active_batch_id: "b1",
        version: 1,
        seq: 1,
      }),
    );
    state = reducer.applyEvent(state, artifactFrame("art1", 1, "v1"));
    state = reducer.applyEvent(state, artifactFrame("art1", 2, "v2"));

    expect(state.artifacts).toHaveLength(1);
    expect(state.artifacts[0]).toMatchObject({ id: "art1", version: 2, summary: "v2" });
  });

  it("test_unknown_event_ignored", () => {
    const reducer = createCollabReducer();
    const before = snapshotWith({
      collaboration: {
        id: "c1",
        articleId: "a1",
        chatId: "chat-1",
        sessionKey: "websocket:chat-1",
        goal: {},
        status: "running",
        stage: "draft",
        activeBatchId: "b1",
        cancelGeneration: 0,
        version: 5,
        seq: 5,
        lastHandoffId: null,
        createdAt: "t",
        updatedAt: "t",
      },
      lastVersion: 5,
      lastSeq: 5,
    });

    // 不认识的帧一律安全忽略：原样返回入参状态（引用相等，面板可跳过重渲）。
    expect(reducer.applyEvent(before, collabFrame("writing_unknown", { version: 99, seq: 99 })))
      .toBe(before);
    expect(reducer.applyEvent(before, collabFrame("team_event", { version: 99 }))).toBe(before);
    expect(reducer.applyEvent(before, { event: "writing_collab" })).toBe(before);
    expect(reducer.applyEvent(before, null)).toBe(before);
    expect(reducer.applyEvent(before, "writing_collab")).toBe(before);
    expect(reducer.applyEvent(before, collabFrame("writing_collab", { version: "9" })))
      .toBe(before);
  });

  it("ignores frames for another collaboration and merges blocked_reason deltas", () => {
    const reducer = createCollabReducer();
    let state = reducer.applyEvent(
      snapshotWith(),
      collabFrame("writing_collab", {
        collaboration_id: "c1",
        status: "waiting_author",
        stage: "outline",
        active_batch_id: "b1",
        version: 3,
        seq: 3,
        blocked_reason: "outline needs an author decision",
      }),
    );
    expect(state.blockedReason).toBe("outline needs an author decision");

    state = reducer.applyEvent(
      state,
      collabFrame("writing_collab", {
        collaboration_id: "other",
        status: "failed",
        version: 9,
        seq: 9,
      }),
    );
    expect(state.collaboration?.id).toBe("c1");
    expect(state.collaboration?.status).toBe("waiting_author");
    expect(state.lastVersion).toBe(3);
  });
});

describe("snapshot reconnect", () => {
  function seeded(): CollabSnapshot {
    return snapshotWith({
      collaboration: {
        id: "c1",
        articleId: "a1",
        chatId: "chat-1",
        sessionKey: "websocket:chat-1",
        goal: {},
        status: "running",
        stage: "draft",
        activeBatchId: "b1",
        cancelGeneration: 0,
        version: 5,
        seq: 5,
        lastHandoffId: null,
        createdAt: "t",
        updatedAt: "t",
      },
      artifacts: [{
        id: "art1",
        collaborationId: "c1",
        batchId: "b1",
        kind: "outline",
        version: 1,
        summary: "旧",
      }],
      lastVersion: 5,
      lastSeq: 5,
      nextActions: ["outline"],
      blockedReason: "old reason",
    });
  }

  it("test_snapshot_reset_on_reconnect", () => {
    const reducer = createCollabReducer();
    const live = reducer.applyEvent(seeded(), artifactFrame("art1", 3, "新提纲"));

    const incoming: CollabSnapshot = {
      collaboration: {
        id: "c1",
        articleId: "a1",
        chatId: "chat-1",
        sessionKey: "websocket:chat-1",
        goal: {},
        status: "completed",
        stage: "review",
        activeBatchId: null,
        cancelGeneration: 1,
        version: 9,
        seq: 9,
        lastHandoffId: "h9",
        createdAt: "t",
        updatedAt: "t2",
      },
      batches: [],
      artifacts: [{
        id: "art2",
        collaborationId: "c1",
        batchId: "b1",
        kind: "review",
        version: 1,
        summary: "复审",
      }],
      handoffs: [],
      suggestions: [],
      lastVersion: 9,
      lastSeq: 9,
      nextActions: ["review_all"],
      blockedReason: null,
    };

    const reset = reducer.applySnapshot(live, incoming);
    expect(reset.collaboration?.status).toBe("completed");
    expect(reset.lastVersion).toBe(9);
    expect(reset.lastSeq).toBe(9);
    expect(reset.artifacts).toEqual([{
      id: "art2",
      collaborationId: "c1",
      batchId: "b1",
      kind: "review",
      version: 1,
      summary: "复审",
    }]);
    expect(reset.blockedReason).toBeNull();
    expect(reset.nextActions).toEqual(["review_all"]);

    // reset 之后旧帧不能把状态拉回去。
    const afterStale = reducer.applyEvent(reset, artifactFrame("art1", 1, "旧提纲"));
    expect(afterStale.artifacts).toHaveLength(1);
    expect(afterStale.artifacts[0].id).toBe("art2");
    expect(afterStale).toEqual(reset);

    // 重连快照更旧 → 保持现状，不回退。
    expect(reducer.applySnapshot(reset, seeded())).toBe(reset);
    expect(applyCollabSnapshot(reset, seeded())).toBe(reset);
  });

  it("applySnapshot accepts the first snapshot over an empty state", () => {
    const empty = snapshotWith();
    const incoming = seeded();
    expect(applyCollabSnapshot(empty, incoming)).toEqual(incoming);
  });
});
