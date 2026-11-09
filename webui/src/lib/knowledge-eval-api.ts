/**
 * 知识检索评测 API 客户端（契约 §5，WU-09）。
 *
 * 四条纪律：
 *
 * 1. **认证只有一份实现**：会话 / CSRF / 幂等键 / 错误映射全部复用
 *    `evaluation-api.ts` 导出的 `evaluationTransport`（评测端口 8790，与记忆评测
 *    同源），这里不重写登录、重试与 fetch。
 * 2. **snake_case → camelCase 收敛在 normalize 函数里**：调用方拿到的永远是
 *    camelCase 结构，映射规则不散落到页面组件。
 * 3. **`null` 就是「没有证据」**：指标不可算时后端给 `null`，客户端**原样保留**，
 *    绝不 `?? 0` 兜底——「0 分」与「算不出来」在报告里必须可区分。
 * 4. **公式不下发到前端本地**：指标公式串由后端 `GET /knowledge/metrics` 原样
 *    下发，客户端只透传，**不内置任何公式常量**（首页公式区的唯一来源是后端）。
 */

import { evaluationTransport } from "@/lib/evaluation-api";

// --------------------------------------------------------------------------- 类型

/** 指标口径元数据：公式串是契约的一部分，由后端下发。 */
export interface KnowledgeMetricSpec {
  metricId: string;
  label: string;
  formula: string;
  numerator: string;
  denominator: string;
  direction: "higher_is_better" | "lower_is_better";
  description: string;
}

/** 七个指标的值：不可算即 `null`，**不补 0**。 */
export interface KnowledgeEvalMetrics {
  hitAtK: number | null;
  precisionAtK: number | null;
  mrr: number | null;
  ndcgAtK: number | null;
  recallAtK: number | null;
  poolCoverage: number | null;
  unscoredRate: number | null;
}

/** 一条检索命中。 */
export interface KnowledgeEvalHit {
  chunkId: string;
  rank: number;
  score: number;
  contentHash: string;
  grade: number | null;
  reason: string;
}

export type KnowledgeEvalCaseStatus = "scored" | "not_assessable" | "failed";

export type KnowledgeEvalLabelSource = "expect" | "judge";

export type KnowledgeEvalRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "cancelled"
  | "interrupted"
  | "failed";

export type KnowledgeEvalSearchMode = "hybrid" | "semantic" | "keyword";

/** 单题结果：指标 + 命中明细 + 错误信息。 */
export interface KnowledgeEvalCaseResult {
  caseId: string;
  status: KnowledgeEvalCaseStatus;
  labelSource: KnowledgeEvalLabelSource;
  metrics: KnowledgeEvalMetrics;
  hits: KnowledgeEvalHit[];
  error?: string | null;
}

/** 数据集里的一条查询用例。 */
export interface KnowledgeEvalCase {
  id: string;
  query: string;
  expectAny: string[];
  kbIds: string[];
  mode: KnowledgeEvalSearchMode;
  limit: number;
  labelSource: KnowledgeEvalLabelSource;
  ordinal: number;
}

/** 数据集。 */
export interface KnowledgeEvalDataset {
  id: string;
  name: string;
  notes: string;
  /** 乐观锁版本号：PATCH 时作为 `base_revision` 回传。 */
  revision: string;
  cases: KnowledgeEvalCase[];
  createdAt: string | null;
  updatedAt: string | null;
}

/** 一次运行里被评的一组检索配置（参数白名单由后端校验）。 */
export interface KnowledgeEvalConfigEntry {
  id: string;
  name: string;
  overrides: Record<string, unknown>;
}

/** 按标注来源分组的聚合段：后端不跨组混算。 */
export interface KnowledgeEvalMetricGroup {
  labelSource: string;
  totalQueries: number;
  scoredQueries: number;
  metrics: KnowledgeEvalMetrics;
}

/** 运行详情。 */
export interface KnowledgeEvalRun {
  id: string;
  datasetId: string;
  status: KnowledgeEvalRunStatus;
  configs: KnowledgeEvalConfigEntry[];
  judge: Record<string, unknown>;
  budgets: Record<string, unknown>;
  metrics: KnowledgeEvalMetricGroup[];
  progress: Record<string, unknown>;
  error: string | null;
  createdAt: string | null;
  finishedAt: string | null;
}

