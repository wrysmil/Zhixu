/**
 * 评估服务 API 客户端（gateway 内嵌 aiohttp 应用，默认 127.0.0.1:8790）。
 *
 * 会话模型：`/auth/session` 校验既有 cookie → 失败则用 bootstrap secret 登录换
 * csrf；写请求带 `X-CSRF-Token` + `Idempotency-Key`；401 时重置会话重试一次。
 * 错误响应统一为 `{error:{code,message}}`（CORS 修复后浏览器可读）。
 */

import { loadSavedSecret } from "@/lib/bootstrap";

const EVAL_PORT = 8790;

export type RunStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  | "cancelled"
  | "interrupted"
  | "timed_out";

export type Verdict = "passed" | "failed" | "review_required" | "not_evaluated";

export type RunSummary = {
  id: string;
  suite_id: string;
  suite_name: string;
  status: RunStatus;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  parent_run_id: string | null;
  planned_count: number;
  terminal_count: number;
};

export type RunMetrics = {
  planned: number;
  passed: number;
  failed: number;
  review_required: number;
  not_evaluated: number;
  overall_pass_rate: number | null;
  evaluated_pass_rate: number | null;
  evaluation_coverage: number | null;
  tool_case_pass_rate: number | null;
  tool_eligible_cases: number;
  tool_valid_cases: number;
  quality: Record<string, unknown>;
  agent_duration: Record<string, unknown>;
  usage: Record<string, unknown>;
  /** 任务完成度：与综合合格率不是一回事。 */
  task_outcomes?: TaskOutcomeMetrics;
  qualification?: QualificationMetrics;
  reliability?: ReliabilityMetrics;
};

export type TaskOutcomeMetrics = {
  planned: number;
  completed: number;
  partial: number;
  failed: number;
  unknown: number;
  task_success_rate: number | null;
  partial_completion_rate: number | null;
  task_assessment_coverage: number | null;
};

export type QualificationMetrics = {
  qualified: number;
  disqualified: number;
  review_required: number;
  not_evaluated: number;
  qualification_rate: number | null;
};

export type ReliabilityMetrics = {
  total_cases: number;
  groups: Array<{
    repetitions: number;
    cases: number;
    at_least_once_cases: number;
    all_success_cases: number;
    at_least_once_rate: number | null;
    all_success_rate: number | null;
  }>;
};

/** 证据覆盖度：缺证必须显式，不能默认完整。 */
export type EvaluationCoverage = {
  trace: "complete" | "partial" | "missing";
  identity: "complete" | "partial" | "missing";
  usage: "complete" | "partial" | "missing";
  artifacts: "complete" | "partial" | "missing" | "not_applicable";
  required_evidence: "complete" | "partial";
  reasons: string[];
};

export type DimensionStatus =
  | "passed"
  | "failed"
  | "not_assessable"
  | "not_applicable";

export type AttemptEvaluation = {
  task_outcome: "completed" | "partial" | "failed" | "unknown";
  qualification: "qualified" | "disqualified" | "review_required" | "not_evaluated";
  dimensions: Record<
    string,
    {
      dimension: string;
      status: DimensionStatus;
      score: number | null;
      reason_codes: string[];
      evidence_refs: string[];
    }
  >;
  measurement_errors: Array<Record<string, unknown>>;
};

export type ComparisonPair = {
  case_id: string;
  case_revision: number;
  status: "improved" | "regressed" | "unchanged" | "incomparable";
  reason: string;
  baseline: { success_rate: number | null; trials: number; status: string };
  candidate: { success_rate: number | null; trials: number; status: string };
  delta_success_rate: number | null;
};

export type Comparison = {
  id: string;
  baseline_run_id: string;
  candidate_run_id: string;
  mode: string;
  compatibility: "strict" | "incompatible";
  pairs: ComparisonPair[];
  metrics: Record<string, unknown>;
  intervals: Record<string, unknown> | null;
  excluded: Array<Record<string, unknown>>;
  reasons: string[];
};

