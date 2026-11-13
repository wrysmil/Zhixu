import { request } from "@/lib/api";

import {
  parseBodyPage,
  parseEventPage,
  parseRecordPage,
  parseSummary,
} from "./parser";
import type {
  EventPage,
  RecordPage,
  TrajectoryBodyPage,
  TrajectorySummary,
} from "./types";

const BASE = "/api/trajectory/sessions";

function sessionPath(sessionKey: string): string {
  return `${BASE}/${encodeURIComponent(sessionKey)}`;
}

export interface TrajectoryClientOptions {
  getToken: () => string;
}

export class TrajectoryClient {
  constructor(private readonly options: TrajectoryClientOptions) {}

  private get authToken(): string {
    return this.options.getToken();
  }

  async summary(sessionKey: string, signal?: AbortSignal): Promise<TrajectorySummary> {
    const raw = await request<unknown>(
      sessionPath(sessionKey), this.authToken, { signal }, 20_000,
    );
    const parsed = parseSummary(raw);
    if (!parsed) throw new Error("TRAJECTORY_INVALID_RESPONSE: summary");
    return parsed;
  }

  async records(
    sessionKey: string,
    options: { beforeCursor?: string | null; limit?: number } = {},
    signal?: AbortSignal,
  ): Promise<RecordPage> {
    const params = new URLSearchParams();
    if (options.beforeCursor) params.set("beforeCursor", options.beforeCursor);
    params.set("limit", String(options.limit ?? 200));
    const raw = await request<unknown>(
      `${sessionPath(sessionKey)}/records?${params.toString()}`,
      this.authToken,
      { signal },
      20_000,
    );
    const parsed = parseRecordPage(raw);
    if (!parsed) throw new Error("TRAJECTORY_INVALID_RESPONSE: records");
    return parsed;
  }

  async events(
    sessionKey: string,
    options: { afterSeq?: number; beforeSeq?: number; limit?: number } = {},
    signal?: AbortSignal,
  ): Promise<EventPage> {
    const params = new URLSearchParams();
    if (options.afterSeq != null) params.set("afterSeq", String(options.afterSeq));
    if (options.beforeSeq != null) params.set("beforeSeq", String(options.beforeSeq));
    params.set("limit", String(options.limit ?? 200));
    const raw = await request<unknown>(
      `${sessionPath(sessionKey)}/events?${params.toString()}`,
      this.authToken,
      { signal },
      20_000,
    );
    const parsed = parseEventPage(raw);
    if (!parsed) throw new Error("TRAJECTORY_INVALID_RESPONSE: events");
    return parsed;
  }

  async body(
    sessionKey: string,
    bodyId: string,
    options: { offsetBytes?: number; limitBytes?: number } = {},
    signal?: AbortSignal,
  ): Promise<TrajectoryBodyPage> {
    const params = new URLSearchParams({
      offsetBytes: String(options.offsetBytes ?? 0),
      limitBytes: String(options.limitBytes ?? 65_536),
    });
    const raw = await request<unknown>(
      `${sessionPath(sessionKey)}/bodies/${encodeURIComponent(bodyId)}?${params.toString()}`,
      this.authToken,
      { signal },
      20_000,
    );
    const parsed = parseBodyPage(raw);
    if (!parsed) throw new Error("TRAJECTORY_INVALID_RESPONSE: body");
    return parsed;
  }
}
