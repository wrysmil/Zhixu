// 创作搭档的**单一映射出口**（契约 §8 冻结键前缀 `creative.partner.`）。
//
// 面板、产物卡、hook 只从这里取 key：状态/阶段/动作/产物类型/交接/错误/下一步
// 各有一张表。新增展示维度时改这里，不在组件里拼 key 字符串。
//
// 注意：契约 §8 冻结的是**前缀内的已列键**；本文件另有一批 `creative.partner.ui.*`
// 键（本地 UI 文案）尚未同步进 10 个 locale，因此组件一律用 `t(key, { defaultValue })`
// 兜底，绝不硬编码中文。
import type { ArtifactKind, CollabAction, CollabStage, CollabStatus } from "@/lib/writing-collab-api";

export const PARTNER_TITLE_KEY = "creative.partner.title";
export const PARTNER_BUILTIN_BADGE_KEY = "creative.partner.builtinBadge";
export const PARTNER_PROCESS_TOGGLE_KEY = "creative.partner.process.toggle";
export const PARTNER_INPUT_PLACEHOLDER_KEY = "creative.partner.input.placeholder";
export const PARTNER_REQUIREMENTS_TOGGLE_KEY = "creative.partner.requirements.toggle";

/** 阶段文案：`collaboration.stage` 与 `collaboration.status` 共用 `stage.*` 表。 */
export const STAGE_LABEL_KEY: Record<CollabStage, string> = {
  "": "creative.partner.stage.idle",
  brief: "creative.partner.stage.queued",
  research: "creative.partner.stage.running",
  outline: "creative.partner.stage.running",
  draft: "creative.partner.stage.running",
  review: "creative.partner.stage.running",
  rewrite: "creative.partner.stage.running",
};

export function stageLabelKey(stage: CollabStage): string {
  return STAGE_LABEL_KEY[stage];
}

export function statusLabelKey(status: CollabStatus): string {
  return `creative.partner.stage.${status}`;
}

export const ACTION_LABEL_KEY: Record<CollabAction, string> = {
  start_full: "creative.partner.action.start_full",
  ideate: "creative.partner.action.ideate",
  research: "creative.partner.action.research",
  outline: "creative.partner.action.outline",
  draft: "creative.partner.action.draft",
  rewrite_selection: "creative.partner.action.rewrite_selection",
  review_all: "creative.partner.action.review_all",
  reply: "creative.partner.action.reply",
};

export function actionLabelKey(action: CollabAction): string {
  return ACTION_LABEL_KEY[action];
}

export const ARTIFACT_LABEL_KEY: Record<ArtifactKind, string> = {
  brief: "creative.partner.artifact.brief",
  material_list: "creative.partner.artifact.material_list",
  outline: "creative.partner.artifact.outline",
  draft: "creative.partner.artifact.draft",
  rewrite: "creative.partner.artifact.rewrite",
  review: "creative.partner.artifact.review",
};

export function artifactLabelKey(kind: ArtifactKind): string {
  return ARTIFACT_LABEL_KEY[kind];
}

/** 六类产物各自的主操作（spec §12.3）。 */
export const ARTIFACT_ACTION_KEY: Record<ArtifactKind, string> = {
  brief: "creative.partner.artifactAction.useAngle",
  material_list: "creative.partner.artifactAction.addMaterial",
  outline: "creative.partner.ui.outlineConfirm",
  draft: "creative.partner.ui.viewAndInsert",
  rewrite: "creative.partner.artifactAction.applyRewrite",
  review: "creative.partner.artifactAction.viewIssues",
};

export function artifactActionLabelKey(kind: ArtifactKind): string {
  return ARTIFACT_ACTION_KEY[kind];
}

/** 作者能读懂的任务状态（spec §3：不逐 token 宣读，也没有伪百分比）。 */
export type ProgressDescriptor =
  | { kind: "stage"; key: string }
  | { kind: "handoff"; key: string }
  | { kind: "stopped"; key: string };

export function progressDescriptor(
  status: CollabStatus,
  stage: CollabStage,
): ProgressDescriptor {
  if (status === "running" || status === "queued") {
    if (stage === "research") return { kind: "stage", key: "creative.partner.progress.researching" };
    if (stage === "outline") return { kind: "stage", key: "creative.partner.progress.outlining" };
    if (stage === "draft" || stage === "rewrite") {
      return { kind: "stage", key: "creative.partner.progress.drafting" };
    }
    if (stage === "review") return { kind: "stage", key: "creative.partner.progress.reviewing" };
    return { kind: "stage", key: statusLabelKey(status) };
  }
  if (status === "waiting_author") {
    return { kind: "stage", key: "creative.partner.progress.waitingAuthor" };
  }
  if (status === "completed") return { kind: "stage", key: statusLabelKey(status) };
  return { kind: "stopped", key: "creative.partner.progress.stopped" };
}

