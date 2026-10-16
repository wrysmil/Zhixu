// 创作搭档侧栏（plan Task 12 / spec §3、§12.1）。
//
// 覆盖口径：
// 1. 面板只有「创作搭档 · 内置团队」+ 阶段 + 产物 + 主操作 + 固定输入框，
//    不出现团队选择器 / 节点编辑 / 工作流配置。
// 2. 主操作直接用文章上下文发起，不要求作者再填一份团队需求表。
// 3. 失败保留作者输入与选区；流式更新不抢焦点；切换产物不丢已输入反馈。
// 4. 任务结束总有一个明确下一步；进度是作者能读懂的状态，没有伪百分比/预计时间。
// 5. 状态用适度 live region，关键动作可键盘触达。
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WritingPartnerPanel } from "@/components/creative/WritingPartnerPanel";
import { errorKindForError, errorLabelKey } from "@/components/creative/collab-labels";
import type { WebUIMutationTransport } from "@/lib/api";
import { ApiError } from "@/lib/api";
import {
  collabApi,
  type CollabArtifact,
  type CollabSnapshot,
} from "@/lib/writing-collab-api";

vi.mock("@/lib/writing-collab-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/writing-collab-api")>(
    "@/lib/writing-collab-api",
  );
  return {
    ...actual,
    collabApi: {
      getSnapshot: vi.fn(),
      getArtifact: vi.fn(),
      start: vi.fn(),
      reply: vi.fn(),
      confirm: vi.fn(),
      cancel: vi.fn(),
      retry: vi.fn(),
      apply: vi.fn(),
      undo: vi.fn(),
    },
  };
});

const ARTICLE_ID = "art-1";
const ARTICLE_BODY = "记忆系统需要给作者一个可以取舍的界面。\n第二段用于局部改写。";
const CHAT_ID = "chat-writing";
const COLLABORATION_ID = "c1";
const OUTLINE_ARTIFACT_ID = "art-outline";

/** 只有每聊天的帧订阅是面板需要的通道（沿用 useTeamProgress 的假 client 手法）。 */
class FakeClient implements WebUIMutationTransport {
  private handlers = new Map<string, (event: unknown) => void>();
  readonly mutations: Array<{ action: string; payload: Record<string, unknown> }> = [];

  onChat(chatId: string, handler: (event: unknown) => void): () => void {
    this.handlers.set(chatId, handler);
    return () => this.handlers.delete(chatId);
  }

  emit(chatId: string, event: unknown): void {
    this.handlers.get(chatId)?.(event);
  }

  has(chatId: string): boolean {
    return this.handlers.has(chatId);
  }

  async requestMutation<T>(action: string, payload?: Record<string, unknown>): Promise<T> {
    this.mutations.push({ action, payload: payload ?? {} });
    return {} as T;
  }
}

/**
 * 快照按 `collabApi.getSnapshot` 的**返回口径**（camelCase）构造：
 * snake_case → camelCase 的映射已由 L3（`use-writing-collab` 的依赖模块）负责，
 * 面板这一层只消费映射后的类型。
 */
function snapshot(overrides: Partial<CollabSnapshot> = {}): CollabSnapshot {
  return {
    collaboration: {
      id: COLLABORATION_ID,
      articleId: ARTICLE_ID,
      chatId: CHAT_ID,
      sessionKey: `websocket:${CHAT_ID}`,
      goal: { topic: "记忆系统" },
      status: "waiting_author",
      stage: "outline",
      activeBatchId: "b1",
      cancelGeneration: 0,
      version: 5,
      seq: 12,
      lastHandoffId: "h1",
      createdAt: "2026-10-17T08:00:00+00:00",
      updatedAt: "2026-10-17T08:05:00+00:00",
    },
    batches: [],
    artifacts: [
      {
        id: OUTLINE_ARTIFACT_ID,
        collaborationId: COLLABORATION_ID,
        batchId: "b1",
        kind: "outline",
        version: 2,
        summary: "Three sections",
      },
    ],
    handoffs: [
      {
        id: "h1",
        collaborationId: COLLABORATION_ID,
        batchId: "b1",
        artifactId: OUTLINE_ARTIFACT_ID,
        kind: "outline_confirm",
        artifactVersion: 2,
        status: "pending",
        createdAt: "2026-10-17T08:04:30+00:00",
        resolvedAt: null,
      },
    ],
    suggestions: [],
    lastVersion: 5,
    lastSeq: 12,
    nextActions: ["draft"],
    blockedReason: null,
    ...overrides,
  };
}

