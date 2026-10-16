// 创作空间固定团队协作的前端通道（契约 §3/§5/§6）。
//
// 沿用既有口径（`writing-api.ts`）：**读取走认证 HTTP**，**写入走认证 WebSocket 的
// requestMutation 白名单**。契约里 `writing.collab.get` / `writing.artifact.get` 是读
// 动作，`writing.start` … `writing.undo` 是写动作。
//
// 边界：**snake_case 只存在于传输层**。帧与 HTTP 报文在这里一次性映射成 camelCase，
// 上层（面板 / reducer / 编辑器集成）只看见 camelCase 类型。
import { mutation, request, type WebUIMutationTransport } from "@/lib/api";
import type { ArticleSnapshotPayload } from "@/lib/writing-api";

const COLLAB_BASE = "/api/writing/collab";
const COLLAB_READ_TIMEOUT_MS = 20_000;

export type CollabStatus =
  | "idle"
  | "queued"
  | "running"
  | "waiting_author"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export const COLLAB_STATUSES: readonly CollabStatus[] = [
  "idle",
  "queued",
  "running",
  "waiting_author",
  "completed",
  "failed",
  "cancelled",
  "interrupted",
];

export type CollabStage = "" | "brief" | "research" | "outline" | "draft" | "review" | "rewrite";

export const COLLAB_STAGES: readonly CollabStage[] = [
  "",
  "brief",
  "research",
  "outline",
  "draft",
  "review",
  "rewrite",
];

export type CollabAction =
  | "start_full"
  | "ideate"
  | "research"
  | "outline"
  | "draft"
  | "rewrite_selection"
  | "review_all"
  | "reply";

export const COLLAB_ACTIONS: readonly CollabAction[] = [
  "start_full",
  "ideate",
  "research",
  "outline",
  "draft",
  "rewrite_selection",
  "review_all",
  "reply",
];

export type ArtifactKind = "brief" | "material_list" | "outline" | "draft" | "rewrite" | "review";

export const ARTIFACT_KINDS: readonly ArtifactKind[] = [
  "brief",
  "material_list",
  "outline",
  "draft",
  "rewrite",
  "review",
];

export type HandoffKind = "outline_confirm" | "materials_request" | "final_accept";

export const HANDOFF_KINDS: readonly HandoffKind[] = [
  "outline_confirm",
  "materials_request",
  "final_accept",
];

export type HandoffStatus = "pending" | "confirmed" | "superseded";

export const HANDOFF_STATUSES: readonly HandoffStatus[] = [
  "pending",
  "confirmed",
  "superseded",
];

export type SuggestionStatus = "pending" | "applied" | "rejected" | "stale" | "reverted";

export const SUGGESTION_STATUSES: readonly SuggestionStatus[] = [
  "pending",
  "applied",
  "rejected",
  "stale",
  "reverted",
];

/** 协作层的 `blocked_reason` 是作者能读懂的纯文本，可能为空。 */
export type CollaborationStage = CollabStage;

