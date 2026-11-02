/**
 * 记忆专项评测 API 客户端（契约 §6，WU-09）。
 *
 * 三条纪律：
 *
 * 1. **认证只有一份实现**：会话/CSRF/Idempotency-Key 全部复用
 *    `evaluation-api.ts` 导出的 `evaluationTransport`，这里不重写登录与重试。
 * 2. **一次调用一个幂等键**：key 在方法入口生成后固定下来，401 重试沿用同一个
 *    key——服务端按 scope+endpoint+key 去重，重试不会变成第二次计费请求。
 * 3. **未知状态不等于成功**：状态判断收敛在 `memoryExperimentIsTerminal` /
 *    `memoryImportSucceeded` 等叶函数里；不认识的字符串一律按「未完成/未就绪」。
 */

import {
  EvaluationApiError,
  evaluationTransport,
} from "@/lib/evaluation-api";

// --------------------------------------------------------------------------- 类型

export type MemorySplit = "development" | "calibration" | "regression";

export type MemoryTrack = "extraction" | "retrieval" | "end_to_end";

export type MemoryDatasetStatus = "staging" | "ready" | "invalid";

export type MemoryImportStatus =
  | "staging"
  | "running"
  | "ready"
  | "invalid"
  | "cancelled";

export type MemoryExperimentStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  | "cancelled"
  | "interrupted"
  | "timed_out";

export type MemoryMeasurementStatus = "complete" | "incomplete";

export type MemoryCapabilityStatus = "supported" | "unsupported" | "partial";

export type MemoryDatasetSource = {
  repo_url: string;
  revision: string;
  sha256: string;
  license: string;
  original_ids: string[];
  loader_version: string;
  grading_protocol: string;
};

export type MemoryDatasetRevision = {
  id: string;
  suite_id: string;
  dataset_version: string;
  status: MemoryDatasetStatus;
  split_counts: Partial<Record<MemorySplit, number>>;
  source: MemoryDatasetSource;
  definition_digest: string;
  created_at: string | null;
  updated_at: string | null;
};

/** 契约 §6：`{data,next_after_key}`；`next_after_key` 为空表示到底。 */
export type MemoryPage<T> = {
  data: T[];
  next_after_key: string | null;
};

export type MemoryScenarioSummary = {
  scenario_id: string;
  revision: number;
  split: MemorySplit;
  template_family: string;
  cluster_id: string;
  tags: string[];
  event_count: number;
  probe_count: number;
};

/** 管理面读出：执行载荷与 gold 分列，gold 只在授权页出现。 */
export type MemoryScenarioDetail = {
  execution: Record<string, unknown>;
  gold: Record<string, unknown>;
  revision: number;
};

export type MemoryImportMetadata = {
  suite_id?: string | null;
  dataset_version?: string | null;
  format: "json" | "jsonl" | "longmemeval";
  source: MemoryDatasetSource;
  split?: MemorySplit | null;
  subset?: string | null;
};

export type MemoryImportResult = {
  import_id: string | null;
  status: MemoryImportStatus;
  dataset_revision_id: string | null;
  dataset_version: string | null;
  scenario_count: number;
  errors: Array<Record<string, unknown>>;
};

export type MemoryImportJob = {
  id: string;
  suite_id: string;
  status: MemoryImportStatus;
  counts: {
    expected: number | null;
    observed: number | null;
    rejected: number;
  };
  errors: Array<Record<string, unknown>>;
  next_after_key: string | null;
  dataset_revision_id: string | null;
  dataset_version: string | null;
  created_at: string;
  updated_at: string;
};

export type MemoryCapability = {
  status: MemoryCapabilityStatus;
  detail: string;
};

export type MemoryCapabilitiesView = {
  tracks: Array<{
    id: MemoryTrack;
    supported: boolean;
    missing: string[];
    required_metrics: string[];
    primary_metric: string;
  }>;
  settings: string[];
  profiles: Array<Record<string, unknown>>;
  capabilities: Record<string, MemoryCapability>;
  missing: string[];
  retrieval_channels: string[];
};

export type MemoryExperimentCreateResult = {
  id: string;
  status: MemoryExperimentStatus;
  planned_trials: number;
  child_runs: MemoryChildRun[];
};

export type MemoryCandidate = {
  id: string;
  name: string;
  mode: "zhixu" | "no_persistent_memory";
  agent_profile_id: string;
  extractor_profile_id?: string | null;
  memory_settings: Record<string, unknown>;
};

