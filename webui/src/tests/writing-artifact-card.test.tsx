// 产物卡（plan Task 12 / spec §12.3 表格）。
//
// 覆盖口径：
// 1. 六类产物各自的主操作，以及它们的边界（提纲可编辑、改写真要原文/改写/理由、
//    初稿先查看再插入、审稿默认 ≤5 项）。
// 2. 采纳/加入素材/保存提纲都是**显式作者动作**：卡片自己不写文章。
// 3. 失败或待处理时卡片不消失、按钮不先隐藏。
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ArtifactCard } from "@/components/creative/ArtifactCard";
import type { ArtifactKind, CollabArtifact } from "@/lib/writing-collab-api";

function artifact(overrides: Partial<CollabArtifact> = {}): CollabArtifact {
  return {
    id: "art-1",
    collaborationId: "c1",
    batchId: "b1",
    attempt: 1,
    kind: "brief",
    roleId: "writing-editor",
    version: 1,
    content: {},
    contentDigest: "d1",
    sourceIds: [],
    gaps: [],
    status: "ready",
    createdAt: "2026-10-17T08:00:00+00:00",
    ...overrides,
  };
}

function renderCard(
  kind: ArtifactKind,
  content: unknown,
  props: Partial<Parameters<typeof ArtifactCard>[0]> = {},
) {
  const handlers = {
    onUseAngle: vi.fn(),
    onAddMaterial: vi.fn(),
    onConfirmOutline: vi.fn(),
    onInsertDraft: vi.fn(),
    onApplyRewrite: vi.fn(),
    onRejectRewrite: vi.fn(),
    onRetryRewrite: vi.fn(),
    onApplyIssue: vi.fn(),
  };
  const { status, ...rest } = props;
  const view = render(
    <ArtifactCard
      artifact={artifact({ kind, content, status: status ?? "ready" })}
      locale="en"
      {...handlers}
      {...rest}
    />,
  );
  return { ...view, ...handlers };
}

const FIVE_ISSUES = [
  { id: "i1", severity: "high", title: "Claim is unsupported", detail: "Add a source." },
  { id: "i2", severity: "high", title: "Section 2 repeats itself", detail: "Merge the two paragraphs." },
  { id: "i3", severity: "medium", title: "Passive voice", detail: "Rewrite in active voice." },
  { id: "i4", severity: "medium", title: "Terminology drifts", detail: "Pick one spelling." },
  { id: "i5", severity: "low", title: "Long sentence", detail: "Split it." },
];

const SIX_ISSUES = [
  ...FIVE_ISSUES,
  { id: "i6", severity: "low", title: "Missing closing line", detail: "Add a takeaway." },
];