export interface Collaboration {
  id: string;
  articleId: string;
  chatId: string;
  sessionKey: string;
  goal: Record<string, unknown>;
  status: CollabStatus;
  stage: CollabStage;
  activeBatchId: string | null;
  cancelGeneration: number;
  version: number;
  seq: number;
  lastHandoffId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface CollabBatch {
  id: string;
  collaborationId: string;
  attempt: number;
  teamRunId: string;
  baseRevision: number;
  snapshotDigest: string;
  action: CollabAction;
  instruction: string;
  status: CollabStatus;
  cancelGeneration: number;
  frozen: Record<string, unknown>;
  errorMessage: string;
  createdAt: string;
  finishedAt: string | null;
}

export interface CollabArtifact {
  id: string;
  collaborationId: string;
  batchId: string;
  attempt: number;
  kind: ArtifactKind;
  roleId: string;
  version: number;
  content: unknown;
  contentDigest: string;
  sourceIds: string[];
  gaps: string[];
  status: string;
  createdAt: string;
}

export interface CollabHandoff {
  id: string;
  collaborationId: string;
  batchId: string;
  artifactId: string;
  kind: HandoffKind;
  artifactVersion: number;
  status: HandoffStatus;
  createdAt: string;
  resolvedAt: string | null;
}

export type CollabTarget =
  | { kind: "replace_range"; start: number; end: number }
  | { kind: "insert_at"; position: number }
  | { kind: "outline_item"; itemId: string }
  | { kind: string; [key: string]: unknown };

export interface CollabSuggestion {
  id: string;
  collaborationId: string;
  artifactId: string;
  articleId: string;
  baseRevision: number;
  snapshotDigest: string;
  target: CollabTarget;
  before: string;
  after: string;
  summary: string;
  notes: string;
  sourceMaterialIds: string[];
  status: SuggestionStatus;
  createdAt: string;
  appliedRevision: number | null;
}

/** 列表投影里只带 `summary`：产物全文要靠 `getArtifact` 单独取。 */
export interface CollabArtifactSummary {
  id: string;
  collaborationId: string;
  batchId: string;
  kind: ArtifactKind;
  version: number;
  summary: string;
}

export interface CollabSnapshot {
  collaboration: Collaboration | null;
  batches: CollabBatch[];
  artifacts: (CollabArtifact | CollabArtifactSummary)[];
  handoffs: CollabHandoff[];
  suggestions: CollabSuggestion[];
  lastVersion: number;
  lastSeq: number;
  nextActions: CollabAction[];
  blockedReason: string | null;
}

export interface SelectionRange {
  start: number;
  end: number;
}

export interface StartCollabRequest {
  articleId: string;
  chatId: string;
  action: CollabAction;
  baseRevision: number;
  snapshotDigest: string;
  instruction?: string;
  selection?: { start: number; end: number; text: string };
  materialIds?: string[];
  /** 重试时复用同一个 id；缺省时由 `requestIds` 工厂生成。 */
  clientRequestId?: string;
}

export interface ReplyCollabRequest {
  collaborationId: string;
  feedback: string;
  expectVersion: number;
  clientRequestId?: string;
}

export interface ConfirmCollabRequest {
  collaborationId: string;
  handoffId: string;
  expectedHandoffVersion: number;
  articleRevision: number;
  articleDigest: string;
  outline?: Array<{ id: string; text: string; order: number }>;
  clientRequestId?: string;
}

export interface CancelCollabRequest {
  collaborationId: string;
  activeBatchId: string;
  clientRequestId?: string;
}

export interface RetryCollabRequest {
  collaborationId: string;
  batchId: string;
  expectVersion: number;
  clientRequestId?: string;
}

export interface ApplySuggestionRequest {
  suggestionId: string;
  articleId: string;
  baseRevision: number;
  clientRequestId?: string;
}

export interface UndoRequest {
  undoId: string;
  baseRevision: number;
  clientRequestId?: string;
}

export interface StartCollabResult {
  collaborationId: string;
  batchId: string;
  status: CollabStatus;
  stage: CollabStage;
  sessionKey: string;
  activeBatchId: string | null;
}

export interface ReplyCollabResult {
  collaborationId: string;
  batchId: string;
  status: CollabStatus;
}

export interface ConfirmCollabResult {
  collaborationId: string;
  batchId: string;
  articleRevision: number;
  articleDigest: string;
  handoffStatus: HandoffStatus;
}

export interface CancelCollabResult {
  collaborationId: string;
  status: CollabStatus;
  cancelGeneration: number;
}

export interface RetryCollabResult {
  collaborationId: string;
  batchId: string;
  attempt: number;
  status: CollabStatus;
}

export interface ApplySuggestionResult {
  suggestionId: string;
  articleId: string;
  revision: number;
  digest: string;
  staledSuggestionIds: string[];
  undoId: string;
}

export interface UndoResult {
  undoId: string;
  articleId: string;
  revision: number;
  digest: string;
  suggestionId: string;
}

export interface CollabClient {
  base?: string;
  token?: string;
  /** 按请求取 token（应用侧 token 会刷新；`token` 是静态兜底，主要给测试用）。 */
  getToken?: () => string;
  transport?: WebUIMutationTransport | null;
  requestIds?: RequestIdFactory;
  readTimeoutMs?: number;
}

export interface CollabApi {
  getSnapshot(articleId: string): Promise<CollabSnapshot>;
  getArtifact(artifactId: string): Promise<CollabArtifact>;
  start(req: StartCollabRequest): Promise<StartCollabResult>;
  reply(req: ReplyCollabRequest): Promise<ReplyCollabResult>;
  confirm(req: ConfirmCollabRequest): Promise<ConfirmCollabResult>;
  cancel(req: CancelCollabRequest): Promise<CancelCollabResult>;
  retry(req: RetryCollabRequest): Promise<RetryCollabResult>;
  apply(req: ApplySuggestionRequest): Promise<ApplySuggestionResult>;
  undo(req: UndoRequest): Promise<UndoResult>;
}

export interface RequestIdFactory {
  next(): string;
  for(key: string): string;
  clear(key: string): void;
  release(key: string): void;
}

/** 帧名（契约 §5）；只认这三个，其余一律当作不认识的帧。 */
export const COLLAB_EVENT_NAMES = [
  "writing_collab",
  "writing_artifact",
  "writing_handoff",
] as const;

export type CollabEventName = (typeof COLLAB_EVENT_NAMES)[number];

export function createEmptyCollabSnapshot(): CollabSnapshot {
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
  };
}

export function createRequestIdFactory(
  generate: () => string = defaultRequestId,
): RequestIdFactory {
  const active = new Map<string, string>();
  const make = (): string => {
    const id = generate();
    return typeof id === "string" && id ? id : defaultRequestId();
  };
  return {
    next: make,
    for: (key: string): string => {
      const existing = active.get(key);
      if (existing) return existing;
      const created = make();
      active.set(key, created);
      return created;
    },
    clear: (key: string): void => {
      active.delete(key);
    },
    release: (key: string): void => {
      active.delete(key);
    },
  };
}

function defaultRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `collab-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * JS 的 `String` 下标是 UTF-16 码元，Python 的 `len()` / 切片是 Unicode 码点。
 * 选区跨过中文或 emoji 时必须转换，否则服务端切出来的片段会错位。
 * 落在代理对中间的偏移按 Unicode 标量数向下取整（一个 emoji 算 1 个码点）。
 */
export function utf16RangeToCodepoints(body: string, start: number, end: number): SelectionRange {
  const length = typeof body === "string" ? body.length : 0;
  const from = clampOffset(start, length);
  const to = clampOffset(end, length);
  // 反向区间按空选区处理（落点停在靠前的那一端），不悄悄扩成一段。
  if (from >= to) {
    const collapsed = codepointsBefore(body, from);
    return { start: collapsed, end: collapsed };
  }
  return {
    start: codepointsBefore(body, from),
    end: codepointsBefore(body, to),
  };
}

function clampOffset(value: unknown, length: number): number {
  const num = typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : 0;
  return Math.min(Math.max(num, 0), length);
}

/**
 * `offset` 之前有多少个 Unicode 码点。走 `codePointAt` 而不是手算代理对：
 * 一个码点只有**整个**落在 `offset` 之内才算数，正好切在代理对中间时向下取整。
 */
function codepointsBefore(body: string, offset: number): number {
  let codepoints = 0;
  let unitIndex = 0;
  while (unitIndex < offset && unitIndex < body.length) {
    const point = body.codePointAt(unitIndex);
    if (point === undefined) break;
    const step = point > 0xffff ? 2 : 1;
    if (unitIndex + step > offset) break;
    unitIndex += step;
    codepoints += 1;
  }
  return codepoints;
}

// ---------------------------------------------------------------------------
// 传输层映射（snake_case → camelCase）
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNullableString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asNullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function toRows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function asEnum<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : fallback;
}

function asEnumArray<T extends string>(value: unknown, allowed: readonly T[]): T[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is T => typeof item === "string" && (allowed as readonly string[]).includes(item),
  );
}

export function toCollabStatus(value: unknown): CollabStatus {
  return asEnum(value, COLLAB_STATUSES, "idle");
}

export function toCollabStage(value: unknown): CollabStage {
  return asEnum(value, COLLAB_STAGES, "");
}

export function toCollabAction(value: unknown): CollabAction {
  return asEnum(value, COLLAB_ACTIONS, "start_full");
}

export function toArtifactKind(value: unknown): ArtifactKind {
  return asEnum(value, ARTIFACT_KINDS, "brief");
}

export function toCollaboration(raw: unknown): Collaboration | null {
  if (!isRecord(raw)) return null;
  const id = asString(raw.id);
  if (!id) return null;
  return {
    id,
    articleId: asString(raw.article_id),
    chatId: asString(raw.chat_id),
    sessionKey: asString(raw.session_key),
    goal: asRecord(raw.goal),
    status: toCollabStatus(raw.status),
    stage: toCollabStage(raw.stage),
    activeBatchId: asNullableString(raw.active_batch_id),
    cancelGeneration: asNumber(raw.cancel_generation),
    version: asNumber(raw.version, 1),
    seq: asNumber(raw.seq),
    lastHandoffId: asNullableString(raw.last_handoff_id),
    createdAt: asString(raw.created_at),
    updatedAt: asString(raw.updated_at),
  };
}

export function toCollabBatch(raw: unknown): CollabBatch | null {
  if (!isRecord(raw)) return null;
  const id = asString(raw.id);
  if (!id) return null;
  return {
    id,
    collaborationId: asString(raw.collaboration_id),
    attempt: asNumber(raw.attempt, 1),
    teamRunId: asString(raw.team_run_id),
    baseRevision: asNumber(raw.base_revision),
    snapshotDigest: asString(raw.snapshot_digest),
    action: toCollabAction(raw.action),
    instruction: asString(raw.instruction),
    status: toCollabStatus(raw.status),
    cancelGeneration: asNumber(raw.cancel_generation),
    frozen: asRecord(raw.frozen),
    errorMessage: asString(raw.error_message),
    createdAt: asString(raw.created_at),
    finishedAt: asNullableString(raw.finished_at),
  };
}

export function toCollabArtifact(raw: unknown): CollabArtifact | null {
  if (!isRecord(raw)) return null;
  const id = asString(raw.id);
  if (!id) return null;
  return {
    id,
    collaborationId: asString(raw.collaboration_id),
    batchId: asString(raw.batch_id),
    attempt: asNumber(raw.attempt, 1),
    kind: toArtifactKind(raw.kind),
    roleId: asString(raw.role_id),
    version: asNumber(raw.version, 1),
    content: raw.content,
    contentDigest: asString(raw.content_digest),
    sourceIds: asStringArray(raw.source_ids),
    gaps: asStringArray(raw.gaps),
    status: asString(raw.status, "ready"),
    createdAt: asString(raw.created_at),
  };
}

export function toCollabHandoff(raw: unknown): CollabHandoff | null {
  if (!isRecord(raw)) return null;
  const id = asString(raw.id);
  if (!id) return null;
  return {
    id,
    collaborationId: asString(raw.collaboration_id),
    batchId: asString(raw.batch_id),
    artifactId: asString(raw.artifact_id),
    kind: asEnum(raw.kind, HANDOFF_KINDS, "outline_confirm"),
    artifactVersion: asNumber(raw.artifact_version, 1),
    status: asEnum(raw.status, HANDOFF_STATUSES, "pending"),
    createdAt: asString(raw.created_at),
    resolvedAt: asNullableString(raw.resolved_at),
  };
}

function toCollabTarget(raw: unknown): CollabTarget {
  if (!isRecord(raw)) return { kind: "" };
  const kind = asString(raw.kind);
  if (kind === "replace_range") {
    return { kind, start: asNumber(raw.start), end: asNumber(raw.end) };
  }
  if (kind === "insert_at") {
    return { kind, position: asNumber(raw.position) };
  }
  if (kind === "outline_item") {
    return { kind, itemId: asString(raw.item_id) };
  }
  return { ...raw, kind };
}

export function toCollabSuggestion(raw: unknown): CollabSuggestion | null {
  if (!isRecord(raw)) return null;
  const id = asString(raw.id);
  if (!id) return null;
  return {
    id,
    collaborationId: asString(raw.collaboration_id),
    artifactId: asString(raw.artifact_id),
    articleId: asString(raw.article_id),
    baseRevision: asNumber(raw.base_revision),
    snapshotDigest: asString(raw.snapshot_digest),
    target: toCollabTarget(raw.target),
    before: asString(raw.before),
    after: asString(raw.after),
    summary: asString(raw.summary),
    notes: asString(raw.notes),
    sourceMaterialIds: asStringArray(raw.source_material_ids),
    status: asEnum(raw.status, SUGGESTION_STATUSES, "pending"),
    createdAt: asString(raw.created_at),
    appliedRevision: asNullableNumber(raw.applied_revision),
  };
}

/**
 * `writing.collab.get` 的快照投影。认不出的字段直接丢，缺字段走安全默认值：
 * 面板宁可少显示一块，也不能因为服务端加字段而炸。
 */
export function toCollabSnapshot(raw: unknown): CollabSnapshot {
  const source = asRecord(raw);
  return {
    collaboration: toCollaboration(source.collaboration),
    batches: toRows(source.batches)
      .map(toCollabBatch)
      .filter((item): item is CollabBatch => item !== null),
    artifacts: toRows(source.artifacts)
      .map(toCollabArtifact)
      .filter((item): item is CollabArtifact => item !== null),
    handoffs: toRows(source.handoffs)
      .map(toCollabHandoff)
      .filter((item): item is CollabHandoff => item !== null),
    suggestions: toRows(source.suggestions)
      .map(toCollabSuggestion)
      .filter((item): item is CollabSuggestion => item !== null),
    lastVersion: asNumber(source.last_version),
    lastSeq: asNumber(source.last_seq),
    nextActions: asEnumArray(source.next_actions, COLLAB_ACTIONS),
    blockedReason: asNullableString(source.blocked_reason),
  };
}

// ---------------------------------------------------------------------------
// 事件归并（契约 §5：按 version / seq 幂等，旧事件不得倒退状态）
// ---------------------------------------------------------------------------

function readFrameBody(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) return null;
  const name = value.event;
  if (typeof name !== "string" || !(COLLAB_EVENT_NAMES as readonly string[]).includes(name)) {
    return null;
  }
  return value;
}

function upsertById<T extends { id: string }>(list: T[], item: T): T[] {
  const index = list.findIndex((entry) => entry.id === item.id);
  if (index < 0) return [...list, item];
  const next = [...list];
  next[index] = item;
  return next;
}

/** 帧携带的推进计量（契约 §5）。 */
interface CollabFrameCursor {
  /** `writing_collab` 是协作版本；产物/交接帧是它自己那个实体的版本。 */
  version: number;
  /** 只有 `writing_collab` 带 `seq`；其余通道没有，用 -1 占位。 */
  seq: number;
}

/** 归并事件时记下每个通道走到哪：`channel` 是本帧通道，`cursors` 是各通道游标。 */
interface CollabEventState extends CollabSnapshot {
  channel?: CollabEventName;
  cursors?: Partial<Record<CollabEventName, CollabFrameCursor>>;
}

/**
 * 按通道读计量（契约 §5）。
 *
 * 三个通道各自独立推进：`writing_collab` 带协作 `version` 与 `seq`；
 * `writing_artifact` / `writing_handoff` 的 `version` 是**该实体自己**的版本。
 * 因此每类帧只跟同类帧比大小，不能拿协作游标去卡产物帧——否则同一跃迁里
 * 先到的协作帧会把后到的产物帧误判成旧帧，产物卡就永远建不起来。
 */
function readFrameCursor(body: Record<string, unknown>): CollabFrameCursor {
  const version = asNumber(body.version, -1);
  if ((body.event as CollabEventName) !== "writing_collab") {
    return { version, seq: -1 };
  }
  return { version, seq: asNumber(body.seq, -1) };
}

function channelCursors(state: CollabSnapshot): Partial<Record<CollabEventName, CollabFrameCursor>> {
  return (state as CollabEventState).cursors ?? {};
}

/** 只有严格更新（版本更大，或同版本但 `seq` 更大）的帧才推进对应通道。 */
function isNewerFrame(state: CollabSnapshot, name: CollabEventName, cursor: CollabFrameCursor): boolean {
  const seen = channelCursors(state)[name];
  if (!seen) return true;
  if (cursor.version > seen.version) return true;
  return cursor.version === seen.version && cursor.seq > seen.seq;
}

/**
 * 帧是否属于当前协作（契约 §5 里产物/交接帧唯一可用的归属字段）。
 * 帧没带 `collaboration_id`、或客户端还没读到协作快照时不做无据拒绝：
 * 拒绝的代价（吃掉第一条真实帧）比误收一帧更大，收敛交给快照与版本单调性。
 */
function belongsToCurrentCollaboration(
  state: CollabSnapshot,
  body: Record<string, unknown>,
): boolean {
  const collaborationId = asString(body.collaboration_id);
  if (!collaborationId) return true;
  const current = state.collaboration?.id;
  if (!current) return true;
  return collaborationId === current;
}

/** 游标只进不退：低版本帧即使被接受也不能把各通道游标拉回去。 */
function advanceCursor(
  state: CollabSnapshot,
  name: CollabEventName,
  cursor: CollabFrameCursor,
): Pick<CollabSnapshot, "lastVersion" | "lastSeq"> & { cursors: CollabEventState["cursors"] } {
  const cursors = { ...channelCursors(state) };
  const seen = cursors[name];
  cursors[name] = seen
    ? {
        version: Math.max(cursor.version, seen.version),
        seq: Math.max(cursor.seq, seen.seq),
      }
    : cursor;
  return {
    lastVersion: Math.max(cursor.version, state.lastVersion),
    lastSeq: Math.max(cursor.seq, state.lastSeq),
    cursors,
  };
}

/**
 * 归并一帧；被忽略的帧原样返回入参状态，其余返回带内部游标的快照
 * （由 `applyCollabEvent` 收口后再外露）。
 */
function applyCollabFrame(
  state: CollabSnapshot,
  body: Record<string, unknown>,
  cursor: CollabFrameCursor,
): CollabEventState {
  const name = body.event as CollabEventName;

  if (name === "writing_collab") {
    const collaborationId = asString(body.collaboration_id);
    if (!collaborationId) return state;
    if (state.collaboration && state.collaboration.id !== collaborationId) return state;
    // 同版本重放不算旧帧（状态没变，但 blocked_reason 可能刚被服务端补上）。
    if (!isNewerFrame(state, name, cursor)) return state;
    const collaboration: Collaboration = {
      id: collaborationId,
      articleId: state.collaboration?.articleId ?? "",
      chatId: state.collaboration?.chatId ?? "",
      sessionKey: state.collaboration?.sessionKey ?? "",
      goal: state.collaboration?.goal ?? {},
      status: toCollabStatus(body.status),
      stage: toCollabStage(body.stage),
      activeBatchId: asNullableString(body.active_batch_id),
      cancelGeneration: state.collaboration?.cancelGeneration ?? 0,
      version: cursor.version,
      seq: cursor.seq,
      lastHandoffId: state.collaboration?.lastHandoffId ?? null,
      createdAt: state.collaboration?.createdAt ?? "",
      updatedAt: state.collaboration?.updatedAt ?? "",
    };
    return {
      ...state,
      ...advanceCursor(state, name, cursor),
      channel: name,
      collaboration,
      blockedReason: asNullableString(body.blocked_reason),
    };
  }

  if (name === "writing_artifact") {
    const id = asString(body.artifact_id);
    if (!id) return state;
    if (cursor.version < 0) return state;
    // 归属收敛（契约 §5）：只接受**当前协作**的产物帧。
    //
    // 旧实现用 `body.batch_id` 挡旧批次的迟到帧，但契约 §5 的 `writing_artifact`
    // body 只有 `{collaboration_id, artifact_id, kind, version, summary}` —— 没有
    // `batch_id`，那个判断恒为假（过滤恒空）。可用的真实归属字段只有
    // `collaboration_id`：已知当前协作且不一致 → 丢弃；当前协作未知（快照还没到手）
    // → 不做无据拒绝，否则第一条产物帧会被吃掉。
    //
    // 残余缺口（已登记）：同一协作里**旧批次**的迟到帧没有契约字段可以判旧，
    // 客户端只能靠 `version` 单调性与重连快照收敛。
    if (!belongsToCurrentCollaboration(state, body)) return state;
    // 产物帧只跟产物帧比：旧版本的产物帧被忽略，重复帧不重复插卡。
    if (!isNewerFrame(state, name, cursor)) return state;
    const summary: CollabArtifactSummary = {
      id,
      collaborationId: asString(body.collaboration_id),
      // 契约 §5 没有这个字段，正常收帧时恒为空串；服务端真加了这个字段才用得上。
      batchId: asString(body.batch_id),
      kind: toArtifactKind(body.kind),
      version: cursor.version,
      summary: asString(body.summary),
    };
    return {
      ...state,
      ...advanceCursor(state, name, cursor),
      channel: name,
      artifacts: upsertById(state.artifacts, summary),
    };
  }

  // 交接帧同样只认当前协作。
  if (!belongsToCurrentCollaboration(state, body)) return state;
  const handoffId = asString(body.handoff_id);
  if (!handoffId) return state;
  if (!isNewerFrame(state, name, cursor)) return state;
  const existing = state.handoffs.find((entry) => entry.id === handoffId);
  const handoff: CollabHandoff = {
    id: handoffId,
    collaborationId: asString(body.collaboration_id),
    batchId: existing?.batchId ?? "",
    artifactId: asString(body.artifact_id),
    kind: asEnum(body.kind, HANDOFF_KINDS, "outline_confirm"),
    artifactVersion: asNumber(body.artifact_version, 1),
    status: asEnum(body.status, HANDOFF_STATUSES, "pending"),
    createdAt: existing?.createdAt ?? "",
    resolvedAt: existing?.resolvedAt ?? null,
  };
  return {
    ...state,
    ...advanceCursor(state, name, cursor),
    channel: name,
    handoffs: upsertById(state.handoffs, handoff),
  };
}

export interface CollabEventSummary {
  name: CollabEventName;
  collaborationId: string;
  version: number;
  seq: number;
}

/**
 * 一帧只认一个 event 名；名字不认识、body 形状不对一律 null。
 * 协作帧天然带 `chat_id`，订阅侧直接复用 `onChat(chatId)`（plan Task 11）。
 */
export function parseCollabFrame(raw: unknown): CollabEventSummary | null {
  const body = readFrameBody(raw);
  if (!body) return null;
  const cursor = readFrameCursor(body);
  if (cursor.version < 0) return null;
  return {
    name: body.event as CollabEventName,
    collaborationId: asString(body.collaboration_id),
    version: cursor.version,
    seq: Math.max(cursor.seq, 0),
  };
}

export interface CollabReducer {
  /** 归并一帧增量；旧/重复/看不懂的帧原样返回入参状态。 */
  applyEvent(state: CollabSnapshot, frame: unknown): CollabSnapshot;
  /** 归并整份快照（重连口径）；比现有游标旧的快照不回退。 */
  applySnapshot(state: CollabSnapshot, incoming: CollabSnapshot): CollabSnapshot;
  /** 快照读失败或需要彻底重来时的起点。 */
  empty(): CollabSnapshot;
}

export function applyCollabSnapshot(
  state: CollabSnapshot,
  incoming: CollabSnapshot,
): CollabSnapshot {
  if (incoming.lastVersion < state.lastVersion) return state;
  return { ...incoming };
}

export function applyCollabEvent(state: CollabSnapshot, frame: unknown): CollabSnapshot {
  const body = readFrameBody(frame);
  if (!body) return state;
  const cursor = readFrameCursor(body);
  if (cursor.version < 0) return state;
  const stripped = stripEventChannel(state);
  const next = applyCollabFrame(stripped, body, cursor);
  // 被忽略的帧（旧版本/重复/看不懂）原样返回入参对象，面板才能靠引用相等跳过重渲。
  return next === stripped ? state : next;
}

/**
 * 归并内部状态（`channel` / `cursors`）挂在快照对象上但不外露：对外 `CollabSnapshot`
 * 始终是契约 §6 的形状。`cursors` 必须保留进归并，否则重复帧会被当成新帧。
 */
function stripEventChannel(state: CollabSnapshot): CollabSnapshot {
  const next: CollabSnapshot = { ...state };
  delete (next as CollabEventState).channel;
  return next;
}

export function createCollabReducer(): CollabReducer {
  return {
    applyEvent: applyCollabEvent,
    applySnapshot: applyCollabSnapshot,
    empty: createEmptyCollabSnapshot,
  };
}

// ---------------------------------------------------------------------------
// 跨端内容摘要（契约 A §5.3 的 snapshot_digest）
// ---------------------------------------------------------------------------

/**
 * 契约 A §5.3 冻结的持久化内容键（服务端 `nanobot/writing/models.py::_SNAPSHOT_KEYS`）。
 * 顺序只为可读：序列化时按键排序。
 */
const SNAPSHOT_KEYS = [
  "status",
  "writing_type",
  "template_id",
  "title",
  "summary",
  "brief",
  "tags",
  "outline",
  "materials",
  "body",
] as const;

/**
 * 与 Python `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`
 * 等价的序列化：对象键**递归**排序、无空白、非 ASCII 原样输出。
 * （键名都是 ASCII，所以 JS 的 UTF-16 排序与 Python 的码点排序结果一致。）
 */
export function pythonJson(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return Number.isFinite(value) ? JSON.stringify(value) : "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(pythonJson).join(",")}]`;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record).sort();
    const body = keys
      .map((key) => `${JSON.stringify(key)}:${pythonJson(record[key])}`)
      .join(",");
    return `{${body}}`;
  }
  return "null";
}