function outlineArtifact(overrides: Partial<CollabArtifact> = {}): CollabArtifact {
  return {
    id: OUTLINE_ARTIFACT_ID,
    collaborationId: COLLABORATION_ID,
    batchId: "b1",
    attempt: 1,
    kind: "outline",
    roleId: "writing-architect",
    version: 2,
    content: {
      title: "Outline",
      outline: [
        { id: "o1", text: "Why memory is hard", order: 0 },
        { id: "o2", text: "One concrete example", order: 1 },
      ],
    },
    contentDigest: "d-outline",
    sourceIds: [],
    gaps: [],
    status: "ready",
    createdAt: "2026-10-17T08:04:00+00:00",
    ...overrides,
  };
}

function progressFrame(body: Record<string, unknown>): Record<string, unknown> {
  return { event: "writing_collab", chat_id: CHAT_ID, ...body };
}

function artifactFrame(body: Record<string, unknown>): Record<string, unknown> {
  return { event: "writing_artifact", chat_id: CHAT_ID, ...body };
}

function setupPanel(
  options: {
    snapshot?: CollabSnapshot;
    onStart?: () => Promise<unknown>;
    selection?: { start: number; end: number } | null;
  } = {},
) {
  const client = new FakeClient();
  const fixture = options.snapshot ?? snapshot();
  vi.mocked(collabApi.getSnapshot).mockResolvedValue(fixture);
  vi.mocked(collabApi.getArtifact).mockResolvedValue(outlineArtifact());
  const startResult: Awaited<ReturnType<typeof collabApi.start>> = {
    collaborationId: COLLABORATION_ID,
    batchId: "b2",
    status: "queued",
    stage: "outline",
    sessionKey: `websocket:${CHAT_ID}`,
    activeBatchId: "b2",
  };
  vi.mocked(collabApi.start).mockImplementation(
    options.onStart
      ? () => options.onStart!() as ReturnType<typeof collabApi.start>
      : () => Promise.resolve(startResult),
  );
  const onSelectionChange = vi.fn();
  const tree = (articleId: string = ARTICLE_ID) => (
    <WritingPartnerPanel
      articleId={articleId}
      articleTitle="记忆系统"
      articleBody={ARTICLE_BODY}
      articleRevision={3}
      snapshotDigest="digest-1"
      chatId={CHAT_ID}
      client={client}
      api={collabApi}
      selection={options.selection ?? null}
      onSelectionChange={onSelectionChange}
    />
  );
  const view = render(tree());
  return { ...view, client, onSelectionChange, fixture, tree };
}

beforeEach(() => {
  vi.mocked(collabApi.getSnapshot).mockReset();
  vi.mocked(collabApi.getArtifact).mockReset();
  vi.mocked(collabApi.start).mockReset();
  vi.mocked(collabApi.reply).mockReset();
  vi.mocked(collabApi.confirm).mockReset();
  vi.mocked(collabApi.cancel).mockReset();
  vi.mocked(collabApi.retry).mockReset();
  vi.mocked(collabApi.apply).mockReset();
  vi.mocked(collabApi.undo).mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("WritingPartnerPanel structure", () => {
  it("test_panel_renders_without_team_selector", async () => {
    setupPanel();
    await waitFor(() => expect(collabApi.getSnapshot).toHaveBeenCalledWith(ARTICLE_ID));

    expect(screen.getByRole("heading", { name: /Writing partner/ })).toBeInTheDocument();
    expect(screen.getByText("Built-in")).toBeInTheDocument();
    // 阶段要看得见，但只有服务端说过的那一个。
    expect(document.querySelector("[data-partner-stage]")).toHaveTextContent("Outline");
    expect(screen.getByRole("status")).toHaveTextContent("Waiting for you");

    // 团队 / 角色 / Skill 选择控件一个都不许有。
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /select a team|choose a team/i })).toBeNull();
    expect(screen.queryAllByRole("button", { name: /workflow/i })).toHaveLength(0);
    expect(screen.queryAllByText(/select a team/i)).toHaveLength(0);
  });

  it("test_primary_action_uses_article_context", async () => {
    const user = userEvent.setup();
    const { client } = setupPanel();

    // nextActions=["draft"] → 主操作就是「写初稿」，不需要作者另填需求。
    await user.click(await screen.findByRole("button", { name: "Write a draft" }));

    await waitFor(() => expect(collabApi.start).toHaveBeenCalledTimes(1));
    const request = vi.mocked(collabApi.start).mock.calls[0][0];
    expect(request.action).toBe("draft");
    expect(request.articleId).toBe(ARTICLE_ID);
    expect(request.chatId).toBe(CHAT_ID);
    expect(request.baseRevision).toBe(3);
    expect(request.snapshotDigest).toBe("digest-1");
    expect(request.clientRequestId).toEqual(expect.any(String));
    // 主题已填就直接发起，不要求作者填团队需求表。
    expect(screen.queryByLabelText(/team requirements/i)).toBeNull();
    expect(client.has(CHAT_ID)).toBe(true);
  });
});