/**
 * 报告：**聚合段（metrics）与逐题段（results）必须同时存在**——
 * 只有聚合数字视为实现缺失，两段缺一不可。
 */
export interface KnowledgeEvalReport {
  runId: string | null;
  status: KnowledgeEvalRunStatus | null;
  /** 聚合段：后端直接给出的整体指标（缺失即 `null`）。 */
  metrics: KnowledgeEvalMetrics;
  /** 聚合段（按标注来源分组）：后端按 `label_source` 分组给出时在此呈现。 */
  groups: KnowledgeEvalMetricGroup[];
  /** 逐题段。 */
  results: KnowledgeEvalCaseResult[];
}

/** 逐指标对比。 */
export interface KnowledgeEvalComparisonMetric {
  metricId: string;
  base: number | null;
  candidate: number | null;
  delta: number | null;
}

/** 退化查询（候选侧比基线差）。 */
export interface KnowledgeEvalRegression {
  caseId: string;
  reason: string;
  metrics: KnowledgeEvalMetrics;
}

/** 两个运行的对比。 */
export interface KnowledgeEvalComparison {
  baseRunId: string;
  candidateRunId: string;
  metrics: KnowledgeEvalComparisonMetric[];
  regressedCases: KnowledgeEvalRegression[];
}

// --------------------------------------------------------------------------- 输入类型

export interface KnowledgeEvalDatasetCreate {
  name: string;
  notes?: string;
  cases?: Array<Record<string, unknown>>;
}

/** 局部更新：`base_revision` 与服务端不一致 → 409（乐观锁）。 */
export interface KnowledgeEvalDatasetPatch {
  base_revision: string;
  name?: string;
  notes?: string;
  cases?: Array<Record<string, unknown>>;
}

export interface KnowledgeEvalRunCreate {
  dataset_id: string;
  configs: Array<Record<string, unknown>>;
  judge_profile_id?: string | null;
  budgets?: Record<string, unknown>;
}

// --------------------------------------------------------------------------- 归一化

type RawRecord = Record<string, unknown>;

/** 七个指标的 snake_case 键（契约 §3.2 的指标 ID）。仅用于**识别**后端形状，
 *  不是公式——公式串只从后端透传。 */
const METRIC_IDS = [
  "hit_at_k",
  "precision_at_k",
  "mrr",
  "ndcg_at_k",
  "recall_at_k",
  "pool_coverage",
  "unscored_rate",
] as const;

/** 同一批指标的 camelCase 形态（后端两种都可能出现）。 */
const METRIC_IDS_CAMEL = [
  "hitAtK",
  "precisionAtK",
  "mrr",
  "ndcgAtK",
  "recallAtK",
  "poolCoverage",
  "unscoredRate",
] as const;

const CASE_STATUSES: readonly KnowledgeEvalCaseStatus[] = [
  "scored",
  "not_assessable",
  "failed",
];

const LABEL_SOURCES: readonly KnowledgeEvalLabelSource[] = ["expect", "judge"];

const RUN_STATUSES: readonly KnowledgeEvalRunStatus[] = [
  "queued",
  "running",
  "completed",
  "cancelled",
  "interrupted",
  "failed",
];

function asRecord(value: unknown): RawRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as RawRecord)
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function pickString(raw: RawRecord, ...keys: string[]): string {
  for (const key of keys) {
    const value = raw[key];
    if (typeof value === "string") return value;
  }
  return "";
}

function pickNullableString(raw: RawRecord, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = raw[key];
    if (typeof value === "string") return value;
    if (value === null) return null;
  }
  return null;
}

/**
 * 取数值：**缺失或 `null` 一律 `null`**。
 *
 * 这里刻意没有 `?? 0` 兜底——「算不出来」不是「0 分」。
 */
