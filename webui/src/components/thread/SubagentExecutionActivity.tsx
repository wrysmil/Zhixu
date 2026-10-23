import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import {
  Ban,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  CircleOff,
  Loader2,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { GenericToolRun } from "@/components/thread/activity/GenericToolRun";
import type { GenericToolRunItem } from "@/components/thread/activity/generic-tool-model";
import { AgentGlyph } from "@/components/ui/agent-glyph";
import type {
  ExecutionActivity,
  ExecutionActivityStatus,
  ExecutionSnapshot,
  ExecutionStatus,
  SubagentExecutionState,
} from "@/features/subagent-execution/types";
import { cn } from "@/lib/utils";

export interface SubagentExecutionEntryValue {
  capability: boolean;
  byInvocationId: Readonly<Record<string, SubagentExecutionState>>;
  invocationIdByToolCallId: Readonly<Record<string, string>>;
  /** 档案 id → 该 Agent 配置的图标；缺失时回退到内置默认图标。 */
  agentIconById?: Readonly<Record<string, string>>;
  onOpen: (invocationId: string, trigger: HTMLElement | null) => void;
}

/** 内置默认 Agent 图标，与 Agent 编辑器里的兜底保持一致。 */
export { DEFAULT_AGENT_ICON } from "@/lib/agents/icons";

const SubagentExecutionEntryContext = createContext<SubagentExecutionEntryValue | null>(null);

export function SubagentExecutionEntryProvider({
  value,
  children,
}: {
  value: SubagentExecutionEntryValue;
  children: ReactNode;
}) {
  return (
    <SubagentExecutionEntryContext.Provider value={value}>
      {children}
    </SubagentExecutionEntryContext.Provider>
  );
}

function useSubagentExecutionEntryValue(): SubagentExecutionEntryValue | null {
  return useContext(SubagentExecutionEntryContext);
}

function formatUnit(
  value: number,
  unit: "hour" | "minute" | "second" | "millisecond",
  locale: string,
): string {
  try {
    return new Intl.NumberFormat(locale, {
      style: "unit",
      unit,
      unitDisplay: "short",
    }).format(value);
  } catch {
    const suffix =
      unit === "hour" ? "h" : unit === "minute" ? "m" : unit === "millisecond" ? "ms" : "s";
    return `${value}${suffix}`;
  }
}

function formatDuration(
  ms: number | null | undefined,
  locale: string,
): string | null {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return null;
  // 本地工具（read_file 等）单次常只有几毫秒，取整到整秒会全塌成 "0 sec"，
  // 亚秒时长改用毫秒显示，避免看起来像坏了。
  if (ms < 1000) return formatUnit(Math.max(1, Math.round(ms)), "millisecond", locale);
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return formatUnit(seconds, "second", locale);
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 60) {
    return `${formatUnit(minutes, "minute", locale)} ${formatUnit(remainingSeconds, "second", locale)}`;
  }
  const hours = Math.floor(minutes / 60);
  return `${formatUnit(hours, "hour", locale)} ${formatUnit(minutes % 60, "minute", locale)}`;
}

function executionElapsedMs(snapshot: ExecutionSnapshot, nowMs: number): number | null {
  if (snapshot.status === "queued") return snapshot.queueDurationMs;
  if (snapshot.durationMs !== null) return snapshot.durationMs;
  if (!snapshot.startedAt) return null;
  const startedAt = Date.parse(snapshot.startedAt);
  if (!Number.isFinite(startedAt)) return null;
  return Math.max(0, nowMs - startedAt);
}

/** 活动行状态文字的配色；文字始终存在，颜色只是辅助。 */
function entryStatusTone(status: ExecutionStatus): string {
  if (status === "succeeded") return "text-emerald-700/90 dark:text-emerald-300";
  if (status === "failed") return "text-destructive";
  if (status === "interrupted" || status === "queued") {
    return "text-amber-700/90 dark:text-amber-200";
  }
  if (status === "running") return "text-primary/85";
  return "text-muted-foreground";
}

/** Agent 图标底色跟着运行状态走，图标本身始终是 Agent 语义。 */
function entryIconTone(status: ExecutionStatus): string {
  if (status === "succeeded") {
    return "bg-emerald-500/10 text-emerald-700 ring-emerald-600/25 dark:text-emerald-300";
  }
  if (status === "failed") return "bg-destructive/10 text-destructive ring-destructive/25";
  if (status === "interrupted" || status === "queued") {
    return "bg-amber-500/10 text-amber-700 ring-amber-600/25 dark:text-amber-200";
  }
  if (status === "cancelled") return "bg-muted text-muted-foreground ring-border";
  return "bg-primary/10 text-primary ring-primary/25";
}

