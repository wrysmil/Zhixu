import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EvaluationApiError, evaluationApi, resetEvaluationSession } from "@/lib/evaluation-api";

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

describe("evaluation-api", () => {
  beforeEach(() => {
    resetEvaluationSession();
    vi.stubGlobal("fetch", vi.fn());
    window.localStorage.setItem("zhixu-webui.bootstrap-secret", "secret-1");
  });
  afterEach(() => vi.unstubAllGlobals());

  it("logs in with the saved bootstrap secret and reuses csrf", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: { code: "AUTH_REQUIRED" } }, 401));
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [] }));
    await expect(evaluationApi.listSuites()).resolves.toEqual({ data: [] });

    const sessionCall = fetchMock.mock.calls[0];
    expect(String(sessionCall[0])).toContain("/auth/session");
    const loginCall = fetchMock.mock.calls[1];
    expect(String(loginCall[0])).toContain("/auth/login");
    expect(JSON.parse(String(loginCall[1]?.body))).toEqual({ passphrase: "secret-1" });
    expect(fetchMock.mock.calls[2][0]).toContain("/suites");
    expect(fetchMock.mock.calls[2][1]?.headers).toMatchObject({ "X-CSRF-Token": "csrf-1" });

    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [] }));
    await evaluationApi.listRuns();
    // 第二次请求复用会话：不再出现 /auth/session 或 /auth/login。
    expect(fetchMock.mock.calls[3][0]).toContain("/runs");
    expect(fetchMock.mock.calls.slice(0, 4).map((call) => String(call[0])))
      .not.toContain(expect.stringContaining("/auth/"));
  });

  it("retries once after 401", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: { code: "AUTH_REQUIRED", message: "" } }, 401));
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-2" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [{ id: "r1" }] }));
    await expect(evaluationApi.listRuns()).resolves.toEqual({ data: [{ id: "r1" }] });
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(fetchMock.mock.calls[3][1]?.headers).toMatchObject({ "X-CSRF-Token": "csrf-2" });
  });

  it("maps contract error body to EvaluationApiError", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "VALIDATION_ERROR", message: "suite_id" } }, 422),
    );
    const error = await evaluationApi.listSuites().then(
      () => null,
      (err: unknown) => err,
    );
    expect(error).toBeInstanceOf(EvaluationApiError);
    expect(error).toMatchObject({ code: "VALIDATION_ERROR", message: "suite_id", status: 422 });
  });

  it("sends an idempotency key on write requests", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "r9" }, 202));
    await evaluationApi.cancelRun("run-1");
    const cancelCall = fetchMock.mock.calls[1];
    expect(String(cancelCall[0])).toContain("/runs/run-1/cancel");
    expect(cancelCall[1]?.headers).toHaveProperty("Idempotency-Key");
  });

  it("loads execution pages with explicit cursor and limit", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({
      data: { nodes: [], edges: [] },
      next_after_seq: 80,
      has_more: true,
      sealed: false,
      coverage: {
        trace: "partial",
        identity: "unavailable",
        usage_attribution: "unavailable",
        reasons: ["run_active"],
      },
    }));

    await evaluationApi.getExecution("attempt / 1", 20, 60);

    expect(String(fetchMock.mock.calls[1][0])).toContain(
      "/attempts/attempt%20%2F%201/execution?after_seq=20&limit=60",
    );
  });

  it("encodes tool filters and the opaque paging cursor", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [], next_after_key: null, total_count: 0 }));

    await evaluationApi.listToolCalls("attempt-1", {
      execution_id: "exec/child",
      phase_id: "research phase",
      name: "web_search",
      status: "failed",
      q: "call #19",
      after_key: "opaque+/=",
      limit: 25,
    });

    const url = new URL(String(fetchMock.mock.calls[1][0]));
    expect(url.pathname).toContain("/attempts/attempt-1/tool-calls");
    expect(Object.fromEntries(url.searchParams)).toMatchObject({
      execution_id: "exec/child",
      phase_id: "research phase",
      name: "web_search",
      status: "failed",
      q: "call #19",
      after_key: "opaque+/=",
      limit: "25",
    });
  });

  it("loads the run usage ledger and downloads current-run HTML", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({
      data: [],
      totals: { input_tokens: 0, output_tokens: 0, total_tokens: 0, physical_requests: 0 },
      next_after_key: null,
      coverage: "complete",
    }));
    await evaluationApi.getRunUsage("run-1", { group_by: "agent", limit: 50 });
    expect(String(fetchMock.mock.calls[1][0])).toContain(
      "/runs/run-1/usage?group_by=agent&limit=50",
    );

    fetchMock.mockResolvedValueOnce(new Response("<html></html>", {
      headers: {
        "Content-Type": "text/html",
        "Content-Disposition": 'attachment; filename="run-report.html"',
      },
    }));
    const createObjectURL = vi.fn(() => "blob:report");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    await evaluationApi.downloadRunReport("run-1");

    expect(String(fetchMock.mock.calls[2][0])).toContain("/runs/run-1/report");
    expect(click).toHaveBeenCalled();
    click.mockRestore();
  });
});