export type MemoryExperimentCreate = {
  dataset_revision_id: string;
  scenario_ids?: string[] | null;
  split: MemorySplit;
  track: MemoryTrack;
  candidates: MemoryCandidate[];
  repetitions: number;
  model_mode: "real" | "scripted";
  script_fixture_id?: string | null;
  judge_profile_id?: string | null;
  budgets: Record<string, number | null>;
  primary_metric: string;
  policy: Record<string, unknown>;
};

export type MemoryChildRun = {
  id: string;
  experiment_id: string;
  candidate_id: string;
  batch_index: number;
  run_id: string;
  scenario_ids: string[];
  status: string;
  created_at: string | null;
};

export type MemoryExperimentSummary = {
  id: string;
  suite_id: string;
  dataset_revision_id: string;
  split: MemorySplit;
  track: MemoryTrack;
  status: MemoryExperimentStatus;
  measurement_status: MemoryMeasurementStatus;
  planned_trials: number;
  repetitions: number;
  model_mode: "real" | "scripted";
  primary_metric: string;
  created_at: string | null;
  updated_at: string | null;
};

export type MemoryExperimentDetail = MemoryExperimentSummary & {
  candidates: MemoryCandidate[];
  judge_profile_id: string | null;
  policy: Record<string, unknown>;
  frozen_manifest: Record<string, unknown>;
  budget: Record<string, unknown>;
  child_runs: MemoryChildRun[];
  progress: {
    planned_trials: number;
    child_runs: number;
    child_status_counts: Record<string, number>;
    attempts: number;
    terminal_attempts: number;
  };
  coverage: {
    measurement_status: MemoryMeasurementStatus;
    planned: number;
    scored: number;
  };
};

export type MemoryMetric = {
  id: string;
  version: number;
  unit: "ratio" | "count" | "ms" | "token" | "bytes" | "currency" | string;
  value: number | null;
  numerator: number | null;
  denominator: number | null;
  status: "scored" | "not_assessable" | "not_applicable";
  scope: string;
  reasons: string[];
  coverage: { scored: number; planned: number; reasons: string[] };
};

export type MemoryMetricsView = {
  metrics_version: number;
  aggregation: string;
  metrics: MemoryMetric[];
  scope: Record<string, unknown>;
  coverage: Record<string, unknown>;
};

export type MemoryProbeSummary = {
  scenario_id: string;
  probe_id: string;
  trial_index: number;
  category: string | null;
  qa_correct: boolean | null;
  qa_status: string;
  fact_coverage: number | null;
  grounded_memory_success: boolean | null;
  grading_revision: number;
  review_digest: string | null;
  reasons: string[];
  metrics: MemoryMetric[];
};

export type MemoryEventPage = {
  data: Array<Record<string, unknown>>;
  next_after_seq: number;
  has_more: boolean;
  sealed: boolean;
};

export type MemoryComparisonSummary = {
  id: string;
  baseline_experiment_id: string;
  candidate_experiment_id: string;
  baseline_candidate_id: string;
  candidate_candidate_id: string;
  changed_fields: string[];
  policy: Record<string, unknown>;
  status: string;
  created_at: string | null;
};

export type MemoryComparisonDetail = MemoryComparisonSummary &
  MemoryPage<Record<string, unknown>> & {
    statistics: Record<string, unknown>;
    intervals: Record<string, unknown> | null;
    excluded: Array<Record<string, unknown>>;
    reasons: string[];
    coverage: Record<string, unknown> | null;
  };

export type MemoryComparisonCreate = {
  baseline_experiment_id: string;
  candidate_experiment_id: string;
  baseline_candidate_id: string;
  candidate_candidate_id: string;
  changed_fields: string[];
  policy?: Record<string, unknown>;
};

export type MemoryReviewRef = {
  scenario_id: string;
  probe_id: string;
  metric_id: string;
  grading_revision: number;
};

/**
 * `POST /samples/import` 的返回。
 *
 * 旧内置样本（basic-v1 / reliability-v2）没有 `memory_dataset_revision_id`：
 * 该字段是**可选增量**，旧调用方按 `suite_id` 判断成功不受影响。
 */
export type MemorySampleImportResult = {
  suite_id: string;
  memory_dataset_revision_id?: string | null;
  dataset_revision_id?: string | null;
  dataset_version?: string;
  status?: string;
  scenario_count?: number;
  split?: MemorySplit;
};

export type MemoryPageQuery = { after_key?: string; limit?: number };

export type MemoryUploadHandle = {
  /** 0..100；服务端不知总长时给 0，不假装完成。 */
  percent: number;
  loaded: number;
  total: number | null;
};

// --------------------------------------------------------------------------- 状态叶函数

const TERMINAL_EXPERIMENT_STATUSES = new Set<string>([
  "completed",
  "cancelled",
  "interrupted",
  "timed_out",
]);

