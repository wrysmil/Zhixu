import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, RefreshCw } from "lucide-react";

import { MemoryMetricValue } from "@/components/settings/evaluation/memory/MemoryMetricValue";
import { evaluationErrorText } from "@/components/settings/evaluation/evaluation-utils";
import { Button } from "@/components/ui/button";
import { EvaluationApiError } from "@/lib/evaluation-api";
import {
  memoryEvaluationApi,
  type MemoryProbeSummary,
  type MemoryReviewRef,
} from "@/lib/memory-evaluation-api";
import { cn } from "@/lib/utils";

const BASE = "settings.evaluation.memory.replay.";

/** 契约 §4 的 memory.event kind 闭集。 */
const EVENT_KINDS = new Set<string>([
  "session_closed",
  "extraction_completed",
  "extraction_skipped",
  "extraction_failed",
  "memory_changed",
  "index_ready",
  "recall_candidates",
  "reranked",
  "injected",
  "probe_answered",
  "state_snapshot",
  "deletion_checked",
]);

export type MemoryReplayStage =
  | "history"
  | "extraction"
  | "state"
  | "candidates"
  | "rerank"
  | "injected"
  | "answer"
  | "deletion"
  | "unknown";

/** 显示顺序 = 契约 §4 的真实阶段顺序。 */
export const MEMORY_REPLAY_STAGES: readonly MemoryReplayStage[] = [
  "history",
  "extraction",
  "state",
  "candidates",
  "rerank",
  "injected",
  "answer",
  "deletion",
];

/** 缺这些阶段就无法判断「记没记住、找没找到、有没有用上」。 */
export const MEMORY_REPLAY_REQUIRED_STAGES: readonly MemoryReplayStage[] = [
  "history",
  "extraction",
  "state",
  "candidates",
  "rerank",
  "injected",
  "answer",
];

export type MemoryReplayStatus = "ok" | "failed" | "skipped" | "unknown";

export type MemoryReplayRow = {
  seq: number;
  eventId: string;
  kind: string;
  stage: MemoryReplayStage;
  status: MemoryReplayStatus;
  detail: string | null;
  body: string | null;
  bodyTruncated: boolean;
  probeId: string | null;
  sessionId: string | null;
  checkpointId: string | null;
  originRefs: string[];
};

export function memoryReplayStageOf(kind: string): MemoryReplayStage {
  switch (kind) {
    case "session_closed":
      return "history";
    case "extraction_completed":
    case "extraction_skipped":
    case "extraction_failed":
    case "index_ready":
      return "extraction";
    case "memory_changed":
    case "state_snapshot":
      return "state";
    case "recall_candidates":
      return "candidates";
    case "reranked":
      return "rerank";
    case "injected":
      return "injected";
    case "probe_answered":
      return "answer";
    case "deletion_checked":
      return "deletion";
    default:
      return "unknown";
  }
}

export function memoryReplayRows(events: Array<Record<string, unknown>>): MemoryReplayRow[] {
  const rows = events.map((event) => {
    const payload =
      event.payload && typeof event.payload === "object" && !Array.isArray(event.payload)
        ? (event.payload as Record<string, unknown>)
        : {};
    const kind = String(payload.kind ?? event.type ?? "");
    const status: MemoryReplayStatus = !EVENT_KINDS.has(kind)
      ? "unknown"
      : kind.endsWith("_failed")
        ? "failed"
        : kind.endsWith("_skipped")
          ? "skipped"
          : "ok";
    const text = (value: unknown): string | null =>
      typeof value === "string" && value.length > 0 ? value : null;
    return {
      seq: typeof event.seq === "number" ? event.seq : Number(event.seq ?? 0),
      eventId: String(event.event_id ?? ""),
      kind,
      stage: memoryReplayStageOf(kind),
      status,
      detail: text(payload.detail),
      body: text(payload.body),
      bodyTruncated: payload.body_truncated === true,
      probeId: text(payload.probe_id),
      sessionId: text(payload.session_id),
      checkpointId: text(payload.checkpoint_id),
      originRefs: Array.isArray(payload.origin_refs)
        ? payload.origin_refs.map((item) => String(item))
        : [],
    } satisfies MemoryReplayRow;
  });
  rows.sort((left, right) => left.seq - right.seq);
  return rows;
}

/** 没记录到（或记录不完整）的阶段：显示「缺证」，不伪造步骤。 */
export function memoryReplayMissingStages(
  rows: MemoryReplayRow[],
  stages: readonly MemoryReplayStage[] = MEMORY_REPLAY_REQUIRED_STAGES,
): MemoryReplayStage[] {
  const seen = new Set(rows.map((row) => row.stage));
  return stages.filter((stage) => !seen.has(stage));
}

