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
});