function snapshotText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** `_brief`：只留 goal / audience / style 里非空的字符串，逐项 strip。 */
function normalizedBrief(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  const out: Record<string, string> = {};
  for (const key of ["goal", "audience", "style"] as const) {
    const text = snapshotText(value[key]).trim();
    if (text) out[key] = text;
  }
  return out;
}

/** `_tags`：字符串标签逐项 strip，空串丢弃。 */
function normalizedTags(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((tag): tag is string => typeof tag === "string")
    .map((tag) => tag.trim())
    .filter((tag) => tag.length > 0);
}

/** `_outline`：跳过空文本条目，`order` 取**原始下标**（服务端用 enumerate）。 */
function normalizedOutline(value: unknown): Array<Record<string, unknown>> {
  const entries = Array.isArray(value) ? value : [];
  const items: Array<Record<string, unknown>> = [];
  entries.forEach((raw, index) => {
    const item = isRecord(raw) ? raw : {};
    const text = snapshotText(item.text).trim();
    if (!text) return;
    items.push({ id: snapshotText(item.id), text, order: index });
  });
  return items;
}

/**
 * `_materials`：所有字段固定成套（缺的补空串），`url` 只对 link 生效，
 * `order` 取**原始下标**。服务端在 `id` / 时间戳缺失时会新生成，
 * 客户端无从复现——那种草稿本来也还没存过盘（`writing.start` 会按版本冲突拒绝）。
 */