export type RunMetricsView = {
  metrics_version: number;
  metrics: RunMetrics;
  coverage: Record<string, unknown>;
  scope: {
    run_id: string;
    case_count: number;
    planned_trials: number;
    valid_trials: number;
    repetitions: number;
    model_mode: string;
  };
};

export type RunCounts = Partial<Record<Verdict, number>>;

export type Run = RunSummary & {
  snapshot: Record<string, unknown>;
  counts: RunCounts;
  metrics: RunMetrics | null;
  attempts_url: string;
  /** 评估能力：full / smoke / legacy。smoke 不能当综合质量门禁。 */
  evaluation_capability?: "full" | "smoke" | "legacy";
  coverage?: Record<string, unknown>;
};

export type AttemptError = {
  phase: "setup" | "execution" | "trace" | "grading" | "cleanup";
  code: string;
  message: string;
};

export type Attempt = {
  id: string;
  run_id: string;
  position: number;
  case_id: string;
  case_revision: number;
  case_name: string;
  execution_status:
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "timed_out"
    | "cancelled"
    | "interrupted"
    | "setup_error";
  grading_status: string;
  verdict: Verdict;
  started_at: string | null;
  finished_at: string | null;
  final_answer: string | null;
  stop_reason: string | null;
  errors: AttemptError[];
  assertions: Array<Record<string, unknown>>;
  judge: Record<string, unknown> | null;
  usage: Record<string, unknown> | null;
  timing: Record<string, unknown> | null;
  trace_complete: boolean;
  artifacts: Array<Record<string, unknown>>;
  reviews: Array<Record<string, unknown>>;
  /** v2 判分结果：任务结论、五维度与证据引用。 */
  evaluation?: AttemptEvaluation | null;
  /** 证据覆盖度：缺证必须显式，不能默认完整。 */
  coverage?: EvaluationCoverage | null;
  evidence_refs?: string[];
};

/** 列表接口 /runs/{id}/attempts 返回轻量结构：errors/final_answer 等在 result 里，
 * 与详情接口 /attempts/{id} 的完整 AttemptRead 不同；归一化成 Attempt 供 UI 消费。 */
type AttemptListItem = Omit<
  Attempt,
  | "final_answer"
  | "stop_reason"
  | "errors"
  | "assertions"
  | "judge"
  | "usage"
  | "timing"
  | "trace_complete"
  | "artifacts"
  | "reviews"
> &
  Partial<
    Pick<
      Attempt,
      | "final_answer"
      | "stop_reason"
      | "errors"
      | "assertions"
      | "judge"
      | "usage"
      | "timing"
      | "trace_complete"
      | "artifacts"
      | "reviews"
    >
  > & {
    result?: Partial<
    Pick<
      Attempt,
      | "final_answer"
      | "stop_reason"
      | "errors"
      | "assertions"
      | "judge"
      | "usage"
      | "timing"
      | "trace_complete"
    >
  > | null;
};

function normalizeAttempt(item: AttemptListItem): Attempt {
  const result = item.result ?? {};
  return {
    ...item,
    final_answer: item.final_answer ?? result.final_answer ?? null,
    stop_reason: item.stop_reason ?? result.stop_reason ?? null,
    errors: item.errors ?? result.errors ?? [],
    assertions: item.assertions ?? result.assertions ?? [],
    judge: item.judge ?? result.judge ?? null,
    usage: item.usage ?? result.usage ?? null,
    timing: item.timing ?? result.timing ?? null,
    trace_complete: item.trace_complete ?? result.trace_complete ?? false,
    artifacts: item.artifacts ?? [],
    reviews: item.reviews ?? [],
  };
}

export type TraceEvent = {
  schema_version: 1;
  attempt_id: string;
  seq: number;
  event_id: string;
  type: string;
  payload?: Record<string, unknown>;
};

export type TracePage = {
  data: TraceEvent[];
  next_after_seq: number;
  has_more: boolean;
  sealed: boolean;
};

export type Coverage = "complete" | "partial" | "unavailable";

