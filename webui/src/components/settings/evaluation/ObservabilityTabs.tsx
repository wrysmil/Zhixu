import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import { CoverageBadges } from "@/components/settings/evaluation/CoverageBadges";
import { ExecutionCanvas } from "@/components/settings/evaluation/ExecutionCanvas";
import { ExecutionInspector } from "@/components/settings/evaluation/ExecutionInspector";
import {
  ToolCallsTable,
  type ToolCallFilter,
} from "@/components/settings/evaluation/ToolCallsTable";
import { UsageLedgerTable } from "@/components/settings/evaluation/UsageLedgerTable";
import {
  EvaluationStatusBadge,
  evaluationStatusKey,
} from "@/components/settings/evaluation/evaluation-status";
import { evaluationErrorText } from "@/components/settings/evaluation/evaluation-utils";
import { Button } from "@/components/ui/button";
import {
  evaluationApi,
  type Attempt,
  type ExecutionPage,
  type ToolCallStatus,
  type TraceEvent,
  type UsageGroupBy,
  type UsagePage,
} from "@/lib/evaluation-api";
import { projectExecutionGraph, type ExecutionProjection } from "@/lib/execution-graph";
import { cn } from "@/lib/utils";

type ObservabilityTabId = "canvas" | "tools" | "cases";

const TABS: Array<{ id: ObservabilityTabId; labelKey: string }> = [
  { id: "canvas", labelKey: "settings.evaluation.observability.tabCanvas" },
  { id: "tools", labelKey: "settings.evaluation.observability.tabToolCalls" },
  { id: "cases", labelKey: "settings.evaluation.observability.tabCases" },
];

const USAGE_GROUP_OPTIONS: Array<{ value: UsageGroupBy; labelKey: string }> = [
  { value: "role", labelKey: "settings.evaluation.observability.groupByRole" },
  { value: "agent", labelKey: "settings.evaluation.observability.groupByAgent" },
  { value: "model", labelKey: "settings.evaluation.observability.groupByModel" },
];

/** 观测接口是新增端点：老网关或裁剪过的客户端可能没有实现，此时静默跳过而不是抛错。 */
function isAvailable<T>(value: T | undefined): value is T {
  return typeof value === "function";
}