const STATUS_CLASS: Record<MemoryReplayStatus, string> = {
  ok: "border-border/60 bg-muted/30 text-muted-foreground",
  failed: "border-destructive/30 bg-destructive/10 text-destructive",
  skipped: "border-border/60 bg-muted/20 text-muted-foreground",
  unknown: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
};

export type MemoryReplayProps = {
  attemptId: string;
  /** 从比较页进入时只关心这一个场景。 */
  scenarioId?: string | null;
  onBack?: () => void;
  className?: string;
};

export function MemoryReplay({ attemptId, scenarioId, onBack, className }: MemoryReplayProps) {
  const { t } = useTranslation();
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [probes, setProbes] = useState<MemoryProbeSummary[]>([]);
  const [nextSeq, setNextSeq] = useState<number>(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [eventPage, probePage] = await Promise.all([
        memoryEvaluationApi.listAttemptEvents(attemptId, { after_seq: 0, limit: 200 }),
        memoryEvaluationApi.listAttemptProbes(attemptId, { limit: 100 }),
      ]);
      setEvents(eventPage.data);
      setNextSeq(eventPage.next_after_seq);
      setHasMore(eventPage.has_more);
      setProbes(probePage.data);
      setError(null);
    } catch (failure) {
      setError(evaluationErrorText(failure, t));
    } finally {
      setLoading(false);
    }
  }, [attemptId, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const page = await memoryEvaluationApi.listAttemptEvents(attemptId, {
        after_seq: nextSeq,
        limit: 200,
      });
      setEvents((current) => [...current, ...page.data]);
      setNextSeq(page.next_after_seq);
      setHasMore(page.has_more);
      setError(null);
    } catch (failure) {
      setError(evaluationErrorText(failure, t));
    } finally {
      setLoadingMore(false);
    }
  };

  const rows = useMemo(() => memoryReplayRows(events), [events]);
  const missing = memoryReplayMissingStages(rows);
  const filteredProbes = useMemo(
    () => (scenarioId ? probes.filter((probe) => probe.scenario_id === scenarioId) : probes),
    [probes, scenarioId],
  );

  return (
    <section aria-label={t(BASE + "title")} className={cn("min-w-0 space-y-5", className)}>
      <header className="flex flex-wrap items-center gap-2">
        {onBack ? (
          <Button type="button" variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
            {t("settings.evaluation.memory.common.back")}
          </Button>
        ) : null}
        <h3 className="text-base font-semibold text-foreground">{t(BASE + "title")}</h3>
        <span className="text-[12px] text-muted-foreground">
          {t(BASE + "attempt")}: {attemptId}
          {scenarioId ? ` · ${scenarioId}` : ""}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button type="button" variant="ghost" size="sm" onClick={() => void load()}>
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            {t("settings.evaluation.memory.common.refresh")}
          </Button>
        </div>
      </header>

      {error ? (
        <p role="alert" className="rounded-floating border border-destructive/20 bg-destructive/5 px-3 py-2 text-[12px] text-destructive">
          {error}
        </p>
      ) : null}
      {loading ? (
        <p className="text-[13px] text-muted-foreground">
          {t("settings.evaluation.memory.common.loading")}
        </p>
      ) : null}

      {!loading ? (
        <>
          <section className="space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
            <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "stages")}</h4>
            <ul className="flex flex-wrap gap-1.5">
              {MEMORY_REPLAY_STAGES.map((stage) => {
                const count = rows.filter((row) => row.stage === stage).length;
                const required = MEMORY_REPLAY_REQUIRED_STAGES.includes(stage);
                return (
                  <li
                    key={stage}
                    data-testid={`replay-stage-${stage}`}
                    className={cn(
                      "rounded-full border px-2 py-0.5 text-[11px]",
                      count > 0
                        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                        : required
                          ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
                          : "border-border/60 bg-muted/20 text-muted-foreground",
                    )}
                  >
                    {t(BASE + `stage.${stage}`)}
                    {count > 0 ? ` ×${count}` : ` · ${t(BASE + "evidenceMissing")}`}
                    {required ? "" : ` (${t(BASE + "stagesOptional")})`}
                  </li>
                );
              })}
            </ul>
            {missing.length > 0 ? (
              <p data-testid="replay-missing" className="text-[12px] text-amber-700 dark:text-amber-300">
                {t(BASE + "evidenceMissing")}
              </p>
            ) : null}
          </section>

          <section className="space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3">
            <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "timeline")}</h4>
            {rows.length === 0 ? (
              <p data-testid="replay-events-empty" className="text-[12px] text-muted-foreground">
                {t(BASE + "eventsEmpty")}
              </p>
            ) : (
              <ol className="space-y-1.5">
                {rows.map((row) => (
                  <li
                    key={`${row.seq}-${row.eventId}`}
                    data-testid={`replay-event-${row.seq}`}
                    className="rounded-floating border border-border/60 px-2 py-1.5 text-[12px]"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[11px] text-muted-foreground">
                        #{row.seq}
                      </span>
                      <span className="font-medium text-foreground">{t(BASE + `stage.${row.stage}`)}</span>
                      <span className="text-[11px] text-muted-foreground">{row.kind}</span>
                      <span
                        data-status={row.status}
                        className={cn(
                          "rounded-full border px-2 py-0.5 text-[10px]",
                          STATUS_CLASS[row.status],
                        )}
                      >
                        {t(BASE + `eventState.${row.status}`)}
                      </span>
                      {row.probeId ? (
                        <span className="text-[11px] text-muted-foreground">{row.probeId}</span>
                      ) : null}
                      {row.sessionId ? (
                        <span className="text-[11px] text-muted-foreground">{row.sessionId}</span>
                      ) : null}
                      {row.checkpointId ? (
                        <span className="text-[11px] text-muted-foreground">{row.checkpointId}</span>
                      ) : null}
                    </div>
                    {row.detail ? <p className="mt-0.5 text-muted-foreground">{row.detail}</p> : null}
                    {row.body ? (
                      <p className="mt-0.5 whitespace-pre-wrap break-words text-foreground">
                        {row.body}
                        {row.bodyTruncated ? (
                          <span className="ml-1 text-[11px] text-amber-700 dark:text-amber-300">
                            {t(BASE + "bodyTruncated")}
                          </span>
                        ) : null}
                      </p>
                    ) : null}
                    {row.originRefs.length > 0 ? (
                      <p className="mt-0.5 break-all text-[11px] text-muted-foreground">
                        {row.originRefs.join(", ")}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ol>
            )}
            {hasMore ? (
              <Button type="button" variant="ghost" size="sm" disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? t("settings.evaluation.memory.common.loading") : t(BASE + "loadMore")}
              </Button>
            ) : null}
          </section>

          <MemoryProbeReviewPanel
            attemptId={attemptId}
            scenarioId={scenarioId ?? null}
            probes={filteredProbes}
            onRefresh={() => void load()}
          />
        </>
      ) : null}
    </section>
  );
}