export type ExecutionNode = {
  id: string;
  kind: "agent_activity" | "tool_call" | "model_call" | "judge" | "output";
  agent_id: string | null;
  agent_name: string | null;
  execution_id: string | null;
  parent_execution_id: string | null;
  call_id: string | null;
  model: string | null;
  status: "running" | "succeeded" | "failed" | "cancelled" | "unknown";
  started_at: string | null;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  usage_source: "reported" | "estimated" | "mixed" | "unavailable";
  evidence_refs: string[];
  /** Additive display metadata. Older servers may omit these fields. */
  label?: string | null;
  tool_name?: string | null;
  phase_id?: string | null;
  input_summary?: string | null;
  output_summary?: string | null;
};

export type ExecutionEdge = {
  id: string;
  from: string;
  to: string;
  kind: "delegation" | "invocation" | "sequence" | "return";
};

export type ExecutionCoverage = {
  trace: Coverage;
  identity: Coverage;
  usage_attribution: Coverage;
  reasons: string[];
};

export type ExecutionPage = {
  data: { nodes: ExecutionNode[]; edges: ExecutionEdge[] };
  next_after_seq: number;
  has_more: boolean;
  sealed: boolean;
  coverage: ExecutionCoverage;
};

export type ToolCallStatus = "running" | "succeeded" | "failed" | "cancelled" | "unknown";

export type ToolCall = {
  call_id: string;
  attempt_id: string;
  execution_id: string | null;
  phase_id: string | null;
  tool_name: string;
  agent_id: string | null;
  agent_name: string | null;
  status: ToolCallStatus;
  started_at: string | null;
  duration_ms: number | null;
  input_summary: string | null;
  output_summary: string | null;
  error: string | null;
  retry_of_call_id?: string | null;
};

export type ToolCallsFilter = {
  execution_id?: string;
  phase_id?: string;
  name?: string;
  status?: ToolCallStatus;
  q?: string;
  after_key?: string;
  limit?: number;
};

export type ToolCallPage = {
  data: ToolCall[];
  next_after_key: string | null;
  total_count: number;
  coverage?: Coverage;
};

export type UsageGroupBy = "role" | "agent" | "model";

export type UsageRow = {
  key: string;
  agent_id: string | null;
  agent_name: string | null;
  role: "agent" | "judge";
  provider: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  physical_requests: number;
  missing_usage_calls: number;
  source: "reported" | "estimated" | "mixed" | "unavailable";
  coverage: Coverage;
};

export type UsageTotals = {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  physical_requests: number;
  missing_usage_calls?: number;
};

export type UsagePage = {
  data: UsageRow[];
  totals: UsageTotals;
  next_after_key: string | null;
  coverage: Coverage | { usage_attribution: Coverage; reasons?: string[] };
};

export type Profile = {
  id: string;
  label: string;
  provider: string;
  model: string;
  kind: "agent" | "judge" | "both";
};

export type Suite = {
  id: string;
  revision: number;
  case_count: number;
  name: string;
  description: string;
  tags: string[];
  created_at: string;
  updated_at: string;
  is_deleted: boolean;
};

export type EvaluationCase = {
  id: string;
  revision: number;
  suite_id: string;
  name: string;
  description: string;
  tags: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
  is_deleted: boolean;
};

/** 用例详情：判据编辑页需要的完整定义（evaluation、assertions、量表、限额）。 */
export type EvaluationCaseDetail = EvaluationCase & {
  turns: Array<{ content: string }>;
  execution: {
    tool_mode: string;
    allowed_tools: string[];
    max_iterations: number;
    timeout_s: number;
  };
  assertions: Array<{ id: string; kind: string; required: boolean }>;
  rubric: {
    required: boolean;
    dimensions: Array<{ id: string; name: string; pass_score: number }>;
  } | null;
  evaluation: {
    version: 2;
    required_dimensions: string[];
    required_evidence: string[];
    milestones: Array<{ id: string; weight: number; assertion_ids: string[] }>;
    reference_answer: string | null;
    judge_artifact_paths: string[];
    budgets: Record<string, number | null>;
  } | null;
  dependencies: string[];
  required_inputs: string[];
};

