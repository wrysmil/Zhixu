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
};

export type RunCounts = Partial<Record<Verdict, number>>;

export type Run = RunSummary & {
  snapshot: Record<string, unknown>;
  counts: RunCounts;
  metrics: RunMetrics | null;
  attempts_url: string;
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

export type Overview = {
  recent_runs: RunSummary[];
  metrics: RunMetrics | null;
  status_counts: Partial<Record<RunStatus, number>>;
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

  listSuites(): Promise<{ data: Suite[] }> {
    return request<{ data: Suite[] }>("/suites");
  },

  listCases(suiteId: string): Promise<{ data: EvaluationCase[] }> {
    return request<{ data: EvaluationCase[] }>(`/cases?suite_id=${encodeURIComponent(suiteId)}`);
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