/** 交接状态文案（契约 §5 的 `writing_handoff`）。 */
export const HANDOFF_LABEL_KEY: Record<string, string> = {
  outline_confirm: "creative.partner.handoff.outline_confirm",
  materials_request: "creative.partner.handoff.materials_request",
  final_accept: "creative.partner.handoff.final_accept",
};

export function handoffLabelKey(kind: string): string {
  return HANDOFF_LABEL_KEY[kind] ?? "creative.partner.ui.handoffUnknown";
}

/**
 * 错误 → i18n key。优先按 HTTP 状态（契约 §4 的稳定口径），
 * 再按服务端纯文本 message 的特征串兜底，认不出的一律走通用错误。
 */
export type CollabErrorKind =
  | "selectionRequired"
  | "versionConflict"
  | "notPending"
  | "serviceUnavailable"
  | "requestTooLarge"
  | "unknown";

const STATUS_ERROR_KIND: Record<number, CollabErrorKind> = {
  400: "selectionRequired",
  409: "versionConflict",
  413: "requestTooLarge",
  503: "serviceUnavailable",
};

const MESSAGE_ERROR_KIND: Array<[RegExp, CollabErrorKind]> = [
  [/selection is required/i, "selectionRequired"],
  [/too large/i, "requestTooLarge"],
  [/version conflict/i, "versionConflict"],
  [/context too large/i, "requestTooLarge"],
  [/not pending|no longer pending/i, "notPending"],
  [/not configured|unavailable/i, "serviceUnavailable"],
];

export function errorKindForError(error: unknown): CollabErrorKind {
  const status = (
    typeof error === "object"
    && error !== null
    && "status" in error
    && typeof (error as { status?: unknown }).status === "number"
  ) ? (error as { status: number }).status : 0;
  const message = error instanceof Error ? error.message : "";
  for (const [pattern, kind] of MESSAGE_ERROR_KIND) {
    if (pattern.test(message)) return kind;
  }
  return STATUS_ERROR_KIND[status] ?? "unknown";
}

export function errorLabelKey(error: unknown): string {
  const kind = errorKindForError(error);
  if (kind === "unknown") return "creative.partner.ui.errorUnknown";
  return `creative.partner.error.${kind}`;
}

/** 任务收口的下一步（spec §12.2 / 契约 §8 `next.*`）。 */
export const NEXT_LABEL_KEY: Record<string, string> = {
  writeFromOutline: "creative.partner.next.writeFromOutline",
  insertToArticle: "creative.partner.next.insertToArticle",
  viewChanges: "creative.partner.next.viewChanges",
  addMaterials: "creative.partner.next.addMaterials",
};

export type NextStepId = keyof typeof NEXT_LABEL_KEY;

export function nextLabelKey(id: string): string {
  return NEXT_LABEL_KEY[id] ?? "creative.partner.ui.nextStepUnknown";
}

