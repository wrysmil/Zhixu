// 普通子 Agent 执行的只读 HTTP 通道（契约 §3）。没有 mutation。
import { request } from "@/lib/api";
import type {
  ExecutionDetailResponse,
  ExecutionEventsResponse,
  ExecutionListResponse,
} from "./types";

const EXECUTIONS_BASE = "/api/subagent-executions";
const READ_TIMEOUT_MS = 20_000;

export interface ExecutionListOptions {
  base?: string;
  parentTurnId?: string | null;
  limit?: number;
  cursor?: string | null;
}

export interface ExecutionEventPageOptions {
  base?: string;
  afterSeq?: number;
  throughSeq?: number;
  limit?: number;
}

function queryString(params: URLSearchParams): string {
  const value = params.toString();
  return value ? `?${value}` : "";
}

export async function listSubagentExecutions(
  token: string,
  chatId: string,
  options: ExecutionListOptions = {},
): Promise<ExecutionListResponse> {
  const params = new URLSearchParams({ chatId });
  if (options.parentTurnId) params.set("parentTurnId", options.parentTurnId);
  if (options.cursor) params.set("cursor", options.cursor);
  params.set("limit", String(options.limit ?? 20));
  return request<ExecutionListResponse>(
    `${options.base ?? ""}${EXECUTIONS_BASE}${queryString(params)}`,
    token,
    undefined,
    READ_TIMEOUT_MS,
  );
}

export async function loadSubagentExecution(
  token: string,
  invocationId: string,
  options: { base?: string } = {},
): Promise<ExecutionDetailResponse> {
  return request<ExecutionDetailResponse>(
    `${options.base ?? ""}${EXECUTIONS_BASE}/${encodeURIComponent(invocationId)}`,
    token,
    undefined,
    READ_TIMEOUT_MS,
  );
}

export async function loadSubagentExecutionEvents(
  token: string,
  invocationId: string,
  options: ExecutionEventPageOptions = {},
): Promise<ExecutionEventsResponse> {
  const params = new URLSearchParams();
  params.set("afterSeq", String(options.afterSeq ?? 0));
  if (options.throughSeq !== undefined) params.set("throughSeq", String(options.throughSeq));
  params.set("limit", String(options.limit ?? 100));
  return request<ExecutionEventsResponse>(
    `${options.base ?? ""}${EXECUTIONS_BASE}/${encodeURIComponent(invocationId)}/events${queryString(params)}`,
    token,
    undefined,
    READ_TIMEOUT_MS,
  );
}

/** 固定错误前缀识别；HTTP 层只保证外层是 `{error: string}`。 */
export function subagentExecutionErrorCode(error: unknown): string | null {
  const message = typeof error === "string"
    ? error
    : error instanceof Error
      ? error.message
      : "";
  const match = message.match(/EXECUTION_(?:NOT_FOUND|STORAGE_UNAVAILABLE|WRITER_BUSY)/);
  return match ? match[0] : null;
}
