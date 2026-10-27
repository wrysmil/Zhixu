import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Eye, Loader2, Pencil, Search, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { formControlFocusClassName } from "@/components/ui/form-control";
import { Input } from "@/components/ui/input";
import type { MemoryListResult, MemoryOrigin, MemoryPresentation, MemoryWorkspace } from "@/lib/types";
import { cn } from "@/lib/utils";

export const memorySelectClass = cn("h-8 max-w-full rounded-control border border-input bg-background px-2 text-[12px] text-foreground", formControlFocusClassName);
export const memoryRowClass = "group/memory rounded-lg px-4 py-4 transition-colors hover:bg-muted/45 focus-within:bg-muted/45 sm:px-5";

export function useMemoryCopy() {
  const { i18n } = useTranslation();
  const zh = (i18n.resolvedLanguage ?? i18n.language).startsWith("zh");
  return { zh, locale: zh ? "zh-CN" : i18n.resolvedLanguage ?? "en", text: (chinese: string, english: string) => zh ? chinese : english };
}

/** Unzoned historical values retain their recorded wall time; never assume UTC. */
export function formatMemoryTime(value: string | null | undefined, locale: string, detailed = false): string | null {
  if (!value) return null;
  const unzoned = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2})(?:\.\d+)?)?$/.exec(value);
  if (unzoned) {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return null;
    return `${unzoned[1]} ${unzoned[2]}${detailed ? `:${unzoned[3] ?? "00"} (${locale.startsWith("zh") ? "时区未记录" : "timezone not recorded"})` : ""}`;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", ...(detailed ? { second: "2-digit", timeZoneName: "short" } as const : {}), hourCycle: "h23" }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((entry) => entry.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}${detailed ? `:${part("second")} ${part("timeZoneName")}` : ""}`;
}

export function MemoryTime({ value, label, detailed = false }: { value: string | null | undefined; label?: string; detailed?: boolean }) {
  const { text, locale } = useMemoryCopy();
  const formatted = formatMemoryTime(value, locale, detailed);
  return <span className="inline-flex flex-wrap gap-x-1 tabular-nums">{label && <span>{label}</span>}{formatted ? <time dateTime={value ?? undefined} title={formatMemoryTime(value, locale, true) ?? value ?? undefined}>{formatted}</time> : <span>{text("时间未记录", "Time not recorded")}</span>}</span>;
}

export function memoryOrigin(record: MemoryPresentation & { source: string }): MemoryOrigin {
  if (record.origin_kind) return record.origin_kind;
  if (record.trigger_mode === "manual" || record.source === "manual") return "manual";
  return ["extraction", "consolidation", "idle", "context_compress", "session_end", "topic_change", "daily_consolidation"].includes(record.source) ? "ai" : "unknown";
}

export function SourceBadge({ record }: { record: MemoryPresentation & { source: string } }) {
  const { text } = useMemoryCopy();
  const origin = memoryOrigin(record);
  return <span className={cn("inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] leading-4", origin === "ai" ? "bg-primary/[0.07] text-primary" : "bg-muted/80 text-muted-foreground")}>{origin === "manual" ? text("手动触发", "Manual") : origin === "ai" ? text("AI 生成", "AI generated") : text("来源未记录", "Source not recorded")}{origin === "manual" && record.generation_method === "ai" ? text(" · AI 提取", " · AI extracted") : ""}</span>;
}

export function WorkspaceLabels({ workspaces }: { workspaces?: MemoryWorkspace[] }) {
  const { text } = useMemoryCopy();
  if (!workspaces?.length) return <span className="text-muted-foreground">{text("全局记忆", "Global memory")}</span>;
  return <>{workspaces.map((workspace) => <span key={workspace.id} title={workspace.path} className="max-w-56 truncate text-muted-foreground">{workspace.name}</span>)}</>;
}

export function ImportanceBadge({ value }: { value: number }) {
  const { text } = useMemoryCopy();
  const score = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null;
  const high = score !== null && score >= 0.8;
  const medium = score !== null && score >= 0.5;
  const level = high ? text("高", "High") : medium ? text("中", "Medium") : text("低", "Low");
  return <span title={text("高：80% 及以上；中：50%–79%；低：50% 以下", "High: 80% or more; medium: 50–79%; low: below 50%")} className={cn("inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium leading-4", high ? "bg-amber-100/80 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300" : medium ? "bg-blue-100/80 text-blue-800 dark:bg-blue-500/15 dark:text-blue-300" : "bg-muted text-muted-foreground")}>{score === null ? text("重要性未记录", "Importance not recorded") : text(`重要性 ${level} · ${Math.round(score * 100)}%`, `Importance ${level} · ${Math.round(score * 100)}%`)}</span>;
}

