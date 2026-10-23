import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw, Search } from "lucide-react";
import type { SessionWorkbenchModel } from "./session-workbench-model";
import { useSessionWorkbench, type WorkbenchEventClient } from "./use-session-workbench";
import { WorkbenchProgress } from "./workbench-progress";
import { WorkbenchCapabilities, WorkbenchLimitations, WorkbenchMemories } from "./workbench-context";
import { WorkbenchEmpty, WorkbenchSection, useWorkbenchText } from "./workbench-parts";
import { cn } from "@/lib/utils";

// Default width only; the host owns resizing and the composer inset.
export const SESSION_WORKBENCH_RAIL_WIDTH = "19rem";
interface SessionWorkbenchProps {
  model: SessionWorkbenchModel;
  sessionKey?: string | null;
  token?: string;
  client?: WorkbenchEventClient;
  onOpenFilePreview?: (path: string) => void;
  onOpenExecution?: (invocationId: string) => void;
}
type Tab = "progress" | "memory" | "capabilities";

export function SessionWorkbench({ model, sessionKey, token, client, onOpenFilePreview, onOpenExecution }: SessionWorkbenchProps) {
  const { t, i18n } = useTranslation();
  const text = useWorkbenchText();
  const id = useId();
  const [tab, setTab] = useState<Tab>("progress");
  const { snapshot, loading, error, refresh } = useSessionWorkbench({ sessionKey, token, client });
  const timestamp = snapshot?.updated_at ? Date.parse(snapshot.updated_at) : model.updatedAtMs;
  const updated = timestamp && Number.isFinite(timestamp)
    ? new Date(timestamp).toLocaleTimeString(i18n.language, { hour: "2-digit", minute: "2-digit" }) : "";
  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: "progress", label: text("进展", "Progress") },
    { key: "memory", label: text("记忆", "Memory"), count: snapshot?.memories.length },
    { key: "capabilities", label: text("能力", "Capabilities"), count: snapshot ? snapshot.skills.length + snapshot.mcp.length : undefined },
  ];

  return <aside data-testid="session-workbench" aria-label={t("workbench.title", { defaultValue: "工作台" })}
    className="relative flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden border-l border-border/60 bg-muted/20 text-foreground">
    <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border/50 px-4">
      <span className="text-xs font-semibold">{t("workbench.title", { defaultValue: "工作台" })}</span>
      {updated && <span className="ml-auto text-[10px] text-muted-foreground">{text("更新于", "Updated")} {updated}</span>}
      {sessionKey && token && <button type="button" onClick={refresh} disabled={loading}
        aria-label={text("刷新工作台", "Refresh workbench")} className="ml-auto rounded p-1.5 text-muted-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
        <RefreshCw aria-hidden className="h-3.5 w-3.5" />
      </button>}
    </header>
    <div role="tablist" aria-label={text("工作台视图", "Workbench views")} className="flex shrink-0 border-b border-border/50 px-3">
      {tabs.map((item) => <button key={item.key} id={`${id}-${item.key}`} type="button" role="tab"
        aria-selected={tab === item.key} aria-controls={`${id}-panel`} tabIndex={tab === item.key ? 0 : -1}
        onClick={() => setTab(item.key)}
        onKeyDown={(event) => {
          const offset = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
          if (!offset && event.key !== "Home" && event.key !== "End") return;
          event.preventDefault();
          const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[2] : tabs[(tabs.findIndex((entry) => entry.key === tab) + offset + tabs.length) % tabs.length];
          setTab(next.key);
          document.getElementById(`${id}-${next.key}`)?.focus();
        }}
        className={cn("flex flex-1 items-center justify-center gap-1.5 border-b-2 px-2 py-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring", tab === item.key ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
        {item.label}{!!item.count && <span className="text-[10px] tabular-nums text-muted-foreground">{item.count}</span>}
      </button>)}
    </div>
    <div id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-${tab}`} className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
      {loading && <div role="status" className="space-y-2 border-b border-border/45 px-4 py-3">
        <p className="text-[11px] text-muted-foreground">{text("正在读取会话状态…", "Loading session state…")}</p>
        <div className="h-2 rounded bg-muted" /><div className="h-2 w-2/3 rounded bg-muted" />
      </div>}
      {error && <div role="alert" className="border-b border-border/45 px-4 py-3 text-xs">
        <p className="font-medium text-destructive">{text("工作台数据加载失败", "Could not load workbench data")}</p>
        <p className="mt-1 text-[11px] text-muted-foreground [overflow-wrap:anywhere]">{error}</p>
        <button type="button" onClick={refresh} className="mt-2 rounded border border-border bg-background px-2 py-1 hover:bg-muted">{text("重试", "Retry")}</button>
      </div>}
      {!sessionKey && <div className="px-4 py-3"><WorkbenchEmpty>{text("开始会话后，任务、记忆和能力状态会显示在这里。", "Start a session to see tasks, memories and capabilities.")}</WorkbenchEmpty></div>}
      {tab === "progress" && <>
        {model.summary && <div className="border-b border-border/45 px-4 py-3">
          <p className="mb-1 text-[10px] text-muted-foreground">{text("最近进展", "Latest progress")}</p>
          <p className="line-clamp-3 text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere]" title={model.summary}>{model.summary.slice(0, 140)}{model.summary.length > 140 ? "…" : ""}</p>
        </div>}
        {!loading && !error && <WorkbenchProgress snapshot={snapshot} onOpenExecution={onOpenExecution} />}
      </>}
      {tab === "memory" && !loading && !error && <WorkbenchMemories snapshot={snapshot} onOpenFilePreview={onOpenFilePreview} />}
      {tab === "capabilities" && !loading && !error && <>
        <WorkbenchCapabilities snapshot={snapshot} />
        {!!model.cliUsed.length && <WorkbenchSection title="CLI" count={model.cliUsed.length} defaultOpen><ul className="space-y-2">{model.cliUsed.map((item) => <li key={item.name} className="flex justify-between gap-2 text-xs text-muted-foreground"><span className="min-w-0 truncate">{item.name}</span><span>×{item.count}</span></li>)}</ul></WorkbenchSection>}
      </>}
      {!!snapshot?.limitations.length && <WorkbenchLimitations limitations={snapshot.limitations} />}
      <WorkbenchSection title={t("workbench.environment", { defaultValue: "环境信息" })}>
        <div className="flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
          <span className="rounded border border-border/50 px-1.5 py-0.5 tabular-nums">+{model.diffAdded} −{model.diffDeleted} · {model.diffFiles} {t("workbench.files", { defaultValue: "文件" })}</span>
          <span className="rounded border border-border/50 px-1.5 py-0.5">{t("workbench.local", { defaultValue: "本地" })}</span>
          {model.accessMode && <span className="rounded border border-border/50 px-1.5 py-0.5">{model.accessMode === "full" ? t("workbench.fullAccess") : t("workbench.restrictedAccess")}</span>}
        </div>
        {(model.projectName || model.projectPath) && <p title={model.projectPath} className="mt-2 truncate text-[11px] text-muted-foreground">{model.projectName || model.projectPath}</p>}
      </WorkbenchSection>
      <WorkbenchSection title={t("workbench.outputs", { defaultValue: "产出" })} count={model.outputs.length}>
        {model.outputs.length ? <ul className="space-y-1">{model.outputs.map((output) => <li key={output.path}>
          <button type="button" onClick={() => onOpenFilePreview?.(output.path)} disabled={!onOpenFilePreview} title={output.path}
            className="flex w-full min-w-0 items-center gap-1.5 rounded py-1 text-left text-xs text-muted-foreground enabled:hover:bg-muted enabled:hover:text-foreground">
            <span className="min-w-0 flex-1 truncate">{output.display}</span>
            <span className="shrink-0 text-[10px] tabular-nums"><span className="text-emerald-600 dark:text-emerald-400">+{output.added}</span> <span className="text-destructive">−{output.deleted}</span></span>
          </button>
        </li>)}</ul> : <WorkbenchEmpty>{t("workbench.emptyOutputs")}</WorkbenchEmpty>}
      </WorkbenchSection>
      <WorkbenchSection title={t("workbench.sources", { defaultValue: "来源" })} count={model.sources.length}>
        {model.sources.length ? <ul className="space-y-1">{model.sources.map((source) => <li key={source.href}>
          <a href={source.href} target="_blank" rel="noreferrer noopener" title={source.href} className="flex min-w-0 items-center gap-1.5 rounded py-1 text-xs text-muted-foreground hover:text-foreground hover:underline">
            <Search aria-hidden className="h-3 w-3 shrink-0" /><span className="min-w-0 truncate">{source.title}</span>
          </a>
        </li>)}</ul> : <WorkbenchEmpty>{t("workbench.emptySources")}</WorkbenchEmpty>}
      </WorkbenchSection>
    </div>
  </aside>;
}