describe("WritingPartnerPanel artifacts and progress", () => {
  it("test_artifact_cards_actions", async () => {
    const user = userEvent.setup();
    setupPanel();

    const points = await screen.findAllByRole("textbox", { name: "Outline point" });
    expect(points[0]).toHaveValue("Why memory is hard");

    await user.click(screen.getByRole("button", { name: "Save outline and continue" }));
    await waitFor(() => expect(collabApi.confirm).toHaveBeenCalledTimes(1));
    expect(vi.mocked(collabApi.confirm).mock.calls[0][0]).toMatchObject({
      collaborationId: COLLABORATION_ID,
      handoffId: "h1",
      // 契约 §10 裁决 3：`expected_handoff_version` 是交接自身的整数 artifact_version，
      // 直接透传；fixture 里它（2）与协作版本（5）刻意不同。
      expectedHandoffVersion: 2,
      outline: [
        { id: "o1", text: "Why memory is hard", order: 0 },
        { id: "o2", text: "One concrete example", order: 1 },
      ],
    });
  });

  it("test_confirm_sends_the_handoff_artifact_version", async () => {
    const user = userEvent.setup();
    const panel = setupPanel();
    await screen.findAllByRole("textbox", { name: "Outline point" });

    // 冻结口径：整数 artifact_version 直接透传（不用协作 lastVersion / status）。
    const handoff = panel.fixture.handoffs[0];
    expect(handoff.artifactVersion).toBe(2);
    expect(panel.fixture.lastVersion).toBe(5);
    expect(panel.fixture.collaboration?.version).toBe(5);

    await user.click(screen.getByRole("button", { name: "Save outline and continue" }));

    await waitFor(() => expect(collabApi.confirm).toHaveBeenCalledTimes(1));
    const request = vi.mocked(collabApi.confirm).mock.calls[0][0];
    expect(request.expectedHandoffVersion).toBe(handoff.artifactVersion);
    // 回归护栏：绝不能把协作版本混进交接 CAS（那会稳定 409）。
    expect(request.expectedHandoffVersion).not.toBe(panel.fixture.lastVersion);
  });

  it("test_task_progress_states", async () => {
    const { client } = setupPanel();
    await screen.findAllByRole("textbox", { name: "Outline point" });

    await act(async () => {
      client.emit(
        CHAT_ID,
        progressFrame({
          collaboration_id: COLLABORATION_ID,
          status: "running",
          stage: "research",
          active_batch_id: "b2",
          version: 6,
          seq: 13,
        }),
      );
    });

    expect(await screen.findByText("Researching")).toBeInTheDocument();
    // 没有伪百分比，也没有预计完成时间。
    expect(screen.queryByText(/\d+\s*%/)).toBeNull();
    expect(screen.queryByText(/remaining|eta|estimated/i)).toBeNull();
  });

  it("test_next_step_always_present", async () => {
    const user = userEvent.setup();
    const { client } = setupPanel();
    await screen.findAllByRole("textbox", { name: "Outline point" });

    const nextStep = screen.getByRole("button", { name: "Write from this outline" });
    expect(nextStep).toBeInTheDocument();

    await act(async () => {
      client.emit(
        CHAT_ID,
        progressFrame({
          collaboration_id: COLLABORATION_ID,
          status: "completed",
          stage: "draft",
          active_batch_id: null,
          version: 7,
          seq: 14,
        }),
      );
    });
    await waitFor(() => expect(screen.queryByText("Running")).toBeNull());

    // 任务收口后仍然给一个明确下一步，而不是泛泛的「还需要什么帮助」。
    await user.click(screen.getByRole("button", { name: "Write from this outline" }));
    await waitFor(() => expect(collabApi.start).toHaveBeenCalledTimes(1));
    expect(vi.mocked(collabApi.start).mock.calls[0][0].action).toBe("draft");
    expect(screen.queryByText(/what else can I help/i)).toBeNull();
  });
});