export function MemoryActions({ onDetails, onEdit, onDelete, expanded, busy }: { onDetails: () => void; onEdit?: () => void; onDelete: () => void; expanded?: boolean; busy?: boolean }) {
  const { text } = useMemoryCopy();
  const actionClass = "inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-background hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";
  const details = expanded ? text("收起详情", "Collapse details") : text("查看详情", "View details");
  return <div className="flex shrink-0 gap-0.5 opacity-100 transition-opacity [@media(hover:hover)_and_(pointer:fine)]:opacity-0 group-hover/memory:opacity-100 group-focus-within/memory:opacity-100" data-memory-actions>
    <button type="button" className={actionClass} aria-label={details} title={details} aria-expanded={expanded} onClick={onDetails}><Eye className="h-4 w-4" aria-hidden /></button>
    {onEdit && <button type="button" className={actionClass} aria-label={text("编辑", "Edit")} title={text("编辑", "Edit")} onClick={onEdit}><Pencil className="h-4 w-4" aria-hidden /></button>}
    <button type="button" className={cn(actionClass, "hover:text-destructive")} aria-label={text("删除", "Delete")} title={text("删除", "Delete")} onClick={onDelete} disabled={busy}><Trash2 className="h-4 w-4" aria-hidden /></button>
  </div>;
}

export function MemoryFilters({ query, onQuery, origin, onOrigin, workspace, onWorkspace, result, children, onClear, active, actions, loading = false }: {
  query: string; onQuery: (value: string) => void; origin: MemoryOrigin | ""; onOrigin: (value: MemoryOrigin | "") => void;
  workspace: string; onWorkspace: (value: string) => void; result: MemoryListResult<unknown>; children?: ReactNode;
  onClear: () => void; active: boolean; actions?: ReactNode; loading?: boolean;
}) {
  const { text } = useMemoryCopy();
  const counts = result.source_counts;
  const choices = [{ value: "", label: text("全部", "All"), count: counts?.all }, { value: "ai", label: text("AI 生成", "AI generated"), count: counts?.ai }, { value: "manual", label: text("手动触发", "Manual"), count: counts?.manual }, ...((counts?.unknown ?? 0) > 0 || origin === "unknown" ? [{ value: "unknown", label: text("来源未记录", "Unknown source"), count: counts?.unknown }] : [])];
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap gap-1" role="group" aria-label={text("生成来源", "Memory source")}>{choices.map(({ value, label, count }) => <button key={value} type="button" onClick={() => onOrigin(value as MemoryOrigin | "")} aria-pressed={origin === value} className={cn("inline-flex min-h-9 items-center gap-1.5 rounded-lg px-3 text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", origin === value ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground")}>{label}{count !== undefined && <span className="tabular-nums text-muted-foreground">{count}</span>}</button>)}</div>
      {actions}
    </div>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[12px] text-muted-foreground" role="toolbar" aria-label={text("记忆筛选", "Memory filters")}>
      <div className="relative w-full min-w-0 lg:w-40 lg:min-w-40 lg:flex-1">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input type="search" value={query} onChange={(event) => onQuery(event.target.value)} placeholder={text("搜索内容、目标或标签…", "Search content, goals or tags…")} aria-label={text("搜索记忆", "Search memories")} className="h-9 pl-9 text-[12px]" />
      </div>
      <label className="flex h-8 min-w-0 items-center gap-2 rounded-control border border-input bg-background px-2 text-[12px] text-muted-foreground">
        <span className="shrink-0">{text("工作区", "Workspace")}</span>
        <select aria-label={text("工作区", "Workspace")} className="min-w-0 max-w-36 bg-transparent text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" value={workspace} onChange={(event) => onWorkspace(event.target.value)}>
          <option value="">{text("全部工作区", "All workspaces")}</option>
          {result.workspace_options?.map((option) => <option key={option.id} value={option.id} title={option.path}>{option.name}</option>)}
        </select>
      </label>
      {children}
      <div className="ml-auto inline-flex shrink-0 items-center gap-3">
        <span className="inline-flex items-center gap-1.5 whitespace-nowrap" role="status">
          {loading && <Loader2 className="h-3 w-3 animate-spin" aria-label={text("加载中", "Loading")} />}
          {text(`筛选后 ${result.total ?? result.items.length} 条`, `${result.total ?? result.items.length} results`)}
        </span>
        {active && <button type="button" onClick={onClear} className="whitespace-nowrap rounded text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{text("清除筛选", "Clear filters")}</button>}
      </div>
    </div>
  </div>;
}