describe("ArtifactCard actions", () => {
  it("test_artifact_cards_actions", async () => {
    const user = userEvent.setup();

    const brief = renderCard("brief", {
      angles: [{ title: "Start from the bug report", rationale: "Readers meet the problem first." }],
    });
    await user.click(screen.getByRole("button", { name: "Use this angle" }));
    expect(brief.onUseAngle).toHaveBeenCalledTimes(1);
    expect(brief.onAddMaterial).not.toHaveBeenCalled();
    brief.unmount();

    const materials = renderCard("material_list", {
      materials: [
        {
          id: "m1",
          title: "SQLite WAL notes",
          excerpt: "WAL keeps readers from blocking the writer.",
          source: "https://sqlite.org/wal.html",
        },
      ],
    });
    expect(screen.getByText("SQLite WAL notes")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add material" }));
    expect(materials.onAddMaterial).toHaveBeenCalledWith(
      expect.objectContaining({ title: "SQLite WAL notes" }),
    );
    materials.unmount();

    const outline = renderCard("outline", {
      outline: [
        { id: "o1", text: "Why memory is hard", order: 0 },
        { id: "o2", text: "One concrete example", order: 1 },
      ],
    });
    const points = screen.getAllByRole("textbox", { name: "Outline point" });
    expect(points[0]).toHaveValue("Why memory is hard");
    await user.click(screen.getByRole("button", { name: "Save outline and continue" }));
    expect(outline.onConfirmOutline).toHaveBeenCalledWith([
      { id: "o1", text: "Why memory is hard", order: 0 },
      { id: "o2", text: "One concrete example", order: 1 },
    ]);
    outline.unmount();

    const draft = renderCard("draft", { sections: [{ id: "s1", title: "Intro", body: "Hello." }] });
    // 初稿必须先给作者看清楚：不点「查看并插入」之前正文只有作者读过一次。
    expect(screen.queryByText("Hello.")).toBeNull();
    await user.click(screen.getByRole("button", { name: "View and insert" }));
    expect(screen.getByText("Hello.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Insert into article" }));
    expect(draft.onInsertDraft).toHaveBeenCalledWith(
      expect.objectContaining({ sections: [expect.objectContaining({ id: "s1" })] }),
    );
    draft.unmount();

    const rewrite = renderCard("rewrite", {
      before: "原始段落。",
      after: "改写段落。",
      summary: "Shorter and more concrete.",
      notes: "Kept the author's voice.",
    });
    expect(screen.getByText("Before")).toBeInTheDocument();
    expect(screen.getByText("原始段落。")).toBeInTheDocument();
    expect(screen.getByText("After")).toBeInTheDocument();
    expect(screen.getByText("改写段落。")).toBeInTheDocument();
    expect(screen.getByText("Shorter and more concrete.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Apply rewrite" }));
    expect(rewrite.onApplyRewrite).toHaveBeenCalledTimes(1);
    rewrite.unmount();

    const review = renderCard("review", { issues: FIVE_ISSUES });
    await user.click(screen.getByRole("button", { name: "View issues" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByText("Claim is unsupported")).toBeInTheDocument();
    review.unmount();
  });

  it("test_review_highlights_five_issues_then_expands", async () => {
    const user = userEvent.setup();
    renderCard("review", { issues: SIX_ISSUES });

    expect(screen.getAllByRole("listitem")).toHaveLength(5);
    expect(screen.queryByText("Missing closing line")).toBeNull();

    await user.click(screen.getByRole("button", { name: /Show 1 more/ }));
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
    expect(screen.getByText("Missing closing line")).toBeInTheDocument();
  });

  it("test_prompt_draft_actions_hidden_while_pending", async () => {
    const { onConfirmOutline } = renderCard(
      "outline",
      { outline: [{ id: "o1", text: "Why memory is hard", order: 0 }] },
      { status: "pending" },
    );

    expect(screen.getByText("Waiting for your decision")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save outline and continue" })).toBeNull();
    // 待处理不是错误：卡片仍在，作者还能读内容。
    expect(screen.getAllByRole("textbox", { name: "Outline point" })[0]).toHaveValue(
      "Why memory is hard",
    );
    expect(onConfirmOutline).not.toHaveBeenCalled();
  });

  it("test_card_survives_failed_status", () => {
    renderCard(
      "outline",
      { outline: [{ id: "o1", text: "Why memory is hard", order: 0 }] },
      { status: "failed" },
    );
    expect(screen.getByText("This item failed. Try again.")).toBeInTheDocument();
    expect(screen.getAllByRole("textbox", { name: "Outline point" })[0]).toHaveValue(
      "Why memory is hard",
    );
  });

  it("test_rewrite_shows_reason_before_apply", async () => {
    const user = userEvent.setup();
    const { onRejectRewrite, onRetryRewrite } = renderCard("rewrite", {
      before: "原文。",
      after: "改写。",
      summary: "Why it changed.",
    });
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Try another rewrite" }));
    expect(onRejectRewrite).toHaveBeenCalledTimes(1);
    expect(onRetryRewrite).toHaveBeenCalledTimes(1);
  });
});