const QA_TEXT: Record<string, string> = {
  correct: "qa.correct",
  incorrect: "qa.incorrect",
  unknown: "qa.unknown",
};

const GROUNDED_TEXT: Record<string, string> = {
  yes: "grounded.yes",
  no: "grounded.no",
  unknown: "grounded.unknown",
};

export type MemoryProbeReviewPanelProps = {
  attemptId: string;
  scenarioId?: string | null;
  probes: MemoryProbeSummary[];
  onRefresh?: () => void;
  className?: string;
};

/**
 * 契约 §4：逐 probe 复核复用旧 review 接口，并带
 * `memory_ref={scenario_id, probe_id, metric_id, grading_revision}`。
 *
 * 保存后旧评分与指纹保留（新评分版本单独记录），比较报告保留自己的快照。
 */
export function MemoryProbeReviewPanel({
  attemptId,
  scenarioId,
  probes,
  onRefresh,
  className,
}: MemoryProbeReviewPanelProps) {
  const { t } = useTranslation();
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [messages, setMessages] = useState<Record<string, "submitted" | "conflict" | string>>({});

  const submit = async (probe: MemoryProbeSummary, verdict: "passed" | "failed") => {
    const key = `${probe.scenario_id}:${probe.probe_id}`;
    const metricId =
      targets[key] ?? probe.metrics[0]?.id ?? "";
    const reference: MemoryReviewRef = {
      scenario_id: probe.scenario_id || scenarioId || "",
      probe_id: probe.probe_id,
      metric_id: metricId,
      grading_revision: probe.grading_revision,
    };
    setBusyKey(key);
    try {
      await memoryEvaluationApi.addReview(attemptId, { verdict, memory_ref: reference });
      setMessages((current) => ({ ...current, [key]: "submitted" }));
    } catch (failure) {
      if (
        failure instanceof EvaluationApiError &&
        failure.code === "MEMORY_GRADING_REVISION_CONFLICT"
      ) {
        setMessages((current) => ({ ...current, [key]: "conflict" }));
      } else {
        const text = failure instanceof Error ? failure.message : "";
        setMessages((current) => ({ ...current, [key]: text || "failed" }));
      }
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <section
      aria-label={t(BASE + "probes")}
      className={cn("space-y-2 rounded-panel border border-border/60 bg-settings-surface px-4 py-3", className)}
    >
      <h4 className="text-[13px] font-medium text-foreground">{t(BASE + "probes")}</h4>
      {probes.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">{t(BASE + "probesEmpty")}</p>
      ) : (
        <ul className="space-y-2">
          {probes.map((probe) => {
            const key = `${probe.scenario_id}:${probe.probe_id}`;
            const message = messages[key];
            const qaKey = probe.qa_correct === true ? "correct" : probe.qa_correct === false ? "incorrect" : "unknown";
            const groundedKey =
              probe.grounded_memory_success === true
                ? "yes"
                : probe.grounded_memory_success === false
                  ? "no"
                  : "unknown";
            const ungroundedCorrect = probe.qa_correct === true && probe.grounded_memory_success !== true;
            return (
              <li
                key={key}
                data-testid={`probe-${probe.probe_id}`}
                className="space-y-1.5 rounded-floating border border-border/60 px-2 py-2"
              >
                <div className="flex flex-wrap items-center gap-2 text-[13px]">
                  <span className="font-medium text-foreground">{probe.probe_id}</span>
                  {probe.category ? (
                    <span className="text-[11px] text-muted-foreground">{probe.category}</span>
                  ) : null}
                  <span data-testid={`probe-qa-${probe.probe_id}`} className="text-[12px] text-foreground">
                    {t(BASE + QA_TEXT[qaKey])}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    {t(BASE + "reviewFingerprint", {
                      revision: probe.grading_revision,
                      digest: probe.review_digest ? ` · ${probe.review_digest.slice(0, 8)}` : "",
                    })}
                  </span>
                </div>

                <p className="text-[12px] text-muted-foreground">
                  <MemoryMetricValue
                    value={probe.fact_coverage}
                    unit="ratio"
                    status={probe.fact_coverage === null ? "not_assessable" : "scored"}
                  />
                  {" · "}
                  <span data-testid={`probe-grounded-${probe.probe_id}`} className="text-foreground">
                    {t(BASE + GROUNDED_TEXT[groundedKey])}
                  </span>
                </p>
                {ungroundedCorrect ? (
                  <p
                    data-testid={`probe-ungrounded-${probe.probe_id}`}
                    className="text-[11px] text-amber-700 dark:text-amber-300"
                  >
                    {t(BASE + "grounded.no")}
                  </p>
                ) : null}

                {probe.metrics.length > 0 ? (
                  <div className="space-y-1">
                    <p className="text-[11px] text-muted-foreground">{t(BASE + "reviewTarget")}</p>
                    <div className="flex flex-wrap gap-1.5">
                      {probe.metrics.map((metric) => {
                        const active = (targets[key] ?? probe.metrics[0]?.id ?? "") === metric.id;
                        return (
                          <button
                            key={metric.id}
                            type="button"
                            aria-pressed={active}
                            className={cn(
                              "rounded-full border px-2 py-0.5 text-[11px]",
                              active
                                ? "border-primary/40 bg-primary/10 text-foreground"
                                : "border-border/60 text-muted-foreground hover:text-foreground",
                            )}
                            onClick={() =>
                              setTargets((current) => ({ ...current, [key]: metric.id }))
                            }
                          >
                            {metric.id}:{" "}
                            <MemoryMetricValue value={metric.value} unit={metric.unit} status={metric.status} />
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ) : null}

                <div className="flex flex-wrap items-center gap-1.5">
                  <button
                    type="button"
                    disabled={busyKey === key || probe.metrics.length === 0}
                    className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground disabled:opacity-50"
                    onClick={() => void submit(probe, "passed")}
                  >
                    {t(BASE + "reviewPass")}
                  </button>
                  <button
                    type="button"
                    disabled={busyKey === key || probe.metrics.length === 0}
                    className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground disabled:opacity-50"
                    onClick={() => void submit(probe, "failed")}
                  >
                    {t(BASE + "reviewFail")}
                  </button>
                  <span className="text-[11px] text-muted-foreground">{t(BASE + "reviewKeep")}</span>
                </div>

                {message === "submitted" ? (
                  <p data-testid={`probe-review-${probe.probe_id}`} className="text-[11px] text-emerald-700 dark:text-emerald-300">
                    {t(BASE + "reviewSubmitted")}
                  </p>
                ) : null}
                {message === "conflict" ? (
                  <p data-testid={`probe-conflict-${probe.probe_id}`} className="text-[11px] text-amber-700 dark:text-amber-300">
                    {t(BASE + "reviewConflict", { revision: probe.grading_revision })}
                    {onRefresh ? (
                      <button
                        type="button"
                        className="ml-2 rounded border border-border px-2 py-0.5 text-[11px] text-foreground"
                        onClick={() => {
                          setMessages((current) => {
                            const next = { ...current };
                            delete next[key];
                            return next;
                          });
                          onRefresh();
                        }}
                      >
                        {t("settings.evaluation.memory.common.refresh")}
                      </button>
                    ) : null}
                  </p>
                ) : null}
                {message && message !== "submitted" && message !== "conflict" ? (
                  <p className="text-[11px] text-destructive">{t(BASE + "reviewFailed", { message })}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