export function useMemoryList<T>(load: () => Promise<MemoryListResult<T>>, delay: number) {
  const [result, setResult] = useState<MemoryListResult<T>>({ items: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let current = true;
    setLoading(true);
    const timer = setTimeout(() => {
      void load().then((next) => { if (current) { setResult(next); setError(null); } }).catch((reason: unknown) => { if (current) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (current) setLoading(false); });
    }, delay);
    return () => { current = false; clearTimeout(timer); };
  }, [load, delay, revision]);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  return { result, loading, error, refresh };
}

export function useMemoryDetails<T extends { id: string }>(fetch: (id: string) => Promise<T>) {
  const [selected, setSelected] = useState<T | null>(null);
  const [details, setDetails] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const version = useRef(0);
  useEffect(() => () => { version.current += 1; }, []);
  const close = () => { version.current += 1; setSelected(null); setDetails(null); setError(null); };
  const open = async (item: T) => {
    const request = ++version.current;
    setSelected(item); setDetails(null); setError(null);
    try { const value = await fetch(item.id); if (version.current === request) setDetails(value); }
    catch (reason) { if (version.current === request) setError(reason instanceof Error ? reason.message : String(reason)); }
  };
  return { selected, details, error, close, open };
}

export function MemoryLoadState({ error, loading, empty, filtered, onRetry }: { error: string | null; loading: boolean; empty: boolean; filtered: boolean; onRetry: () => void }) {
  const { text } = useMemoryCopy();
  if (error) return <div role="alert" className="rounded-lg bg-destructive/5 px-4 py-6 text-[13px] text-destructive"><p>{error}</p><Button variant="ghost" size="sm" onClick={onRetry}>{text("重试", "Retry")}</Button></div>;
  if (loading && empty) return <div role="status" className="flex justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-label={text("加载中", "Loading")} /></div>;
  if (empty) return <div className="rounded-lg bg-muted/25 px-4 py-12 text-center text-[13px] text-muted-foreground">{filtered ? text("没有符合筛选条件的记忆", "No memories match your filters") : text("还没有记忆", "No memories yet")}</div>;
  return null;
}

export function DetailFields({ fields }: { fields: Array<[string, ReactNode]> }) {
  return <dl className="grid grid-cols-[minmax(5rem,auto)_minmax(0,1fr)] gap-x-4 gap-y-3 text-[12px] leading-5">{fields.map(([label, value]) => <div className="contents" key={label}><dt className="text-muted-foreground">{label}</dt><dd className="min-w-0 whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{value || "—"}</dd></div>)}</dl>;
}

export function DetailProvenance({ record }: { record: MemoryPresentation & { source: string } }) {
  const { text } = useMemoryCopy();
  const reason = record.trigger_reason || record.source;
  const reasons: Record<string, string> = { idle: text("空闲提取", "Idle extraction"), context_compress: text("上下文压缩", "Context compression"), session_end: text("会话结束", "Session ended"), topic_change: text("话题切换", "Topic changed"), daily_consolidation: text("定期整理", "Scheduled consolidation"), manual: record.generation_method === "ai" ? text("手动提取", "Manually triggered extraction") : text("手动新增", "Manually created"), extraction: text("AI 提取", "AI extraction"), consolidation: text("记忆整理", "Consolidation") };
  return <div className="flex flex-wrap items-center gap-x-2 gap-y-1"><SourceBadge record={record} /><span>{reasons[reason] ?? reason}</span>{record.trigger_mode === "unknown" || (!record.trigger_mode && record.source === "extraction") ? <span className="text-muted-foreground">{text("触发方式未记录", "Trigger not recorded")}</span> : null}</div>;
}