/** 用户可见文案的英文兜底：i18n 资源没同步到某个 locale 时不至于显示 key。 */
export const PARTNER_DEFAULTS: Record<string, string> = {
  [PARTNER_TITLE_KEY]: "Writing partner",
  [PARTNER_BUILTIN_BADGE_KEY]: "Built-in",
  [PARTNER_PROCESS_TOGGLE_KEY]: "View collaboration process",
  [PARTNER_INPUT_PLACEHOLDER_KEY]: "Keep my voice; make section 2 more concrete…",
  [PARTNER_REQUIREMENTS_TOGGLE_KEY]: "This article's requirements",
  "creative.partner.stage.idle": "Idle",
  "creative.partner.stage.queued": "Queued",
  "creative.partner.stage.running": "Running",
  "creative.partner.stage.waiting_author": "Waiting for you",
  "creative.partner.stage.completed": "Completed",
  "creative.partner.stage.failed": "Failed",
  "creative.partner.stage.cancelled": "Cancelled",
  "creative.partner.stage.interrupted": "Interrupted",
  "creative.partner.action.start_full": "Run the full pipeline",
  "creative.partner.action.ideate": "Find an angle",
  "creative.partner.action.research": "Research",
  "creative.partner.action.outline": "Build an outline",
  "creative.partner.action.draft": "Write a draft",
  "creative.partner.action.rewrite_selection": "Rewrite selection",
  "creative.partner.action.review_all": "Review everything",
  "creative.partner.action.reply": "Reply",
  "creative.partner.artifact.brief": "Brief",
  "creative.partner.artifact.material_list": "Materials",
  "creative.partner.artifact.outline": "Outline",
  "creative.partner.artifact.draft": "Draft",
  "creative.partner.artifact.rewrite": "Rewrite",
  "creative.partner.artifact.review": "Review",
  "creative.partner.artifactAction.useAngle": "Use this angle",
  "creative.partner.artifactAction.addMaterial": "Add material",
  "creative.partner.artifactAction.writeFromOutline": "Write from outline",
  "creative.partner.artifactAction.insertDraft": "Insert draft",
  "creative.partner.artifactAction.applyRewrite": "Apply rewrite",
  "creative.partner.artifactAction.viewIssues": "View issues",
  "creative.partner.handoff.outline_confirm": "Outline ready — confirm to continue",
  "creative.partner.handoff.materials_request": "More materials needed",
  "creative.partner.handoff.final_accept": "Final result ready — accept to finish",
  "creative.partner.next.writeFromOutline": "Write from this outline",
  "creative.partner.next.insertToArticle": "Insert into article",
  "creative.partner.next.viewChanges": "View changes",
  "creative.partner.next.addMaterials": "Add materials",
  "creative.partner.progress.researching": "Researching",
  "creative.partner.progress.outlining": "Outlining",
  "creative.partner.progress.drafting": "Drafting",
  "creative.partner.progress.reviewing": "Reviewing",
  "creative.partner.progress.waitingAuthor": "Waiting for you",
  "creative.partner.progress.stopped": "Stopped",
  "creative.partner.error.selectionRequired": "Select some text first",
  "creative.partner.error.versionConflict": "The article changed. Reload and try again.",
  "creative.partner.error.notPending": "This item is no longer waiting for you.",
  "creative.partner.error.serviceUnavailable": "The writing service is unavailable. Try again later.",
  "creative.partner.error.requestTooLarge": "That request is too large. Shorten it and try again.",
  "creative.partner.ui.errorUnknown": "The writing partner could not handle that. Try again.",
  "creative.partner.ui.outlineConfirm": "Save outline and continue",
  "creative.partner.ui.viewAndInsert": "View and insert",
  "creative.partner.ui.viewOutline": "View outline",
  "creative.partner.ui.viewMaterials": "View materials",
  "creative.partner.ui.insertIntoArticle": "Insert into article",
  "creative.partner.ui.insertSection": "Insert this section",
  "creative.partner.ui.replaceArticle": "Replace the empty draft",
  "creative.partner.ui.copyRewrite": "Copy rewrite",
  "creative.partner.ui.rejectRewrite": "Reject",
  "creative.partner.ui.retryRewrite": "Try another rewrite",
  "creative.partner.ui.showMoreIssues": "Show {{count}} more",
  "creative.partner.ui.showFewerIssues": "Show fewer",
  "creative.partner.ui.dismissMaterial": "Remove from this selection",
  "creative.partner.ui.handoffUnknown": "Waiting for your decision",
  "creative.partner.ui.nextStepUnknown": "Open the collaboration process for the next step",
  "creative.partner.ui.outlinePoint": "Outline point",
  "creative.partner.ui.before": "Before",
  "creative.partner.ui.after": "After",
  "creative.partner.ui.issues": "Issues to handle",
  "creative.partner.ui.materials": "Materials",
  "creative.partner.ui.angles": "Angles",
  "creative.partner.ui.sections": "Sections",
  "creative.partner.ui.pending": "Waiting for your decision",
  "creative.partner.ui.failed": "This item failed. Try again.",
  "creative.partner.ui.empty": "Nothing has come back yet.",
  "creative.partner.ui.inputLabel": "Message the editor",
  "creative.partner.ui.quickTasks": "Quick tasks",
  "creative.partner.ui.send": "Send to the team",
  "creative.partner.ui.stop": "Stop",
  "creative.partner.ui.stopping": "Stopping…",
  "creative.partner.ui.retry": "Try again",
  "creative.partner.ui.nextStep": "Next step",
  "creative.partner.ui.noProgress": "No task is running for this article.",
  "creative.partner.ui.busy": "Working…",
  "creative.partner.ui.untitled": "Untitled article",
  "creative.partner.ui.plan": "Plan",
};