function normalizedMaterials(value: unknown): Array<Record<string, unknown>> {
  const entries = Array.isArray(value) ? value : [];
  return entries.map((raw, index) => {
    const item = isRecord(raw) ? raw : {};
    const kind = snapshotText(item.type) || "note";
    const url = snapshotText(item.url);
    return {
      id: snapshotText(item.id),
      type: kind,
      title: snapshotText(item.title),
      content: snapshotText(item.content),
      url: kind === "link" ? url : "",
      source: snapshotText(item.source),
      note: snapshotText(item.note),
      language: snapshotText(item.language),
      included: item.included !== false,
      order: index,
      created_at: snapshotText(item.createdAt),
      updated_at: snapshotText(item.updatedAt),
    };
  });
}

/**
 * 服务端 `normalize_snapshot(normalize_article_fields(payload))` 的等价投影：
 * 客户端本地草稿 → 服务端存盘后会得到的那份内容。
 */
export function articleSnapshotValue(
  snapshot: ArticleSnapshotPayload,
): Record<string, unknown> {
  const fields = snapshot as unknown as Record<string, unknown>;
  const projected: Record<string, unknown> = {
    status: snapshotText(fields.status) || "idea",
    writing_type: snapshotText(fields.writing_type).trim(),
    template_id: snapshotText(fields.template_id).trim(),
    title: snapshotText(fields.title).trim(),
    summary: snapshotText(fields.summary).trim(),
    brief: normalizedBrief(fields.brief),
    tags: normalizedTags(fields.tags),
    outline: normalizedOutline(fields.outline),
    materials: normalizedMaterials(fields.materials),
    body: snapshotText(fields.body),
  };
  const ordered: Record<string, unknown> = {};
  for (const key of SNAPSHOT_KEYS) ordered[key] = projected[key];
  return ordered;
}