function CanvasPanel({
  attempts,
  activeAttemptId,
  onSelectAttempt,
  executionPage,
  executionLoading,
  executionErrorText,
  projection,
  selectedNodeId,
  onSelectNode,
  usageGroupBy,
  onUsageGroupByChange,
  usagePage,
  onFocusToolCall,
}: {
  attempts: Attempt[];
  activeAttemptId: string | null;
  onSelectAttempt: (attemptId: string) => void;
  executionPage: ExecutionPage | null;
  executionLoading: boolean;
  executionErrorText: string | null;
  projection: ExecutionProjection | null;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
  usageGroupBy: UsageGroupBy;
  onUsageGroupByChange: (value: UsageGroupBy) => void;
  usagePage: UsagePage | null;
  onFocusToolCall: (callId: string) => void;
}) {
  const { t } = useTranslation();
  const selectedNode = projection?.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const usageCoverage =
    usagePage && typeof usagePage.coverage === "string" ? usagePage.coverage : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex min-w-[12rem] flex-col gap-1">
          <label
            className="text-[11px] font-medium text-muted-foreground"
            htmlFor="evaluation-selected-case"
          >
            {t("settings.evaluation.observability.selectedCase")}
          </label>
          <select
            id="evaluation-selected-case"
            value={activeAttemptId ?? ""}
            aria-label={t("settings.evaluation.observability.selectedCase")}
            disabled={attempts.length === 0}
            onChange={(event) => onSelectAttempt(event.target.value)}
            className="h-9 rounded-control border border-input bg-background px-2 text-[13px]"
          >
            {attempts.map((attempt) => (
              <option key={attempt.id} value={attempt.id}>
                {attempt.case_name}
              </option>
            ))}
          </select>
        </div>
        <CoverageBadges coverage={executionPage?.coverage ?? null} />
      </div>

      {attempts.length === 0 ? (
        <p className="rounded-floating border border-dashed border-border/70 p-6 text-center text-[13px] text-muted-foreground">
          {t("settings.evaluation.observability.noCases")}
        </p>
      ) : null}

      {executionErrorText ? (
        <p role="alert" className="text-[12px] text-destructive">
          {executionErrorText}
        </p>
      ) : null}

      {executionLoading && !executionPage ? (
        <p className="text-[12px] text-muted-foreground">
          {t("settings.evaluation.observability.loadingGraph")}
        </p>
      ) : null}

      {projection ? (
        <>
          {projection.grouped ? (
            <p className="text-[11px] text-muted-foreground">
              {t("settings.evaluation.observability.groupedTools")}
            </p>
          ) : null}
          <ExecutionCanvas
            nodes={projection.nodes}
            edges={projection.edges}
            selectedId={selectedNodeId}
            onSelect={(node) => onSelectNode(node.id)}
            omittedNodeCount={projection.omittedNodeCount}
          />
        </>
      ) : null}

      {selectedNode && selectedNode.kind === "tool_group" ? (
        <div className="space-y-2 rounded-floating border border-border/70 bg-settings-surface p-3">
          <p className="text-[12px] font-medium text-foreground">
            {t("settings.evaluation.observability.groupCalls", {
              count: selectedNode.count,
              defaultValue: `${selectedNode.count} calls in this group`,
            })}
          </p>
          <div className="flex flex-wrap gap-1">
            {selectedNode.call_ids.map((callId) => (
              <button
                key={callId}
                type="button"
                onClick={() => onFocusToolCall(callId)}
                aria-label={`${t("settings.evaluation.observability.openCallInTools")}: ${callId}`}
                className="rounded-control border border-border/70 px-2 py-0.5 font-mono text-[11px] text-foreground hover:bg-muted/40"
              >
                {callId}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <ExecutionInspector node={selectedNode} />

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-[13px] font-semibold text-foreground">
            {t("settings.evaluation.observability.usage")}
          </h3>
          <div className="flex items-center gap-1">
            <label
              className="text-[11px] font-medium text-muted-foreground"
              htmlFor="evaluation-usage-group"
            >
              {t("settings.evaluation.observability.groupUsageBy")}
            </label>
            <select
              id="evaluation-usage-group"
              value={usageGroupBy}
              aria-label={t("settings.evaluation.observability.groupUsageBy")}
              onChange={(event) => onUsageGroupByChange(event.target.value as UsageGroupBy)}
              className="h-8 rounded-control border border-input bg-background px-2 text-[12px]"
            >
              {USAGE_GROUP_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {t(option.labelKey)}
                </option>
              ))}
            </select>
          </div>
        </div>
        {usageCoverage ? (
          <p className="text-[11px] text-muted-foreground">
            {`${t("settings.evaluation.observability.ledgerCoverage")}: ${usageCoverage}`}
          </p>
        ) : null}
        <UsageLedgerTable page={usagePage} />
      </div>
    </div>
  );
}

function CasesPanel({
  attempts,
  activeAttemptId,
  onSelectAttempt,
  traces,
  traceMore,
  onLoadTrace,
}: {
  attempts: Attempt[];
  activeAttemptId: string | null;
  onSelectAttempt: (attemptId: string) => void;
  traces: Record<string, TraceEvent[]>;
  traceMore: Record<string, number>;
  onLoadTrace: (attemptId: string, afterSeq?: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <ul aria-label={t("settings.evaluation.observability.caseListLabel")} className="space-y-2">
      {attempts.length === 0 ? (
        <li className="rounded-floating border border-dashed border-border/70 p-6 text-center text-[13px] text-muted-foreground">
          {t("settings.evaluation.observability.noCases")}
        </li>
      ) : null}
      {attempts.map((attempt) => {
        const executionLabel = t(
          `settings.evaluation.status.${evaluationStatusKey(attempt.execution_status)}`,
          { defaultValue: attempt.execution_status },
        );
        const verdictLabel = t(
          `settings.evaluation.status.${evaluationStatusKey(attempt.verdict)}`,
          { defaultValue: attempt.verdict },
        );
        const trace = traces[attempt.id];
        return (
          <li
            key={attempt.id}
            className="space-y-2 rounded-floating border border-border/60 bg-settings-surface p-3"
          >
            <button
              type="button"
              aria-label={`${attempt.case_name} · ${verdictLabel}`}
              aria-pressed={attempt.id === activeAttemptId}
              onClick={() => onSelectAttempt(attempt.id)}
              className={cn(
                "flex w-full flex-wrap items-center gap-2 rounded-control px-2 py-1 text-left",
                attempt.id === activeAttemptId
                  ? "bg-muted/50 ring-1 ring-primary/60"
                  : "hover:bg-muted/30",
              )}
            >
              <span className="text-[13px] font-medium text-foreground">
                {attempt.case_name}
              </span>
              <EvaluationStatusBadge status={attempt.verdict} kind="verdict" />
              <span className="text-[11px] text-muted-foreground">{executionLabel}</span>
              {attempt.errors.length > 0 ? (
                <span className="text-[11px] text-destructive">
                  {t("settings.evaluation.detail.errors")}: {attempt.errors.length}
                </span>
              ) : null}
            </button>
            {attempt.final_answer ? (
              <p className="line-clamp-2 text-[12px] text-muted-foreground">
                {attempt.final_answer}
              </p>
            ) : null}
            {trace ? (
              trace.length > 0 ? (
                <ol className="space-y-1 rounded-control bg-muted/40 p-2 font-mono text-[11px] text-muted-foreground">
                  {trace.map((event) => (
                    <li key={event.event_id} className="flex gap-2">
                      <span>{event.seq}</span>
                      <span className="text-foreground">{event.type}</span>
                      {event.payload ? (
                        <span className="truncate">{JSON.stringify(event.payload)}</span>
                      ) : null}
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="text-[11px] text-muted-foreground">
                  {t("settings.evaluation.observability.noTrace")}
                </p>
              )
            ) : null}
            <div className="flex gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onLoadTrace(attempt.id)}
              >
                {t("settings.evaluation.observability.loadTrace")}
              </Button>
              {traceMore[attempt.id] ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => onLoadTrace(attempt.id, traceMore[attempt.id])}
                >
                  {t("settings.evaluation.observability.loadMore")}
                </Button>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function ObservabilityTabs({
  runId,
  attempts,
}: {
  runId: string;
  attempts: Attempt[];
}) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<ObservabilityTabId>("cases");
  const [selectedAttemptId, setSelectedAttemptId] = useState<string | null>(null);
  const activeAttemptId = selectedAttemptId ?? attempts[0]?.id ?? null;

  const [executionPage, setExecutionPage] = useState<ExecutionPage | null>(null);
  const [executionLoading, setExecutionLoading] = useState(false);
  const [executionError, setExecutionError] = useState<unknown>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const [toolQuery, setToolQuery] = useState("");
  const [toolStatus, setToolStatus] = useState<ToolCallStatus | "all">("all");
  const [toolApplied, setToolApplied] = useState<ToolCallFilter>({ q: "", status: null });

  const [usageGroupBy, setUsageGroupBy] = useState<UsageGroupBy>("role");
  const [usagePage, setUsagePage] = useState<UsagePage | null>(null);

  const [traces, setTraces] = useState<Record<string, TraceEvent[]>>({});
  const [traceMore, setTraceMore] = useState<Record<string, number>>({});
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // 切换用例时清空上一张图，避免旧数据与新用例混淆。
  useEffect(() => {
    setExecutionPage(null);
    setSelectedNodeId(null);
  }, [activeAttemptId]);

  useEffect(() => {
    if (activeTab !== "canvas" || !activeAttemptId) return;
    if (!isAvailable(evaluationApi.getExecution)) return;
    let cancelled = false;
    setExecutionLoading(true);
    void evaluationApi
      .getExecution(activeAttemptId, 0, 200)
      .then((page) => {
        if (cancelled) return;
        setExecutionPage(page);
        setExecutionError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setExecutionPage(null);
        setExecutionError(cause);
      })
      .finally(() => {
        if (!cancelled) setExecutionLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeTab, activeAttemptId]);

  useEffect(() => {
    if (activeTab !== "canvas") return;
    if (!isAvailable(evaluationApi.getRunUsage)) return;
    let cancelled = false;
    void evaluationApi
      .getRunUsage(runId, { group_by: usageGroupBy, limit: 50 })
      .then((page) => {
        if (cancelled) return;
        setUsagePage(page);
      })
      .catch(() => {
        if (cancelled) return;
        setUsagePage(null);
      });
    return () => {
      cancelled = true;
    };
  }, [activeTab, runId, usageGroupBy]);

  const projection = useMemo(
    () =>
      executionPage
        ? projectExecutionGraph(executionPage.data.nodes, executionPage.data.edges)
        : null,
    [executionPage],
  );

  const loadTrace = async (attemptId: string, afterSeq = 0) => {
    try {
      const page = await evaluationApi.getTrace(attemptId, afterSeq, 200);
      setTraces((prev) => ({
        ...prev,
        [attemptId]:
          afterSeq === 0 ? page.data : [...(prev[attemptId] ?? []), ...page.data],
      }));
      setTraceMore((prev) => ({
        ...prev,
        [attemptId]: page.has_more ? page.next_after_seq : 0,
      }));
    } catch {
      setTraces((prev) => ({ ...prev, [attemptId]: [] }));
    }
  };

  const applyToolFilters = () => {
    setToolApplied({
      q: toolQuery.trim(),
      status: toolStatus === "all" ? null : toolStatus,
    });
  };

  const resetToolFilters = () => {
    setToolQuery("");
    setToolStatus("all");
    setToolApplied({ q: "", status: null });
  };

  /** 从分组节点跳到工具调用视图并按真实 call_id 过滤。 */
  const focusToolCall = (callId: string) => {
    setToolQuery(callId);
    setToolStatus("all");
    setToolApplied({ q: callId, status: null });
    setActiveTab("tools");
  };

  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    setActiveTab(TABS[next].id);
    tabRefs.current[next]?.focus();
  };

  const executionErrorMessage = executionError ? evaluationErrorText(executionError, t) : null;

  return (
    <div className="space-y-3">
      <div
        role="tablist"
        aria-label={t("settings.evaluation.observability.tabsLabel")}
        className="flex flex-wrap gap-1 rounded-control border border-border/60 bg-muted/20 p-1"
      >
        {TABS.map((tab, index) => {
          const selected = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              ref={(element) => {
                tabRefs.current[index] = element;
              }}
              type="button"
              role="tab"
              id={`evaluation-observability-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={`evaluation-observability-panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => setActiveTab(tab.id)}
              onKeyDown={(event) => onTabKeyDown(event, index)}
              className={cn(
                "rounded-control px-3 py-1.5 text-[12px] font-medium transition-colors",
                selected
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(tab.labelKey)}
            </button>
          );
        })}
      </div>

      {TABS.map((tab) => (
        <div
          key={tab.id}
          role="tabpanel"
          id={`evaluation-observability-panel-${tab.id}`}
          aria-labelledby={`evaluation-observability-tab-${tab.id}`}
          hidden={activeTab !== tab.id}
        >
          {activeTab !== tab.id ? null : tab.id === "canvas" ? (
            <CanvasPanel
              attempts={attempts}
              activeAttemptId={activeAttemptId}
              onSelectAttempt={setSelectedAttemptId}
              executionPage={executionPage}
              executionLoading={executionLoading}
              executionErrorText={executionErrorMessage}
              projection={projection}
              selectedNodeId={selectedNodeId}
              onSelectNode={setSelectedNodeId}
              usageGroupBy={usageGroupBy}
              onUsageGroupByChange={setUsageGroupBy}
              usagePage={usagePage}
              onFocusToolCall={focusToolCall}
            />
          ) : tab.id === "tools" ? (
            <ToolCallsTable
              attemptId={activeAttemptId}
              query={toolQuery}
              status={toolStatus}
              applied={toolApplied}
              onQueryChange={setToolQuery}
              onStatusChange={setToolStatus}
              onApply={applyToolFilters}
              onReset={resetToolFilters}
            />
          ) : (
            <CasesPanel
              attempts={attempts}
              activeAttemptId={activeAttemptId}
              onSelectAttempt={setSelectedAttemptId}
              traces={traces}
              traceMore={traceMore}
              onLoadTrace={(attemptId, afterSeq) => void loadTrace(attemptId, afterSeq)}
            />
          )}
        </div>
      ))}
    </div>
  );
}