function pickNumber(raw: RawRecord, ...keys: string[]): number | null {
  for (const key of keys) {
    const value = raw[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function pickNumberOrZero(raw: RawRecord, ...keys: string[]): number {
  return pickNumber(raw, ...keys) ?? 0;
}

function pickStringArray(raw: RawRecord, ...keys: string[]): string[] {
  for (const key of keys) {
    const value = raw[key];
    if (Array.isArray(value)) {
      return value.filter((item): item is string => typeof item === "string");
    }
  }
  return [];
}

/** 版本号既可能是整数也可能是字符串，统一成字符串回传。 */
function pickRevision(raw: RawRecord, ...keys: string[]): string {
  for (const key of keys) {
    const value = raw[key];
    if (typeof value === "string") return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

/** 指标值归一化：键名映射与 `null` 保留都收在这里。 */
export function normalizeKnowledgeMetrics(raw: unknown): KnowledgeEvalMetrics {
  const record = asRecord(raw);
  return {
    hitAtK: pickNumber(record, "hit_at_k", "hitAtK"),
    precisionAtK: pickNumber(record, "precision_at_k", "precisionAtK"),
    mrr: pickNumber(record, "mrr"),
    ndcgAtK: pickNumber(record, "ndcg_at_k", "ndcgAtK"),
    recallAtK: pickNumber(record, "recall_at_k", "recallAtK"),
    poolCoverage: pickNumber(record, "pool_coverage", "poolCoverage"),
    unscoredRate: pickNumber(record, "unscored_rate", "unscoredRate"),
  };
}

function hasFlatMetricKeys(raw: RawRecord): boolean {
  return (
    METRIC_IDS.some((id) => id in raw) ||
    METRIC_IDS_CAMEL.some((id) => id in raw)
  );
}

function normalizeHit(raw: unknown): KnowledgeEvalHit {
  const record = asRecord(raw);
  return {
    chunkId: pickString(record, "chunk_id", "chunkId"),
    rank: pickNumberOrZero(record, "rank"),
    score: pickNumberOrZero(record, "score"),
    contentHash: pickString(record, "content_hash", "contentHash"),
    grade: pickNumber(record, "grade"),
    reason: pickString(record, "reason"),
  };
}

function normalizeHits(raw: unknown): KnowledgeEvalHit[] {
  return asArray(raw).map(normalizeHit);
}

function normalizeCaseStatus(raw: unknown): KnowledgeEvalCaseStatus {
  return CASE_STATUSES.includes(raw as KnowledgeEvalCaseStatus)
    ? (raw as KnowledgeEvalCaseStatus)
    : // 未知状态不当作「已评分」——保守按失败处理。
      "failed";
}

function normalizeLabelSource(raw: unknown): KnowledgeEvalLabelSource {
  return LABEL_SOURCES.includes(raw as KnowledgeEvalLabelSource)
    ? (raw as KnowledgeEvalLabelSource)
    : // 未知来源按更严格的 judge 处理（可能调模型的来源）。
      "judge";
}

function normalizeRunStatus(raw: unknown): KnowledgeEvalRunStatus | null {
  return RUN_STATUSES.includes(raw as KnowledgeEvalRunStatus)
    ? (raw as KnowledgeEvalRunStatus)
    : null;
}

export function normalizeKnowledgeCaseResult(raw: unknown): KnowledgeEvalCaseResult {
  const record = asRecord(raw);
  return {
    caseId: pickString(record, "case_id", "caseId"),
    status: normalizeCaseStatus(record.status),
    labelSource: normalizeLabelSource(record.label_source ?? record.labelSource),
    metrics: normalizeKnowledgeMetrics(record.metrics),
    hits: normalizeHits(record.hits),
    error: pickNullableString(record, "error"),
  };
}

function normalizeCase(raw: unknown): KnowledgeEvalCase {
  const record = asRecord(raw);
  return {
    id: pickString(record, "id", "case_id", "caseId"),
    query: pickString(record, "query"),
    expectAny: pickStringArray(record, "expect_any", "expectAny"),
    kbIds: pickStringArray(record, "kb_ids", "kbIds"),
    mode: (record.mode === "semantic" || record.mode === "keyword"
      ? record.mode
      : "hybrid") as KnowledgeEvalSearchMode,
    limit: pickNumberOrZero(record, "limit"),
    labelSource: normalizeLabelSource(record.label_source ?? record.labelSource),
    ordinal: pickNumberOrZero(record, "ordinal"),
  };
}

export function normalizeKnowledgeDataset(raw: unknown): KnowledgeEvalDataset {
  const record = asRecord(raw);
  return {
    id: pickString(record, "id"),
    name: pickString(record, "name"),
    notes: pickString(record, "notes"),
    revision: pickRevision(record, "revision"),
    cases: asArray(record.cases).map(normalizeCase),
    createdAt: pickNullableString(record, "created_at", "createdAt"),
    updatedAt: pickNullableString(record, "updated_at", "updatedAt"),
  };
}

function normalizeConfigEntry(raw: unknown): KnowledgeEvalConfigEntry {
  const record = asRecord(raw);
  return {
    id: pickString(record, "id"),
    name: pickString(record, "name"),
    overrides: asRecord(record.overrides),
  };
}

/** 聚合段：后端可能给「扁平指标」也可能给「按 label_source 分组」。 */
function normalizeMetricGroups(raw: unknown): KnowledgeEvalMetricGroup[] {
  const record = asRecord(raw);

  // 形状 1：{groups: [{label_source, metrics, total_queries, scored_queries}]}
  if (Array.isArray(record.groups)) {
    return record.groups.map((group) => {
      const item = asRecord(group);
      return {
        labelSource: pickString(item, "label_source", "labelSource"),
        totalQueries: pickNumberOrZero(item, "total_queries", "totalQueries"),
        scoredQueries: pickNumberOrZero(item, "scored_queries", "scoredQueries"),
        metrics: normalizeKnowledgeMetrics(item.metrics),
      };
    });
  }

  // 形状 2：{expect: {metrics, total_queries}, judge: {...}}
  const groups: KnowledgeEvalMetricGroup[] = [];
  for (const [key, value] of Object.entries(record)) {
    if (!value || typeof value !== "object" || Array.isArray(value)) continue;
    const item = asRecord(value);
    if (!("metrics" in item) && !hasFlatMetricKeys(item)) continue;
    groups.push({
      labelSource: key,
      totalQueries: pickNumberOrZero(item, "total_queries", "totalQueries"),
      scoredQueries: pickNumberOrZero(item, "scored_queries", "scoredQueries"),
      metrics: normalizeKnowledgeMetrics("metrics" in item ? item.metrics : item),
    });
  }
  if (groups.length > 0) return groups;

  // 形状 3：扁平指标 —— 视为一个「整体」分组。
  if (hasFlatMetricKeys(record)) {
    return [
      {
        labelSource: "all",
        totalQueries: pickNumberOrZero(record, "total_queries", "totalQueries"),
        scoredQueries: pickNumberOrZero(record, "scored_queries", "scoredQueries"),
        metrics: normalizeKnowledgeMetrics(record),
      },
    ];
  }
  return [];
}

function normalizeFlatMetrics(raw: unknown): KnowledgeEvalMetrics {
  const record = asRecord(raw);
  if (hasFlatMetricKeys(record)) return normalizeKnowledgeMetrics(record);
  const groups = normalizeMetricGroups(record);
  const overall = groups.find((group) => group.labelSource === "all");
  if (overall) return overall.metrics;
  return normalizeKnowledgeMetrics({});
}

export function normalizeKnowledgeRun(raw: unknown): KnowledgeEvalRun {
  const record = asRecord(raw);
  return {
    id: pickString(record, "id"),
    datasetId: pickString(record, "dataset_id", "datasetId"),
    status: normalizeRunStatus(record.status) ?? "queued",
    configs: asArray(record.configs).map(normalizeConfigEntry),
    judge: asRecord(record.judge ?? record.judge_config ?? record.judgeConfig),
    budgets: asRecord(record.budgets),
    metrics: normalizeMetricGroups(record.metrics),
    progress: asRecord(record.progress),
    error: pickNullableString(record, "error"),
    createdAt: pickNullableString(record, "created_at", "createdAt"),
    finishedAt: pickNullableString(record, "finished_at", "finishedAt"),
  };
}

export function normalizeKnowledgeReport(raw: unknown): KnowledgeEvalReport {
  const record = asRecord(raw);
  return {
    runId: pickString(record, "run_id", "runId") || null,
    status: normalizeRunStatus(record.status),
    metrics: normalizeFlatMetrics(record.metrics),
    groups: normalizeMetricGroups(record.metrics),
    results: asArray(record.results).map(normalizeKnowledgeCaseResult),
  };
}

function normalizeComparisonMetric(
  metricId: string,
  raw: unknown,
): KnowledgeEvalComparisonMetric {
  if (raw !== null && typeof raw === "object" && !Array.isArray(raw)) {
    const record = asRecord(raw);
    return {
      metricId,
      base: pickNumber(record, "base", "base_value", "baseValue"),
      candidate: pickNumber(record, "candidate", "candidate_value", "candidateValue"),
      delta: pickNumber(record, "delta", "diff", "difference"),
    };
  }
  // 裸数值按「差值」理解（后端只给逐指标差时最常见）。
  return {
    metricId,
    base: null,
    candidate: null,
    delta: pickNumber(asRecord({ value: raw }), "value"),
  };
}

export function normalizeKnowledgeComparison(raw: unknown): KnowledgeEvalComparison {
  const record = asRecord(raw);
  const metricsRaw = record.metrics;

  const metrics: KnowledgeEvalComparisonMetric[] = [];
  if (Array.isArray(metricsRaw)) {
    for (const item of metricsRaw) {
      const entry = asRecord(item);
      metrics.push(
        normalizeComparisonMetric(
          pickString(entry, "metric_id", "metricId", "id"),
          entry,
        ),
      );
    }
  } else {
    const bucket = asRecord(metricsRaw);
    for (const [key, value] of Object.entries(bucket)) {
      metrics.push(normalizeComparisonMetric(key, value));
    }
  }

  const regressedCases: KnowledgeEvalRegression[] = asArray(
    record.regressed ?? record.regressed_cases ?? record.regressedCases,
  ).map((item) => {
    const entry = asRecord(item);
    return {
      caseId: pickString(entry, "case_id", "caseId"),
      reason: pickString(entry, "reason"),
      metrics: normalizeKnowledgeMetrics(entry.metrics),
    };
  });

  return {
    baseRunId: pickString(record, "base_run_id", "baseRunId", "base"),
    candidateRunId: pickString(
      record,
      "candidate_run_id",
      "candidateRunId",
      "candidate",
    ),
    metrics,
    regressedCases,
  };
}

/** 指标元数据：公式串**原样透传**，客户端不内置任何公式。 */
export function normalizeMetricSpec(raw: unknown): KnowledgeMetricSpec {
  const record = asRecord(raw);
  return {
    metricId: pickString(record, "metric_id", "metricId", "id"),
    label: pickString(record, "label"),
    formula: pickString(record, "formula"),
    numerator: pickString(record, "numerator"),
    denominator: pickString(record, "denominator"),
    direction:
      record.direction === "lower_is_better" ? "lower_is_better" : "higher_is_better",
    description: pickString(record, "description"),
  };
}

// --------------------------------------------------------------------------- 传输

function jsonHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "Idempotency-Key": evaluationTransport.idempotencyKey(),
    ...extra,
  };
}

function encodePath(value: string): string {
  return encodeURIComponent(value);
}

/** 列表端点既可能返回裸数组，也可能返回 `{data: [...]}` / `{metrics: [...]}`。 */
function unwrapList(body: unknown, ...keys: string[]): unknown[] {
  if (Array.isArray(body)) return body;
  const record = asRecord(body);
  for (const key of keys) {
    if (Array.isArray(record[key])) return record[key] as unknown[];
  }
  if (Array.isArray(record.data)) return record.data as unknown[];
  return [];
}

// --------------------------------------------------------------------------- 客户端

export const knowledgeEvalApi = {
  /**
   * 指标口径与计算公式——首页公式区的唯一来源（公式不得抄进 i18n）。
   *
   * 客户端只原样透传后端下发的 `formula`，**不内置任何公式常量**。
   */
  listMetrics(signal?: AbortSignal): Promise<KnowledgeMetricSpec[]> {
    return evaluationTransport
      .request<unknown>("/knowledge/metrics", { signal })
      .then((body) => unwrapList(body, "metrics").map(normalizeMetricSpec));
  },

  listDatasets(signal?: AbortSignal): Promise<KnowledgeEvalDataset[]> {
    return evaluationTransport
      .request<unknown>("/knowledge/datasets", { signal })
      .then((body) => unwrapList(body, "datasets").map(normalizeKnowledgeDataset));
  },

  createDataset(input: KnowledgeEvalDatasetCreate): Promise<KnowledgeEvalDataset> {
    return evaluationTransport
      .request<unknown>("/knowledge/datasets", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify(input),
      })
      .then(normalizeKnowledgeDataset);
  },

  getDataset(id: string, signal?: AbortSignal): Promise<KnowledgeEvalDataset> {
    return evaluationTransport
      .request<unknown>(`/knowledge/datasets/${encodePath(id)}`, { signal })
      .then(normalizeKnowledgeDataset);
  },

  /** 局部更新：`base_revision` 不匹配 → 409（乐观锁由后端裁决）。 */
  updateDataset(
    id: string,
    input: KnowledgeEvalDatasetPatch,
  ): Promise<KnowledgeEvalDataset> {
    return evaluationTransport
      .request<unknown>(`/knowledge/datasets/${encodePath(id)}`, {
        method: "PATCH",
        headers: jsonHeaders(),
        body: JSON.stringify(input),
      })
      .then(normalizeKnowledgeDataset);
  },

  /** 删数据集（幂等）。 */
  deleteDataset(id: string): Promise<void> {
    return evaluationTransport.request<void>(
      `/knowledge/datasets/${encodePath(id)}`,
      {
        method: "DELETE",
        headers: { "Idempotency-Key": evaluationTransport.idempotencyKey() },
      },
    );
  },

  listRuns(datasetId?: string, signal?: AbortSignal): Promise<KnowledgeEvalRun[]> {
    return evaluationTransport
      .request<unknown>(
        evaluationTransport.withQuery("/knowledge/runs", {
          dataset_id: datasetId,
        }),
        { signal },
      )
      .then((body) => unwrapList(body, "runs").map(normalizeKnowledgeRun));
  },

  /** 发起运行：后端只入队（202），请求内不执行检索。 */
  createRun(input: KnowledgeEvalRunCreate): Promise<KnowledgeEvalRun> {
    return evaluationTransport
      .request<unknown>("/knowledge/runs", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify(input),
      })
      .then(normalizeKnowledgeRun);
  },

  getRun(id: string, signal?: AbortSignal): Promise<KnowledgeEvalRun> {
    return evaluationTransport
      .request<unknown>(`/knowledge/runs/${encodePath(id)}`, { signal })
      .then(normalizeKnowledgeRun);
  },

  /** 报告必须同时含聚合段（metrics）与逐题段（results）。 */
  getRunReport(id: string, signal?: AbortSignal): Promise<KnowledgeEvalReport> {
    return evaluationTransport
      .request<unknown>(`/knowledge/runs/${encodePath(id)}/report`, { signal })
      .then(normalizeKnowledgeReport);
  },

  cancelRun(id: string): Promise<void> {
    return evaluationTransport.request<void>(
      `/knowledge/runs/${encodePath(id)}/cancel`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": evaluationTransport.idempotencyKey(),
        },
        body: "{}",
      },
    );
  },

  /** 对比两个运行：逐指标差 + 退化查询清单。 */
  compare(
    base: string,
    candidate: string,
    signal?: AbortSignal,
  ): Promise<KnowledgeEvalComparison> {
    return evaluationTransport
      .request<unknown>(
        evaluationTransport.withQuery("/knowledge/compare", {
          base,
          candidate,
        }),
        { signal },
      )
      .then(normalizeKnowledgeComparison);
  },
};