/** 未知状态一律「未终态」：不能因为字符串不认识就当成跑完了。 */
export function memoryExperimentIsTerminal(status: string | null | undefined): boolean {
  return typeof status === "string" && TERMINAL_EXPERIMENT_STATUSES.has(status);
}

/** 只有字面量 ready 算就绪；未知状态不当成功。 */
export function memoryImportSucceeded(status: string | null | undefined): boolean {
  return status === "ready";
}

/** 只有字面量 ready 的修订才允许创建实验。 */
export function memoryDatasetIsReady(status: string | null | undefined): boolean {
  return status === "ready";
}

// --------------------------------------------------------------------------- 传输

function abortError(): Error {
  if (typeof DOMException === "function") {
    return new DOMException("已取消", "AbortError");
  }
  const error = new Error("已取消");
  error.name = "AbortError";
  return error;
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function errorFromBody(status: number, raw: string): EvaluationApiError {
  let body: unknown = null;
  try {
    body = raw ? JSON.parse(raw) : null;
  } catch {
    // 非 JSON 错误体（网关层 text/plain）保留默认码。
  }
  const contract = (body as { error?: { code?: string; message?: string } } | null)?.error;
  return new EvaluationApiError(
    contract?.code ?? "EVAL_ERROR",
    contract?.message || `HTTP ${status}`,
    status,
  );
}

function postJson<T>(path: string, payload: unknown): Promise<T> {
  return evaluationTransport.request<T>(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": evaluationTransport.idempotencyKey(),
    },
    body: JSON.stringify(payload),
  });
}

/**
 * XHR 上传：需要 `upload.onprogress` 与 `AbortSignal`，fetch 在浏览器里给不了进度。
 *
 * 401 时重置会话后用**同一个 key** 重发一次：服务端幂等表看到同一 key + 同一请求体
 * 会直接返回既有结果，不会创建第二份导入。
 */