describe("WritingPartnerPanel author input", () => {
  it("test_selection_required_for_rewrite", async () => {
    setupPanel();
    await screen.findAllByRole("textbox", { name: "Outline point" });

    // 没有选区：提示要先划范围，且绝不发请求。
    expect(screen.getByRole("button", { name: "Rewrite selection" })).toBeDisabled();
    expect(screen.getByText("Select some text first")).toBeInTheDocument();
    expect(collabApi.start).not.toHaveBeenCalled();
  });

  it("test_selection_enables_rewrite", async () => {
    const user = userEvent.setup();
    setupPanel({ selection: { start: 0, end: 4 } });
    await screen.findAllByRole("textbox", { name: "Outline point" });

    const rewrite = screen.getByRole("button", { name: "Rewrite selection" });
    expect(rewrite).toBeEnabled();
    await user.click(rewrite);

    await waitFor(() => expect(collabApi.start).toHaveBeenCalledTimes(1));
    expect(vi.mocked(collabApi.start).mock.calls[0][0]).toMatchObject({
      action: "rewrite_selection",
      selection: { start: 0, end: 4, text: "记忆系统" },
    });
  });

  it("test_error_preserves_input", async () => {
    const user = userEvent.setup();
    setupPanel({
      selection: { start: 0, end: 4 },
      onStart: async () => {
        throw new ApiError(400, "collaboration context too large; shrink the selection or materials");
      },
    });
    await screen.findAllByRole("textbox", { name: "Outline point" });

    const input = screen.getByRole("textbox", { name: "Message the editor" });
    await user.type(input, "Keep my voice");
    await user.click(screen.getByRole("button", { name: "Rewrite selection" }));

    expect(
      await screen.findByText("That request is too large. Shorten it and try again."),
    ).toBeInTheDocument();
    // 失败保留作者输入与选区。
    expect(input).toHaveValue("Keep my voice");
    expect(vi.mocked(collabApi.start).mock.calls[0][0].selection).toEqual({
      start: 0,
      end: 4,
      text: "记忆系统",
    });
  });

  // security review F5/F6：`client_request_id` 必须带文章身份，且失败后必须释放，
  // 否则「失败 → 改载荷/切文章 → 重试」会复用同一 id 被服务端判 RequestReplayConflict。
  it("test_request_id_is_article_scoped_and_released_after_failure", async () => {
    const user = userEvent.setup();
    const ids = ["req-1", "req-2", "req-3", "req-4"];
    let cursor = 0;
    const originalRandomUUID = crypto.randomUUID;
    Object.defineProperty(crypto, "randomUUID", {
      value: () => ids[cursor++] ?? `req-overflow-${cursor}`,
      configurable: true,
    });
    try {
      const panel = setupPanel({
        onStart: async () => {
          throw new ApiError(503, "writing collaboration service is not configured");
        },
      });
      await screen.findAllByRole("textbox", { name: "Outline point" });
      // 「写初稿」在快捷任务里也有同名按钮，主操作按 data 属性定位。
      const primary = () =>
        document.querySelector("[data-partner-primary]") as HTMLButtonElement;

      await user.click(primary());
      await waitFor(() => expect(collabApi.start).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(primary()).toBeEnabled());
      await user.click(primary());
      await waitFor(() => expect(collabApi.start).toHaveBeenCalledTimes(2));

      const first = vi.mocked(collabApi.start).mock.calls[0][0].clientRequestId;
      const second = vi.mocked(collabApi.start).mock.calls[1][0].clientRequestId;
      expect(first).toBe(ids[0]);
      // 失败路径释放：同一篇文章重试必须换新 id。
      expect(second).not.toBe(first);
      expect(second).toBe(ids[1]);

      // 切到另一篇文章后同样不得复用旧 id（键里含 articleId）。
      panel.rerender(panel.tree("art-2"));
      await waitFor(() => expect(collabApi.getSnapshot).toHaveBeenCalledWith("art-2"));
      await screen.findAllByRole("textbox", { name: "Outline point" });
      await user.click(primary());
      await waitFor(() => expect(collabApi.start).toHaveBeenCalledTimes(3));
      const third = vi.mocked(collabApi.start).mock.calls[2][0].clientRequestId;
      expect(third).not.toBe(first);
      expect(third).not.toBe(second);
      expect(vi.mocked(collabApi.start).mock.calls[2][0].articleId).toBe("art-2");
    } finally {
      Object.defineProperty(crypto, "randomUUID", {
        value: originalRandomUUID,
        configurable: true,
      });
    }
  });

  it("test_focus_not_stolen_by_stream", async () => {
    const user = userEvent.setup();
    const { client } = setupPanel();
    await screen.findAllByRole("textbox", { name: "Outline point" });

    const input = screen.getByRole("textbox", { name: "Message the editor" });
    await user.click(input);
    await user.type(input, "Half a thought");
    expect(input).toHaveFocus();

    await act(async () => {
      client.emit(
        CHAT_ID,
        artifactFrame({
          collaboration_id: COLLABORATION_ID,
          artifact_id: "art-draft",
          kind: "draft",
          version: 1,
          summary: "Draft section 1",
        }),
      );
      client.emit(
        CHAT_ID,
        progressFrame({
          collaboration_id: COLLABORATION_ID,
          status: "running",
          stage: "draft",
          active_batch_id: "b2",
          version: 6,
          seq: 15,
        }),
      );
    });

    await waitFor(() => expect(screen.getByText("Drafting")).toBeInTheDocument());
    expect(input).toHaveFocus();
    expect(input).toHaveValue("Half a thought");

    // 切换产物也要保留已输入反馈。
    await act(async () => {
      client.emit(
        CHAT_ID,
        progressFrame({
          collaboration_id: COLLABORATION_ID,
          status: "running",
          stage: "review",
          active_batch_id: "b2",
          version: 7,
          seq: 16,
        }),
      );
    });
    await waitFor(() => expect(screen.getByText("Reviewing")).toBeInTheDocument());
    expect(input).toHaveValue("Half a thought");
  });

  it("test_a11y_live_region_and_keyboard", async () => {
    const user = userEvent.setup();
    const { client } = setupPanel();
    await screen.findAllByRole("textbox", { name: "Outline point" });

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent("Waiting for you");

    // 关键动作可以键盘触达。
    expect(screen.getByRole("button", { name: "Save outline and continue" })).toBeEnabled();
    await user.tab();
    expect(document.activeElement).not.toBe(document.body);

    await act(async () => {
      client.emit(
        CHAT_ID,
        progressFrame({
          collaboration_id: COLLABORATION_ID,
          status: "running",
          stage: "outline",
          active_batch_id: "b2",
          version: 6,
          seq: 17,
        }),
      );
    });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Outlining"));
  });
});

