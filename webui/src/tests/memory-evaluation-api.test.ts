import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EvaluationApiError, resetEvaluationSession } from "@/lib/evaluation-api";
import {
  memoryDatasetIsReady,
  memoryEvaluationApi,
  memoryExperimentIsTerminal,
  memoryImportSucceeded,
  type MemoryExperimentCreate,
} from "@/lib/memory-evaluation-api";

function jsonResponse(
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function experimentInput(): MemoryExperimentCreate {
  return {
    dataset_revision_id: "rev-1",
    split: "regression",
    track: "end_to_end",
    candidates: [
      {
        id: "cand-1",
        name: "当前候选",
        mode: "zhixu",
        agent_profile_id: "agent-1",
        memory_settings: { extraction_enabled: true },
      },
    ],
    repetitions: 1,
    model_mode: "real",
    budgets: { max_trials: 10 },
    primary_metric: "qa_accuracy_scenario_macro",
    policy: { fact_matching_version: "fact-matching/v1" },
  };
}

/** 受控 XHR：能模拟 upload 进度、成功响应、401 与中止。 */
class FakeXhr {
  static instances: FakeXhr[] = [];

  upload: { onprogress: ((event: ProgressEvent) => void) | null } = {
    onprogress: null,
  };
  status = 0;
  responseText = "";
  withCredentials = false;
  headers: Record<string, string> = {};
  aborted = false;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  open(): void {
    // no-op
  }

  setRequestHeader(name: string, value: string): void {
    this.headers[name] = value;
  }

  send(): void {
    FakeXhr.instances.push(this);
  }

  abort(): void {
    this.aborted = true;
    this.onabort?.();
  }

  progress(loaded: number, total: number): void {
    this.upload.onprogress?.({
      lengthComputable: true,
      loaded,
      total,
    } as ProgressEvent);
  }

  respond(status: number, body: unknown): void {
    this.status = status;
    this.responseText = JSON.stringify(body);
    this.onload?.();
  }
}

describe("memory-evaluation-api", () => {
  beforeEach(() => {
    resetEvaluationSession();
    FakeXhr.instances = [];
    vi.stubGlobal("fetch", vi.fn());
    window.localStorage.setItem("zhixu-webui.bootstrap-secret", "secret-1");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("lists datasets with split filter and opaque cursor", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ data: [{ id: "rev-1" }], next_after_key: "opaque+/=" }),
    );

    const page = await memoryEvaluationApi.listDatasets({
      split: "regression",
      limit: 25,
      after_key: "a b",
    });

    const url = new URL(String(fetchMock.mock.calls[1][0]));
    expect(url.pathname).toContain("/memory/datasets");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      after_key: "a b",
      limit: "25",
      split: "regression",
    });
    expect(page.next_after_key).toBe("opaque+/=");
  });

  it("encodes attempt event cursors explicitly", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        data: [],
        next_after_seq: 0,
        has_more: false,
        sealed: false,
      }),
    );

    await memoryEvaluationApi.listAttemptEvents("attempt/1", {
      after_seq: 12,
      limit: 50,
    });

    const url = new URL(String(fetchMock.mock.calls[1][0]));
    expect(url.pathname).toContain("/memory/attempts/attempt%2F1/events");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      after_seq: "12",
      limit: "50",
    });
  });

  it("reuses one idempotency key across the 401 retry", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "AUTH_REQUIRED", message: "" } }, 401),
    );
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-2" }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ id: "exp-1", status: "queued", planned_trials: 3, child_runs: [] }, 202),
    );

    const created = await memoryEvaluationApi.createExperiment(experimentInput());

    expect(created.id).toBe("exp-1");
    const first = fetchMock.mock.calls[1][1];
    const retried = fetchMock.mock.calls[3][1];
    const firstKey = (first?.headers as Record<string, string>)["Idempotency-Key"];
    const retriedKey = (retried?.headers as Record<string, string>)["Idempotency-Key"];
    expect(firstKey).toBeTruthy();
    expect(retriedKey).toBe(firstKey);
    // 重试用的是新会话的 CSRF，而不是把过期令牌再发一遍。
    expect((retried?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-2");
  });

  it("streams the upload with progress and sends CSRF + idempotency key", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));

    const progress: number[] = [];
    const file = new File(["{}"], "core.jsonl", { type: "application/x-ndjson" });
    const pending = memoryEvaluationApi.importDataset(
      {
        dataset_version: "uploaded-v1",
        format: "jsonl",
        source: {
          repo_url: "local://tests",
          revision: "uploaded-v1",
          sha256: "a".repeat(64),
          license: "CC-BY-4.0",
          original_ids: [],
          loader_version: "memory-dataset-loader/v1",
          grading_protocol: "memory-gold/v1",
        },
        file,
      },
      { onProgress: (handle) => progress.push(handle.percent) },
    );

    await vi.waitFor(() => expect(FakeXhr.instances).toHaveLength(1));
    const xhr = FakeXhr.instances[0];
    expect(xhr.withCredentials).toBe(true);
    expect(xhr.headers["X-CSRF-Token"]).toBe("csrf-1");
    expect(xhr.headers["Idempotency-Key"]).toBeTruthy();

    xhr.progress(50, 100);
    xhr.respond(202, {
      import_id: "imp-1",
      status: "ready",
      dataset_revision_id: "rev-1",
      dataset_version: "uploaded-v1",
      scenario_count: 1,
      errors: [],
    });
    const result = await pending;

    expect(result.dataset_revision_id).toBe("rev-1");
    expect(progress).toEqual([50]);
  });

  it("retries an upload after 401 with the same key (no double charge)", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: { code: "AUTH_REQUIRED" } }, 401));
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-2" }));

    const file = new File(["{}"], "core.jsonl");
    const pending = memoryEvaluationApi.importDataset(
      {
        dataset_version: "uploaded-v1",
        format: "jsonl",
        source: {
          repo_url: "local://tests",
          revision: "uploaded-v1",
          sha256: "a".repeat(64),
          license: "CC-BY-4.0",
          original_ids: [],
          loader_version: "memory-dataset-loader/v1",
          grading_protocol: "memory-gold/v1",
        },
        file,
      },
    );

    await vi.waitFor(() => expect(FakeXhr.instances).toHaveLength(1));
    const first = FakeXhr.instances[0];
    first.respond(401, { error: { code: "AUTH_REQUIRED", message: "" } });

    await vi.waitFor(() => expect(FakeXhr.instances).toHaveLength(2));
    const retried = FakeXhr.instances[1];
    expect(retried.headers["Idempotency-Key"]).toBe(first.headers["Idempotency-Key"]);
    expect(retried.headers["X-CSRF-Token"]).toBe("csrf-2");
    retried.respond(202, {
      import_id: "imp-1",
      status: "ready",
      dataset_revision_id: "rev-1",
      dataset_version: "uploaded-v1",
      scenario_count: 1,
      errors: [],
    });
    await expect(pending).resolves.toMatchObject({ import_id: "imp-1" });
  });

  it("polls until a terminal status instead of trusting the first response", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "exp-1", status: "queued" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "exp-1", status: "completed" }));

    const seen: string[] = [];
    const detail = await memoryEvaluationApi.pollExperiment("exp-1", {
      intervalMs: 1,
      onUpdate: (update) => seen.push(update.status),
    });

    expect(seen).toEqual(["queued", "completed"]);
    expect(detail.status).toBe("completed");
  });

  it("stops polling when the AbortSignal fires", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "exp-1", status: "running" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "exp-1", status: "running" }));

    const controller = new AbortController();
    const pending = memoryEvaluationApi.pollExperiment("exp-1", {
      intervalMs: 20,
      signal: controller.signal,
      onUpdate: () => controller.abort(),
    });

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    const callsAfterAbort = fetchMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 30));
    // 取消之后不再发下一次请求：轮询不能变成后台无限请求。
    expect(fetchMock.mock.calls.length).toBe(callsAfterAbort);
  });

  it("never treats unknown statuses as success", () => {
    expect(memoryExperimentIsTerminal("weird-status")).toBe(false);
    expect(memoryExperimentIsTerminal(null)).toBe(false);
    expect(memoryExperimentIsTerminal("cancelled")).toBe(true);
    expect(memoryImportSucceeded("running")).toBe(false);
    expect(memoryImportSucceeded("ready")).toBe(true);
    expect(memoryDatasetIsReady("staging")).toBe(false);
    expect(memoryDatasetIsReady("ready")).toBe(true);
  });

  it("keeps the legacy sample-import shape while adding the revision id", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ suite_id: "s1", memory_dataset_revision_id: "rev-1" }, 201),
    );
    const memorySample = await memoryEvaluationApi.importSample("memory-smoke-v1");
    expect(memorySample.suite_id).toBe("s1");
    expect(memorySample.memory_dataset_revision_id).toBe("rev-1");

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ suite_id: "s2", case_count: 4 }, 201),
    );
    const legacy = await memoryEvaluationApi.importSample("basic-v1");
    expect(legacy.suite_id).toBe("s2");
    expect(legacy.memory_dataset_revision_id).toBeUndefined();
  });

  it("maps contract error bodies to EvaluationApiError", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { error: { code: "MEMORY_EXPERIMENT_NOT_FOUND", message: "记忆实验不存在" } },
        404,
      ),
    );
    const error = await memoryEvaluationApi.getExperiment("missing").then(
      () => null,
      (err: unknown) => err,
    );
    expect(error).toBeInstanceOf(EvaluationApiError);
    expect(error).toMatchObject({
      code: "MEMORY_EXPERIMENT_NOT_FOUND",
      status: 404,
    });
  });
});