/**
 * `writing.start` / `writing.confirm` 的 `snapshot_digest`（契约 A §5.3）：
 * 与服务端 `snapshot_digest()` **逐字节同口径**，跨端用 Python 侧生成的 fixture 校验
 * （见 `tests/writing-collab-api.test.ts` 的 `test_snapshot_digest_matches_python`）。
 */
export function collabSnapshotDigest(snapshot: ArticleSnapshotPayload): string {
  return sha256Hex(pythonJson(articleSnapshotValue(snapshot)));
}

/** SHA-256（FIPS 180-4）。浏览器与测试环境都没有同步 crypto，故自带实现，零依赖。 */
const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
  0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
  0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
  0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
  0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
  0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
  0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
  0xc67178f2,
]);

function rotr(value: number, shift: number): number {
  return ((value >>> shift) | (value << (32 - shift))) >>> 0;
}

export function sha256Hex(message: string): string {
  const bytes = new TextEncoder().encode(message);
  const bitLength = bytes.length * 8;
  // padding：0x80 + 若干 0 + 64 位大端长度，补到 64 字节整数倍。
  const padded = new Uint8Array((((bytes.length + 8) >> 6) << 6) + 64);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(bitLength / 4294967296));
  view.setUint32(padded.length - 4, bitLength >>> 0);

  const state = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const w = new Uint32Array(64);
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let index = 0; index < 16; index += 1) {
      w[index] = view.getUint32(offset + index * 4);
    }
    for (let index = 16; index < 64; index += 1) {
      const s0 = rotr(w[index - 15], 7) ^ rotr(w[index - 15], 18) ^ (w[index - 15] >>> 3);
      const s1 = rotr(w[index - 2], 17) ^ rotr(w[index - 2], 19) ^ (w[index - 2] >>> 10);
      w[index] = (w[index - 16] + s0 + w[index - 7] + s1) >>> 0;
    }
    let a = state[0];
    let b = state[1];
    let c = state[2];
    let d = state[3];
    let e = state[4];
    let f = state[5];
    let g = state[6];
    let h = state[7];
    for (let index = 0; index < 64; index += 1) {
      const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + s1 + ch + SHA256_K[index] + w[index]) >>> 0;
      const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (s0 + maj) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }
    state[0] = (state[0] + a) >>> 0;
    state[1] = (state[1] + b) >>> 0;
    state[2] = (state[2] + c) >>> 0;
    state[3] = (state[3] + d) >>> 0;
    state[4] = (state[4] + e) >>> 0;
    state[5] = (state[5] + f) >>> 0;
    state[6] = (state[6] + g) >>> 0;
    state[7] = (state[7] + h) >>> 0;
  }
  return Array.from(state, (word) => word.toString(16).padStart(8, "0")).join("");
}