async function uploadWithProgress<T>(
  path: string,
  form: FormData,
  options: {
    idempotencyKey?: string;
    signal?: AbortSignal;
    onProgress?: (handle: MemoryUploadHandle) => void;
    retry?: boolean;
  } = {},
): Promise<T> {
  const key = options.idempotencyKey ?? evaluationTransport.idempotencyKey();
  await evaluationTransport.ensureSession();
  if (typeof XMLHttpRequest === "undefined") {
    // 非浏览器环境（SSR/测试）退化为 fetch：没有进度，但语义一致。
    const response = await evaluationTransport.request<T>(path, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: form,
    });
    return response;
  }
  const send = () =>
    new Promise<T>((resolve, reject) => {
      if (options.signal?.aborted) {
        reject(abortError());
        return;
      }
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${evaluationTransport.baseUrl()}${path}`);
      xhr.withCredentials = true;
      xhr.setRequestHeader("X-CSRF-Token", evaluationTransport.getCsrfToken());
      xhr.setRequestHeader("Idempotency-Key", key);
      if (xhr.upload) {
        xhr.upload.onprogress = (event: ProgressEvent) => {
          const total = event.lengthComputable ? event.total : null;
          options.onProgress?.({
            percent: total ? Math.round((event.loaded / total) * 100) : 0,
            loaded: event.loaded,
            total,
          });
        };
      }
      const onAbort = () => xhr.abort();
      options.signal?.addEventListener("abort", onAbort, { once: true });
      const cleanup = () => options.signal?.removeEventListener("abort", onAbort);
      xhr.onerror = () => {
        cleanup();
        reject(new EvaluationApiError("EVAL_NETWORK", "网络错误", 0));
      };
      xhr.onabort = () => {
        cleanup();
        reject(abortError());
      };
      xhr.onload = () => {
        cleanup();
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve((xhr.responseText ? JSON.parse(xhr.responseText) : undefined) as T);
          } catch {
            reject(new EvaluationApiError("EVAL_ERROR", "响应不是合法 JSON", xhr.status));
          }
          return;
        }
        reject(errorFromBody(xhr.status, xhr.responseText));
      };
      xhr.send(form);
    });

  try {
    return await send();
  } catch (error) {
    if (
      error instanceof EvaluationApiError &&
      error.status === 401 &&
      options.retry !== false
    ) {
      evaluationTransport.resetSession();
      await evaluationTransport.ensureSession();
      // 同一 key：认证重试不是第二次业务请求。
      return uploadWithProgress<T>(path, form, { ...options, idempotencyKey: key, retry: false });
    }
    throw error;
  }
}

function encodePath(value: string): string {
  return encodeURIComponent(value);
}

// --------------------------------------------------------------------------- 客户端

export const memoryEvaluationApi = {
  /** ``GET /memory/datasets``：修订列表（不带场景正文与 gold）。 */
  listDatasets(
    filter: MemoryPageQuery & { split?: MemorySplit | null } = {},
    signal?: AbortSignal,
  ): Promise<MemoryPage<MemoryDatasetRevision>> {
    return evaluationTransport.request<MemoryPage<MemoryDatasetRevision>>(
      evaluationTransport.withQuery("/memory/datasets", {
        after_key: filter.after_key,
        limit: filter.limit === undefined ? undefined : String(filter.limit),
        split: filter.split ?? undefined,
      }),
      { signal },
    );
  },

  /** ``POST /memory/datasets/import``：流式 multipart + 进度。 */
  importDataset(
    input: MemoryImportMetadata & {
      file: File | Blob;
      fileName?: string;
      after_key?: string;
    },
    options: {
      idempotencyKey?: string;
      signal?: AbortSignal;
      onProgress?: (handle: MemoryUploadHandle) => void;
    } = {},
  ): Promise<MemoryImportResult> {
    const form = new FormData();
    const { file, fileName, ...metadata } = input;
    form.append("metadata", JSON.stringify(metadata));
    form.append("file", file, fileName ?? "memory-dataset.jsonl");
    return uploadWithProgress<MemoryImportResult>("/memory/datasets/import", form, options);
  },

  /** ``GET /memory/imports/{id}``：状态与错误分页。 */
  getImport(
    importId: string,
    query: MemoryPageQuery = {},
    signal?: AbortSignal,
  ): Promise<MemoryImportJob> {
    return evaluationTransport.request<MemoryImportJob>(
      evaluationTransport.withQuery(`/memory/imports/${encodePath(importId)}`, {
        after_key: query.after_key,
        limit: query.limit === undefined ? undefined : String(query.limit),
      }),
      { signal },
    );
  },

  listScenarios(
    datasetRevisionId: string,
    query: MemoryPageQuery = {},
    signal?: AbortSignal,
  ): Promise<MemoryPage<MemoryScenarioSummary>> {
    return evaluationTransport.request<MemoryPage<MemoryScenarioSummary>>(
      evaluationTransport.withQuery(
        `/memory/datasets/${encodePath(datasetRevisionId)}/scenarios`,
        {
          after_key: query.after_key,
          limit: query.limit === undefined ? undefined : String(query.limit),
        },
      ),
      { signal },
    );
  },

  getScenario(
    datasetRevisionId: string,
    scenarioId: string,
    revision?: number,
    signal?: AbortSignal,
  ): Promise<MemoryScenarioDetail> {
    return evaluationTransport.request<MemoryScenarioDetail>(
      evaluationTransport.withQuery(
        `/memory/datasets/${encodePath(datasetRevisionId)}/scenarios/${encodePath(scenarioId)}`,
        { revision: revision === undefined ? undefined : String(revision) },
      ),
      { signal },
    );
  },

  /** CAS 编辑：`base_revision` 与服务端当前版本不一致会 409。 */
  createRevision(
    datasetRevisionId: string,
    input: { base_revision: string; changes: Array<Record<string, unknown>> },
    signal?: AbortSignal,
  ): Promise<MemoryDatasetRevision> {
    return evaluationTransport.request<MemoryDatasetRevision>(
      `/memory/datasets/${encodePath(datasetRevisionId)}/revisions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
        signal,
      },
    );
  },

  capabilities(signal?: AbortSignal): Promise<MemoryCapabilitiesView> {
    return evaluationTransport.request<MemoryCapabilitiesView>("/memory/capabilities", {
      signal,
    });
  },

  /** 入队即返回 202 摘要；真正的 trial 由调度器领取。 */
  createExperiment(
    input: MemoryExperimentCreate,
    options: { idempotencyKey?: string; signal?: AbortSignal } = {},
  ): Promise<MemoryExperimentCreateResult> {
    const key = options.idempotencyKey ?? evaluationTransport.idempotencyKey();
    return evaluationTransport.request<MemoryExperimentCreateResult>(
      "/memory/experiments",
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify(input),
        signal: options.signal,
      },
    );
  },

  listExperiments(
    query: MemoryPageQuery & { dataset_revision_id?: string } = {},
    signal?: AbortSignal,
  ): Promise<MemoryPage<MemoryExperimentSummary>> {
    return evaluationTransport.request<MemoryPage<MemoryExperimentSummary>>(
      evaluationTransport.withQuery("/memory/experiments", {
        after_key: query.after_key,
        limit: query.limit === undefined ? undefined : String(query.limit),
        dataset_revision_id: query.dataset_revision_id,
      }),
      { signal },
    );
  },

  getExperiment(experimentId: string, signal?: AbortSignal): Promise<MemoryExperimentDetail> {
    return evaluationTransport.request<MemoryExperimentDetail>(
      `/memory/experiments/${encodePath(experimentId)}`,
      { signal },
    );
  },

  cancelExperiment(experimentId: string): Promise<MemoryExperimentDetail> {
    return postJson<MemoryExperimentDetail>(
      `/memory/experiments/${encodePath(experimentId)}/cancel`,
      {},
    );
  },

  getExperimentMetrics(
    experimentId: string,
    signal?: AbortSignal,
  ): Promise<MemoryMetricsView> {
    return evaluationTransport.request<MemoryMetricsView>(
      `/memory/experiments/${encodePath(experimentId)}/metrics`,
      { signal },
    );
  },

  listAttemptProbes(
    attemptId: string,
    query: MemoryPageQuery = {},
    signal?: AbortSignal,
  ): Promise<MemoryPage<MemoryProbeSummary>> {
    return evaluationTransport.request<MemoryPage<MemoryProbeSummary>>(
      evaluationTransport.withQuery(`/memory/attempts/${encodePath(attemptId)}/probes`, {
        after_key: query.after_key,
        limit: query.limit === undefined ? undefined : String(query.limit),
      }),
      { signal },
    );
  },

  listAttemptEvents(
    attemptId: string,
    query: { after_seq?: number; limit?: number } = {},
    signal?: AbortSignal,
  ): Promise<MemoryEventPage> {
    return evaluationTransport.request<MemoryEventPage>(
      evaluationTransport.withQuery(`/memory/attempts/${encodePath(attemptId)}/events`, {
        after_seq: query.after_seq === undefined ? undefined : String(query.after_seq),
        limit: query.limit === undefined ? undefined : String(query.limit),
      }),
      { signal },
    );
  },

  createComparison(input: MemoryComparisonCreate): Promise<MemoryComparisonSummary> {
    return postJson<MemoryComparisonSummary>("/memory/comparisons", input);
  },

  getComparison(
    comparisonId: string,
    query: MemoryPageQuery & { status?: string } = {},
    signal?: AbortSignal,
  ): Promise<MemoryComparisonDetail> {
    return evaluationTransport.request<MemoryComparisonDetail>(
      evaluationTransport.withQuery(`/memory/comparisons/${encodePath(comparisonId)}`, {
        after_key: query.after_key,
        limit: query.limit === undefined ? undefined : String(query.limit),
        status: query.status,
      }),
      { signal },
    );
  },

  downloadComparisonReport(comparisonId: string): Promise<void> {
    return evaluationTransport.requestDownload(
      `/memory/comparisons/${encodePath(comparisonId)}/report`,
      `memory-comparison-${comparisonId.slice(0, 8)}.html`,
    );
  },

  /**
   * 轮询实验直到终态。AbortSignal 取消后立刻停止（不再发下一次请求）并抛
   * `AbortError`；未知状态不会被当成终态，因此不会提前返回「成功」。
   */
  async pollExperiment(
    experimentId: string,
    options: {
      signal?: AbortSignal;
      intervalMs?: number;
      onUpdate?: (detail: MemoryExperimentDetail) => void;
    } = {},
  ): Promise<MemoryExperimentDetail> {
    const interval = options.intervalMs ?? 2000;
    for (;;) {
      if (options.signal?.aborted) throw abortError();
      const detail = await this.getExperiment(experimentId, options.signal);
      options.onUpdate?.(detail);
      if (memoryExperimentIsTerminal(detail.status)) return detail;
      await delay(interval, options.signal);
    }
  },

  /**
   * 内置记忆集合导入：返回 `suite_id` 与可选的 `memory_dataset_revision_id`。
   * 旧样本没有后者，类型保持可选以兼容既有调用方。
   */
  importSample(sampleId: string): Promise<MemorySampleImportResult> {
    return evaluationTransport.request<MemorySampleImportResult>("/samples/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sample_id: sampleId }),
    });
  },

  /** 追加逐 probe 复核：`memory_ref` 缺省即整 attempt 验收（旧形状不变）。 */
  addReview(
    attemptId: string,
    payload: { verdict: "passed" | "failed"; comment?: string; memory_ref?: MemoryReviewRef },
  ): Promise<Record<string, unknown>> {
    return evaluationTransport.request<Record<string, unknown>>(
      `/attempts/${encodePath(attemptId)}/reviews`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  },
};