/** gold 样本：人工确认后才允许进入 Judge 校准。 */
export type GoldSample = {
  id: string;
  label_status: "proposed" | "confirmed" | "rejected";
  reviewer: string | null;
  comment: string;
  updated_at: string;
  task?: string;
  expected_verdict?: "passed" | "failed";
  expected_scores?: Array<Record<string, unknown>>;
};

export type Overview = {
  recent_runs: RunSummary[];
  metrics: RunMetrics | null;
  status_counts: Partial<Record<RunStatus, number>>;
  /** 指标来源的运行 ID：总览数字只代表这一次运行，不是全部运行。 */
  metrics_run_id?: string | null;
  /** 指标统计范围：目前是 single_run（单次运行）。 */
  metrics_scope?: string | null;
};

export type TargetKind = "agent" | "team" | "skill" | "dataset";

export type CreateRunInput = {
  suite_id: string;
  case_ids: string[];
  agent_profile_id: string;
  judge_profile_id?: string | null;
  target?: { kind: TargetKind; id: string } | null;
  run_timeout_s?: number | null;
};

export type RunsFilter = {
  status?: RunStatus;
  suite_id?: string;
  parent_run_id?: string;
};

export type DatasetImportResult = {
  suite: Suite;
  case_count: number;
  errors: Array<Record<string, unknown>>;
};

export type RunExportFormat = "json" | "csv";

export class EvaluationApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message || code);
    this.name = "EvaluationApiError";
  }
}

function evaluationBase(): string {
  if (typeof window === "undefined") {
    return `http://127.0.0.1:${EVAL_PORT}/api/eval/v1`;
  }
  const host = window.location.hostname || "127.0.0.1";
  return `${window.location.protocol}//${host}:${EVAL_PORT}/api/eval/v1`;
}

let csrfToken = "";
let sessionPromise: Promise<void> | null = null;

export function resetEvaluationSession(): void {
  csrfToken = "";
  sessionPromise = null;
}