/** 契约 §3：`chat_id` 必须匹配 `^[A-Za-z0-9_:-]{1,64}$`（会话键 ≤74 字符）。 */
export const COLLAB_CHAT_ID_RE = /^[A-Za-z0-9_:-]{1,64}$/;

/** 文章的协作会话 chatId：非法字符折成 `-`，并把总长压在 64 以内。 */
export function collabChatId(articleId: string, prefix = "writing"): string {
  const safe = (articleId || "")
    .replace(/[^A-Za-z0-9_:-]/g, "-")
    .slice(0, 64 - prefix.length - 1);
  return `${prefix}:${safe || "article"}`;
}

// ---------------------------------------------------------------------------
// collabApi
// ---------------------------------------------------------------------------

function snapshotPath(base: string, articleId: string): string {
  return `${base}${COLLAB_BASE}?${new URLSearchParams({ article_id: articleId }).toString()}`;
}

function artifactPath(base: string, artifactId: string): string {
  return `${base}${COLLAB_BASE}/artifact?${new URLSearchParams({
    artifact_id: artifactId,
  }).toString()}`;
}

function selectionToWire(
  selection: StartCollabRequest["selection"],
): Record<string, unknown> | undefined {
  if (!selection) return undefined;
  return { start: selection.start, end: selection.end, text: selection.text };
}

function toStartResult(raw: Record<string, unknown>): StartCollabResult {
  return {
    collaborationId: asString(raw.collaboration_id),
    batchId: asString(raw.batch_id),
    status: toCollabStatus(raw.status),
    stage: toCollabStage(raw.stage),
    sessionKey: asString(raw.session_key),
    activeBatchId: asNullableString(raw.active_batch_id),
  };
}

function toReplyResult(raw: Record<string, unknown>): ReplyCollabResult {
  return {
    collaborationId: asString(raw.collaboration_id),
    batchId: asString(raw.batch_id),
    status: toCollabStatus(raw.status),
  };
}

function toConfirmResult(raw: Record<string, unknown>): ConfirmCollabResult {
  return {
    collaborationId: asString(raw.collaboration_id),
    batchId: asString(raw.batch_id),
    articleRevision: asNumber(raw.article_revision),
    articleDigest: asString(raw.article_digest),
    handoffStatus: asEnum(raw.handoff_status, HANDOFF_STATUSES, "pending"),
  };
}

function toCancelResult(raw: Record<string, unknown>): CancelCollabResult {
  return {
    collaborationId: asString(raw.collaboration_id),
    status: toCollabStatus(raw.status),
    cancelGeneration: asNumber(raw.cancel_generation),
  };
}

function toRetryResult(raw: Record<string, unknown>): RetryCollabResult {
  return {
    collaborationId: asString(raw.collaboration_id),
    batchId: asString(raw.batch_id),
    attempt: asNumber(raw.attempt, 1),
    status: toCollabStatus(raw.status),
  };
}

function toApplyResult(raw: Record<string, unknown>): ApplySuggestionResult {
  return {
    suggestionId: asString(raw.suggestion_id),
    articleId: asString(raw.article_id),
    revision: asNumber(raw.revision),
    digest: asString(raw.digest),
    staledSuggestionIds: asStringArray(raw.staled_suggestion_ids),
    undoId: asString(raw.undo_id),
  };
}

function toUndoResult(raw: Record<string, unknown>): UndoResult {
  return {
    undoId: asString(raw.undo_id),
    articleId: asString(raw.article_id),
    revision: asNumber(raw.revision),
    digest: asString(raw.digest),
    suggestionId: asString(raw.suggestion_id),
  };
}