/**
 * Agent 图标：优先用档案里配置的图标（lucide、emoji 或图片路径），没有就用内置默认图标。
 * 图标只做身份展示，状态仍由文字与配色表达。
 */


function activityIcon(activity: ExecutionActivity): LucideIcon {
  if (activity.kind === "progress") return CircleDot;
  if (activity.status === "running") return Loader2;
  if (activity.status === "succeeded") return CheckCircle2;
  if (activity.status === "failed") return XCircle;
  if (activity.status === "cancelled") return Ban;
  return CircleOff;
}

export function statusLabel(
  status: ExecutionStatus,
  t: (key: string, options?: { defaultValue?: string }) => string,
): string {
  return t(`thread.subagentExecution.status.${status}`, {
    defaultValue: status,
  });
}

export function activityStatusLabel(
  status: ExecutionActivityStatus,
  t: (key: string, options?: { defaultValue?: string }) => string,
): string {
  return t(`thread.subagentExecution.activityStatus.${status}`, {
    defaultValue: status,
  });
}

export function SubagentExecutionActivity({
  activity,
  nowMs = Date.now(),
}: {
  activity: ExecutionActivity;
  nowMs?: number;
}) {
  const { t, i18n } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const Icon = activityIcon(activity);
  const totalMs = activity.finishedAt
    ? Date.parse(activity.finishedAt) - Date.parse(activity.startedAt)
    : nowMs - Date.parse(activity.startedAt);
  const duration = formatDuration(
    Number.isFinite(totalMs) ? totalMs : null,
    i18n.resolvedLanguage ?? i18n.language,
  );
  const active = activity.status === "running";
  // 摘要只在服务端做过白名单投影，前端原样展示即可；两者都没有时不给出可点控件。
  const hasSummary = Boolean(activity.detail || activity.output);
  const truncated =
    activity.detail?.endsWith("…") === true || activity.output?.endsWith("…") === true;

  return (
    <li
      className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-2 border-b border-border/35 py-2 last:border-b-0"
      data-testid={`subagent-execution-activity-${activity.activityId}`}
      data-status={activity.status}
    >
      <span
        className={cn(
          "mt-0.5 grid h-4 w-4 place-items-center rounded-full",
          activity.status === "succeeded" && "text-emerald-600/80",
          activity.status === "failed" && "text-destructive",
          activity.status === "cancelled" && "text-muted-foreground",
          activity.status === "interrupted" && "text-amber-600/85",
          active && "text-muted-foreground/75",
        )}
        aria-hidden
      >
        <Icon className={cn("h-3.5 w-3.5", active && "animate-spin")} />
      </span>
      <span className="min-w-0">
        <span className="block truncate font-medium text-foreground/85">
          {activity.toolName || activity.label}
        </span>
        <span className="block text-[11px] text-muted-foreground/70">
          {activityStatusLabel(activity.status, t)}
        </span>
      </span>
      {duration || hasSummary ? (
        <span className="flex items-center gap-1">
          {duration ? (
            <span className="text-[11px] tabular-nums text-muted-foreground/65" aria-hidden>
              {duration}
            </span>
          ) : null}
          {hasSummary ? (
            <button
              type="button"
              aria-expanded={expanded}
              aria-label={t("thread.subagentExecution.detailToggle", {
                defaultValue: "Show parameters and output",
              })}
              onClick={() => setExpanded((current) => !current)}
              className="grid h-5 w-5 place-items-center rounded-control text-muted-foreground/55 transition-colors hover:bg-accent/40 hover:text-foreground/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/55"
            >
              <ChevronRight
                className={cn("h-3.5 w-3.5 transition-transform", expanded && "rotate-90")}
                aria-hidden
              />
            </button>
          ) : null}
        </span>
      ) : null}
      {expanded && hasSummary ? (
        <div className="col-span-3 mt-1 space-y-1.5 rounded-control bg-muted/40 px-2 py-1.5">
          <ActivitySummary
            label={t("thread.subagentExecution.parameters", { defaultValue: "Parameters" })}
            value={activity.detail}
          />
          <ActivitySummary
            label={t("thread.subagentExecution.toolOutput", { defaultValue: "Output" })}
            value={activity.output}
          />
          {truncated ? (
            <p className="text-[10px] text-muted-foreground/60">
              {t("thread.subagentExecution.truncated", { defaultValue: "Content truncated" })}
            </p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function ActivitySummary({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="min-w-0">
      <span className="block text-[10px] text-muted-foreground/60">{label}</span>
      <p className="break-words font-mono text-[11px] leading-4 text-foreground/75">{value}</p>
    </div>
  );
}

export function SubagentExecutionEntry({ item }: { item: GenericToolRunItem }) {
  const { t, i18n } = useTranslation();
  const context = useSubagentExecutionEntryValue();
  const invocationId = item.toolCallId
    ? context?.invocationIdByToolCallId[item.toolCallId] ?? null
    : null;
  const execution = invocationId ? context?.byInvocationId[invocationId] ?? null : null;
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    if (execution?.snapshot.status !== "running") return undefined;
    setNowMs(Date.now());
    const interval = window.setInterval(() => setNowMs(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, [execution?.snapshot.status]);

  // Old gateways keep the original generic row with no extra affordance.
  if (!context || !context.capability) {
    return <GenericToolRun items={[item]} />;
  }

  // Legacy chats have no trustworthy association. Keep the original generic row
  // visible and explicitly say that execution details are unavailable.
  if (!execution || !invocationId) {
    return (
      <div data-testid="subagent-execution-unavailable">
        <GenericToolRun items={[item]} />
        <p className="pl-[1.625rem] text-[11px] text-muted-foreground/65">
          {t("thread.subagentExecution.unavailable", { defaultValue: "No execution details" })}
        </p>
      </div>
    );
  }

  const snapshot = execution.snapshot;
  const elapsed = formatDuration(
    executionElapsedMs(snapshot, nowMs),
    i18n.resolvedLanguage ?? i18n.language,
  );
  const status = statusLabel(snapshot.status, t);
  // 运行中／运行出错／运行完成三种状态都要在主聊天里可读，不再用动词短语替代。
  const action = status;
  // 有档案就用档案名（如「通用助理」「资料研究」）；没有档案就是通用 Agent。
  const agentLabel = snapshot.agentId
    ? snapshot.agentName
    : t("thread.subagentExecution.genericAgent", { defaultValue: "General Agent" });
  const agentIcon = snapshot.agentId
    ? context?.agentIconById?.[snapshot.agentId] ?? ""
    : "";

  return (
    <button
      type="button"
      data-testid="subagent-execution-entry"
      data-invocation-id={snapshot.invocationId}
      data-status={snapshot.status}
      aria-label={t("thread.subagentExecution.entryAria", {
        agent: snapshot.agentName,
        title: snapshot.title,
        status,
        defaultValue: "{{agent}} · {{title}} · {{status}}",
      })}
      className="group flex w-full max-w-[45rem] items-center gap-2 rounded-control px-1 py-1 text-left text-[13px] leading-5 transition-colors hover:bg-accent/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/55"
      onClick={(event) => context.onOpen(snapshot.invocationId, event.currentTarget)}
    >
      <span className="sr-only" role="status" aria-live="polite">{status}</span>
      <span
        className={cn(
          "grid h-5 w-5 shrink-0 place-items-center rounded-md ring-1",
          entryIconTone(snapshot.status),
        )}
        aria-hidden
      >
        <AgentGlyph icon={agentIcon} size={14} />
      </span>
      <span className="min-w-0 flex-1 truncate">
        <span className="font-medium text-foreground/90">{agentLabel}</span>
        <span className="text-muted-foreground/60"> · </span>
        <span className="text-foreground/80">{snapshot.title}</span>
        <span
          className={cn(
            "ml-1",
            entryStatusTone(snapshot.status),
            snapshot.status === "running" && "animate-pulse motion-reduce:animate-none",
          )}
        >
          {action}
        </span>
        {elapsed ? (
          <span className="ml-1 tabular-nums text-muted-foreground/55" aria-hidden>· {elapsed}</span>
        ) : null}
      </span>
      <ChevronRight
        className="h-3.5 w-3.5 shrink-0 text-muted-foreground/35 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 motion-reduce:transition-none"
        aria-hidden
      />
    </button>
  );
}