function idempotencyKey(): string {
  const webCrypto = globalThis.crypto;
  if (webCrypto && typeof webCrypto.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  return `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function ensureSession(): Promise<void> {
  if (csrfToken) return;
  if (!sessionPromise) {
    sessionPromise = (async () => {
      const base = evaluationBase();
      let res = await fetch(`${base}/auth/session`, { credentials: "include" });
      if (!res.ok) {
        const secret = loadSavedSecret();
        if (!secret) {
          throw new EvaluationApiError("EVAL_OFFLINE", "evaluation-auth", 401);
        }
        res = await fetch(`${base}/auth/login`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ passphrase: secret }),
        });
        if (!res.ok) {
          throw new EvaluationApiError("EVAL_AUTH_FAILED", "evaluation-auth", res.status);
        }
      }
      const body = (await res.json()) as { csrf_token?: string };
      csrfToken = body.csrf_token ?? "";
      if (!csrfToken) {
        throw new EvaluationApiError("EVAL_AUTH_FAILED", "evaluation-auth", res.status);
      }
    })().catch((err: unknown) => {
      sessionPromise = null;
      throw err;
    });
  }
  return sessionPromise;
}

function errorFromResponse(res: Response, body: unknown): EvaluationApiError {
  const contract = (body as { error?: { code?: string; message?: string } } | null)?.error;
  return new EvaluationApiError(
    contract?.code ?? "EVAL_ERROR",
    contract?.message || `HTTP ${res.status}`,
    res.status,
  );
}

async function parseError(res: Response): Promise<EvaluationApiError> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // 非 JSON 错误体（如网关层 text/plain），保留默认错误码。
  }
  return errorFromResponse(res, body);
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  await ensureSession();
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string> | undefined),
    "X-CSRF-Token": csrfToken,
  };
  const res = await fetch(`${evaluationBase()}${path}`, { ...init, credentials: "include", headers });
  if (res.status === 401 && retry) {
    resetEvaluationSession();
    return request<T>(path, init, false);
  }
  if (!res.ok) {
    throw await parseError(res);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

function contentDispositionFilename(res: Response, fallback: string): string {
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  return match?.[1] ?? fallback;
}

async function requestDownload(path: string, fallbackName: string): Promise<void> {
  await ensureSession();
  const res = await fetch(`${evaluationBase()}${path}`, {
    credentials: "include",
    headers: { "X-CSRF-Token": csrfToken },
  });
  if (res.status === 401) {
    resetEvaluationSession();
    return requestDownload(path, fallbackName);
  }
  if (!res.ok) {
    throw await parseError(res);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = contentDispositionFilename(res, fallbackName);
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function withQuery(path: string, params: Record<string, string | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) query.set(key, value);
  }
  const encoded = query.toString();
  return encoded ? `${path}?${encoded}` : path;
}

export const evaluationApi = {
  overview(): Promise<Overview> {
    return request<Overview>("/overview");
  },

  listRuns(filter: RunsFilter = {}): Promise<{ data: RunSummary[] }> {
    return request<{ data: RunSummary[] }>(
      withQuery("/runs", {
        status: filter.status,
        suite_id: filter.suite_id,
        parent_run_id: filter.parent_run_id,
      }),
    );
  },

  getRun(id: string): Promise<Run> {
    return request<Run>(`/runs/${encodeURIComponent(id)}`);
  },

  listAttempts(runId: string): Promise<{ data: Attempt[] }> {
    return request<{ data: AttemptListItem[] }>(
      `/runs/${encodeURIComponent(runId)}/attempts`,
    ).then((page) => ({ data: page.data.map(normalizeAttempt) }));
  },

  getAttempt(id: string): Promise<Attempt> {
    return request<Attempt>(`/attempts/${encodeURIComponent(id)}`);
  },

  getTrace(attemptId: string, afterSeq = 0, limit = 200): Promise<TracePage> {
    return request<TracePage>(
      `/attempts/${encodeURIComponent(attemptId)}/trace?after_seq=${afterSeq}&limit=${limit}`,
    );
  },

  getExecution(attemptId: string, afterSeq = 0, limit = 200): Promise<ExecutionPage> {
    return request<ExecutionPage>(
      `/attempts/${encodeURIComponent(attemptId)}/execution?after_seq=${afterSeq}&limit=${limit}`,
    );
  },

  listToolCalls(attemptId: string, filter: ToolCallsFilter = {}): Promise<ToolCallPage> {
    return request<ToolCallPage>(withQuery(
      `/attempts/${encodeURIComponent(attemptId)}/tool-calls`,
      {
        execution_id: filter.execution_id,
        phase_id: filter.phase_id,
        name: filter.name,
        status: filter.status,
        q: filter.q,
        after_key: filter.after_key,
        limit: String(filter.limit ?? 50),
      },
    ));
  },

  getRunUsage(
    runId: string,
    filter: { group_by?: UsageGroupBy; after_key?: string; limit?: number } = {},
  ): Promise<UsagePage> {
    return request<UsagePage>(withQuery(
      `/runs/${encodeURIComponent(runId)}/usage`,
      {
        group_by: filter.group_by ?? "role",
        after_key: filter.after_key,
        limit: String(filter.limit ?? 50),
      },
    ));
  },

  createRun(input: CreateRunInput): Promise<Run> {
    return request<Run>("/runs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey(),
      },
      body: JSON.stringify(input),
    });
  },

  cancelRun(id: string): Promise<Run> {
    return request<Run>(`/runs/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey() },
      body: "{}",
    });
  },

  rerunRun(runId: string, attemptIds: string[]): Promise<Run> {
    return request<Run>(`/runs/${encodeURIComponent(runId)}/rerun`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey(),
      },
      body: JSON.stringify({ attempt_ids: attemptIds }),
    });
  },

  /** 追加人工复核结论：自动结论不会被这条路径改写。 */
  addReview(
    attemptId: string,
    payload: { verdict: "passed" | "failed"; comment?: string }
  ): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>(
      `/attempts/${encodeURIComponent(attemptId)}/reviews`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      }
    );
  },

  listGoldSamples(): Promise<{ gold_revision: number; data: GoldSample[] }> {
    return request<{ gold_revision: number; data: GoldSample[] }>(
      "/calibration-samples"
    );
  },

  /** 人工确认 gold：只有字面量 true 才会被服务端接受。 */
  confirmGoldSample(
    sampleId: string,
    payload: {
      base_gold_revision: number;
      expected_verdict: "passed" | "failed";
      expected_scores: Array<Record<string, unknown>>;
      comment?: string;
    }
  ): Promise<GoldSample> {
    return request<GoldSample>(
      `/calibration-samples/${encodeURIComponent(sampleId)}`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ...payload, confirm: true }),
      }
    );
  },

  createCalibration(payload: {
    judge_profile_id: string;
    gold_revision: number;
    sample_ids: string[];
  }): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>("/calibrations", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey(),
      },
      body: JSON.stringify(payload),
    });
  },

  getRunMetrics(runId: string): Promise<RunMetricsView> {
    return request<RunMetricsView>(`/runs/${runId}/metrics`);
  },

  createComparison(input: {
    baseline_run_id: string;
    candidate_run_id: string;
    mode?: "strict";
  }): Promise<Comparison> {
    return request<Comparison>("/comparisons", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey(),
      },
      body: JSON.stringify(input),
    });
  },

  getComparison(id: string): Promise<Comparison> {
    return request<Comparison>(`/comparisons/${id}`);
  },

  listSuites(): Promise<{ data: Suite[] }> {
    return request<{ data: Suite[] }>("/suites");
  },

  listCases(suiteId: string): Promise<{ data: EvaluationCase[] }> {
    return request<{ data: EvaluationCase[] }>(`/cases?suite_id=${encodeURIComponent(suiteId)}`);
  },

  getCase(caseId: string): Promise<EvaluationCaseDetail> {
    return request<EvaluationCaseDetail>(`/cases/${caseId}`);
  },

  /** 保存用例判据：后端按 base_revision 做 CAS，落库即形成新 revision。 */
  updateCase(
    caseId: string,
    payload: Record<string, unknown>
  ): Promise<EvaluationCaseDetail> {
    return request<EvaluationCaseDetail>(`/cases/${caseId}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
  },

  listProfiles(): Promise<{ data: Profile[] }> {
    return request<{ data: Profile[] }>("/profiles");
  },

  importBasicSuite(payload: { suite: Record<string, unknown>; cases: Array<Record<string, unknown>> }): Promise<Suite> {
    return request<Suite>("/suites/import", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey(),
      },
      body: JSON.stringify({ schema_version: 1, ...payload }),
    });
  },

  downloadRunExport(runId: string, format: RunExportFormat): Promise<void> {
    return requestDownload(
      `/runs/${encodeURIComponent(runId)}/export?format=${format}`,
      `evaluation-run-${runId.slice(0, 8)}.${format}`,
    );
  },

  downloadRunReport(runId: string): Promise<void> {
    return requestDownload(
      `/runs/${encodeURIComponent(runId)}/report`,
      `evaluation-run-${runId.slice(0, 8)}.html`,
    );
  },

  downloadDatasetTemplate(): Promise<void> {
    return requestDownload("/datasets/template", "evaluation-dataset-template.xlsx");
  },

  async importDataset(file: File): Promise<DatasetImportResult> {
    const form = new FormData();
    form.append("file", file);
    return request<DatasetImportResult>("/datasets/import", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey() },
      body: form,
    });
  },

  exportDatasetXlsx(suiteId: string): Promise<void> {
    return requestDownload(
      `/suites/${encodeURIComponent(suiteId)}/export.xlsx`,
      `evaluation-suite-${suiteId.slice(0, 8)}.xlsx`,
    );
  },

  downloadSummaryReport(suiteId: string): Promise<void> {
    return requestDownload(
      `/reports/summary?suite_id=${encodeURIComponent(suiteId)}`,
      `evaluation-summary-${suiteId.slice(0, 8)}.html`,
    );
  },
};