/** 一个逻辑写操作固定用一个 request id：重试复用，服务端才能幂等join。 */
function writeClient(client: CollabClient): WebUIMutationTransport {
  if (!client.transport) {
    throw new Error("writing collaboration transport is not configured");
  }
  return client.transport;
}

export function createCollabApi(client: CollabClient = {}): CollabApi {
  const base = client.base ?? "";
  const requestIds = client.requestIds ?? createRequestIdFactory();
  const readTimeoutMs = client.readTimeoutMs ?? COLLAB_READ_TIMEOUT_MS;
  // 读动作走 HTTP，必须在**发请求那一刻**取 token：集成层注入静态串会在 token 轮换后
  // 变成 401（面板一挂载就整屏报「写作搭档无法处理该请求」）。
  const readToken = (): string => client.getToken?.() ?? client.token ?? "";
  const send = async (
    action: string,
    payload: Record<string, unknown>,
  ): Promise<Record<string, unknown>> =>
    (await mutation<Record<string, unknown>>(writeClient(client), action, payload)) ?? {};

  return {
    async getSnapshot(articleId: string): Promise<CollabSnapshot> {
      const body = await request<Record<string, unknown>>(
        snapshotPath(base, articleId),
        readToken(),
        undefined,
        readTimeoutMs,
      );
      return toCollabSnapshot(body);
    },

    async getArtifact(artifactId: string): Promise<CollabArtifact> {
      const body = await request<Record<string, unknown>>(
        artifactPath(base, artifactId),
        readToken(),
        undefined,
        readTimeoutMs,
      );
      const raw = isRecord(body.artifact) ? body.artifact : body;
      return toCollabArtifact(raw) ?? toCollabArtifact({ id: artifactId })!;
    },

    async start(req: StartCollabRequest): Promise<StartCollabResult> {
      const payload: Record<string, unknown> = {
        client_request_id: req.clientRequestId ?? requestIds.next(),
        article_id: req.articleId,
        chat_id: req.chatId,
        action: req.action,
        base_revision: req.baseRevision,
        snapshot_digest: req.snapshotDigest,
      };
      if (req.instruction !== undefined) payload.instruction = req.instruction;
      const selection = selectionToWire(req.selection);
      if (selection) payload.selection = selection;
      if (req.materialIds !== undefined) payload.material_ids = req.materialIds;
      return toStartResult(await send("writing.start", payload));
    },

    async reply(req: ReplyCollabRequest): Promise<ReplyCollabResult> {
      return toReplyResult(await send("writing.reply", {
        client_request_id: req.clientRequestId ?? requestIds.next(),
        collaboration_id: req.collaborationId,
        feedback: req.feedback,
        expect_version: req.expectVersion,
      }));
    },

    async confirm(req: ConfirmCollabRequest): Promise<ConfirmCollabResult> {
      const payload: Record<string, unknown> = {
        client_request_id: req.clientRequestId ?? requestIds.next(),
        collaboration_id: req.collaborationId,
        handoff_id: req.handoffId,
        expected_handoff_version: req.expectedHandoffVersion,
        article_revision: req.articleRevision,
        article_digest: req.articleDigest,
      };
      if (req.outline !== undefined) payload.outline = req.outline;
      return toConfirmResult(await send("writing.confirm", payload));
    },

    async cancel(req: CancelCollabRequest): Promise<CancelCollabResult> {
      return toCancelResult(await send("writing.cancel", {
        client_request_id: req.clientRequestId ?? requestIds.next(),
        collaboration_id: req.collaborationId,
        active_batch_id: req.activeBatchId,
      }));
    },

    async retry(req: RetryCollabRequest): Promise<RetryCollabResult> {
      return toRetryResult(await send("writing.retry", {
        client_request_id: req.clientRequestId ?? requestIds.next(),
        collaboration_id: req.collaborationId,
        batch_id: req.batchId,
        expect_version: req.expectVersion,
      }));
    },

    async apply(req: ApplySuggestionRequest): Promise<ApplySuggestionResult> {
      return toApplyResult(await send("writing.apply", {
        client_request_id: req.clientRequestId ?? requestIds.next(),
        suggestion_id: req.suggestionId,
        article_id: req.articleId,
        base_revision: req.baseRevision,
      }));
    },

    async undo(req: UndoRequest): Promise<UndoResult> {
      return toUndoResult(await send("writing.undo", {
        client_request_id: req.clientRequestId ?? requestIds.next(),
        undo_id: req.undoId,
        base_revision: req.baseRevision,
      }));
    },
  };
}

function readCollabClient(): CollabClient {
  const scope = globalThis as typeof globalThis & {
    __NANOBOT_WRITING_COLLAB_CLIENT__?: CollabClient;
  };
  return scope.__NANOBOT_WRITING_COLLAB_CLIENT__ ?? {};
}

/**
 * 默认实例：绑定在 `globalThis.__NANOBOT_WRITING_COLLAB_CLIENT__` 上，
 * 由集成层在拿到 transport / token 后注入（WU-12/WU-13）。
 */
export const collabApi: CollabApi = {
  getSnapshot: (articleId) => createCollabApi(readCollabClient()).getSnapshot(articleId),
  getArtifact: (artifactId) => createCollabApi(readCollabClient()).getArtifact(artifactId),
  start: (req) => createCollabApi(readCollabClient()).start(req),
  reply: (req) => createCollabApi(readCollabClient()).reply(req),
  confirm: (req) => createCollabApi(readCollabClient()).confirm(req),
  cancel: (req) => createCollabApi(readCollabClient()).cancel(req),
  retry: (req) => createCollabApi(readCollabClient()).retry(req),
  apply: (req) => createCollabApi(readCollabClient()).apply(req),
  undo: (req) => createCollabApi(readCollabClient()).undo(req),
};