// ---------------------------------------------------------------------------
// 错误 → 文案（契约 §4：纯文本 message 才是判定依据；400 不是一个「错误种类」）
// ---------------------------------------------------------------------------

describe("collab error labels", () => {
  it("test_400_field_required_is_not_selection_required", () => {
    // 缺字段是 400，但绝不是「需要选区」。
    for (const message of [
      "article_id is required",
      "client_request_id is required",
      "chat_id is required",
      "handoff_id is required",
    ]) {
      expect(errorKindForError(new ApiError(400, message))).toBe("unknown");
      expect(errorLabelKey(new ApiError(400, message)))
        .toBe("creative.partner.ui.errorUnknown");
    }
  });

  it("test_400_invalid_suggestion_target_is_not_selection_required", () => {
    for (const code of [
      "out_of_range",
      "expected_before_mismatch",
      "insert_range_not_empty",
      "outline_item_not_found",
      "unsupported_kind",
    ]) {
      expect(errorKindForError(new ApiError(400, `suggestion target is invalid: ${code}`)))
        .toBe("unknown");
    }
  });

  it("test_400_selection_required_still_maps", () => {
    expect(errorKindForError(new ApiError(400, "collaboration target selection is required")))
      .toBe("selectionRequired");
    expect(errorLabelKey(new ApiError(400, "collaboration target selection is required")))
      .toBe("creative.partner.error.selectionRequired");
  });

  it("test_message_beats_status_and_other_400s_stay_generic", () => {
    expect(errorKindForError(new ApiError(400, "collaboration context too large; shrink the selection or materials")))
      .toBe("requestTooLarge");
    expect(errorKindForError(new ApiError(400, "article material not found: m1"))).toBe("unknown");
    // 409/413/503 的既有稳定口径不变。
    expect(errorKindForError(new ApiError(409, "handoff version conflict expected=2 actual=3")))
      .toBe("versionConflict");
    expect(errorKindForError(new ApiError(409, "suggestion is not pending"))).toBe("notPending");
    expect(errorKindForError(new ApiError(413, "collaboration context too large")))
      .toBe("requestTooLarge");
    expect(errorKindForError(new ApiError(503, "writing collaboration service is not configured")))
      .toBe("serviceUnavailable");
    expect(errorKindForError(new ApiError(404, "collaboration not found"))).toBe("unknown");
    expect(errorKindForError(new Error("boom"))).toBe("unknown");
  });
});
