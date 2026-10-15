import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronRight,
  PanelRightClose,
  Plus,
  PanelRightOpen,
  Search,
  Wrench,
} from "lucide-react";

import { StartDock } from "@/components/dock/StartDock";
import { HOME_DOCK_SCOPE } from "@/lib/dock-store";
import {
  type SessionWorkbenchModel,
  type WorkbenchSource,
} from "./session-workbench-model";
import { cn } from "@/lib/utils";

interface SessionWorkbenchProps {
  model: SessionWorkbenchModel;
  onOpenFilePreview?: (path: string) => void;
  onCollapse: () => void;
  sessionKey?: string | null;
  getToken?: () => string;
  overlay?: boolean;
  onRailWidthChange?: (width: number) => void;
  openToolsSignal?: number;
}

function formatUpdatedAt(ms: number | null, language: string): string {
  if (ms === null || !Number.isFinite(ms)) return "";
  return new Date(ms).toLocaleTimeString(
    language.startsWith("zh") ? "zh-CN" : language,
    { hour: "2-digit", minute: "2-digit" },
  );
}

function Section({
  title,
  count,
  defaultOpen,
  children,
}: {
  title: string;
  count?: number;
  defaultOpen: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-border/40 last:border-b-0">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[12.5px] font-medium text-foreground/85 hover:bg-muted/40"
      >
        <ChevronRight
          className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground/70 transition-transform", open && "rotate-90")}
          aria-hidden
        />
        <span className="min-w-0 truncate">{title}</span>
        {typeof count === "number" && count > 0 ? (
          <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground/70">
            {count}
          </span>
        ) : null}
      </button>
      {open ? <div className="px-3 pb-2.5 text-[12.5px]">{children}</div> : null}
    </div>
  );
}

function SourceLink({ source }: { source: WorkbenchSource }) {
  return (
    <a
      href={source.href}
      target="_blank"
      rel="noreferrer noopener"
      title={source.href}
      className="flex min-w-0 items-center gap-1.5 rounded px-1 py-0.5 text-muted-foreground hover:bg-muted/45 hover:text-foreground hover:underline hover:underline-offset-2"
    >
      <Search className="h-3 w-3 shrink-0 opacity-60" aria-hidden />
      <span className="min-w-0 truncate">{source.title}</span>
      {source.host ? (
        <span className="ml-auto shrink-0 text-[11px] opacity-60">{source.host}</span>
      ) : null}
    </a>
  );
}

export function SessionWorkbench({
  model,
  onOpenFilePreview,
  onCollapse,
  sessionKey,
  getToken,
  overlay = false,
  onRailWidthChange,
  openToolsSignal = 0,
}: SessionWorkbenchProps) {
  const { t, i18n } = useTranslation();
  const updated = formatUpdatedAt(model.updatedAtMs, i18n.language);
  const hasIntegrations = model.mcpUsed.length > 0 || model.cliUsed.length > 0;
  // StartDock 以 getToken 引用为壳列表拉取依赖；这里保持跨渲染稳定。
  const dockGetToken = useCallback(() => getToken?.() ?? "", [getToken]);
  const [toolsOpen, setToolsOpen] = useState(false);
  // 工作区状态下点 ＋：直接切到工具并展开 文件/浏览器/终端 菜单，与工具态的 ＋ 行为一致。
  const [menuSignal, setMenuSignal] = useState(0);
  useEffect(() => {
    if (openToolsSignal === 0) return;
    setToolsOpen(true);
  }, [openToolsSignal]);
  const railRef = useRef<HTMLElement | null>(null);
  const onRailWidthChangeRef = useRef(onRailWidthChange);
  onRailWidthChangeRef.current = onRailWidthChange;

  useLayoutEffect(() => {
    const rail = railRef.current;
    if (!rail) return;
    const reportWidth = () => {
      onRailWidthChangeRef.current?.(overlay ? 0 : rail.getBoundingClientRect().width);
    };
    reportWidth();
    const observer = new ResizeObserver(reportWidth);
    observer.observe(rail);
    return () => {
      observer.disconnect();
      onRailWidthChangeRef.current?.(0);
    };
  }, [overlay, toolsOpen]);

  return (
    <aside
      ref={railRef}
      data-testid="session-workbench"
      data-mode={toolsOpen ? "tools" : "workspace"}
      data-overlay={overlay ? "true" : "false"}
      aria-label={t("workbench.title", { defaultValue: "Workbench" })}
      className={cn(
        "flex min-h-0 min-w-0 max-w-full shrink-0 flex-col overflow-hidden border-l border-border/60 bg-background",
        toolsOpen ? "w-[36rem]" : "w-[19rem]",
        overlay ? "absolute inset-y-0 right-0 z-30 shadow-lg" : "relative max-w-[55%]",
      )}
    >
      <header className="flex h-9 shrink-0 items-center gap-2 border-b border-border/40 px-3">
        <span className="text-[12.5px] font-semibold text-foreground/85">
          {t("workbench.title", { defaultValue: "Workbench" })}
        </span>
        {updated ? (
          <span className="truncate text-[11px] text-muted-foreground/80">
            {t("workbench.updatedAt", {
              time: updated,
              defaultValue: "Updated {{time}}",
            })}
          </span>
        ) : null}
        <button
          type="button"
          aria-label={t("workbench.toolsMenu", { defaultValue: "Open sidebar tool" })}
          title={t("workbench.toolsMenu", { defaultValue: "Open sidebar tool" })}
          data-testid="workbench-tools-menu"
          onClick={() => {
            setToolsOpen(true);
            setMenuSignal((value) => value + 1);
          }}
          className="ml-auto grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={toolsOpen
            ? t("workbench.showWorkspace", { defaultValue: "Show workspace" })
            : t("workbench.expandTools", { defaultValue: "Expand sidebar tools" })}
          aria-expanded={toolsOpen}
          onClick={() => {
            setToolsOpen((value) => !value);
          }}
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <PanelRightOpen className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          aria-label={t("workbench.collapse", { defaultValue: "Collapse workbench" })}
          onClick={onCollapse}
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground"
        >
          <PanelRightClose className="h-3.5 w-3.5" aria-hidden />
        </button>
      </header>

      <div hidden={toolsOpen} className={cn("min-h-0 min-w-0 flex-1 overflow-y-auto scrollbar-thin", toolsOpen && "hidden")}>
        <Section
          title={t("workbench.overview", { defaultValue: "Overview" })}
          defaultOpen
        >
          {model.summary ? (
            <p className="text-[12px] leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">
              {model.summary}
            </p>
          ) : (
            <p className="text-[12px] text-muted-foreground/60">
              {t("workbench.emptyOverview", { defaultValue: "No summary yet" })}
            </p>
          )}
        </Section>

        <Section
          title={t("workbench.environment", { defaultValue: "Environment" })}
          defaultOpen
        >
          <div className="flex flex-wrap items-center gap-1.5 text-[11.5px]">
            <span
              className={cn(
                "rounded border px-1.5 py-px tabular-nums",
                model.diffDeleted > 0
                  ? "border-border/50 bg-muted/40 text-muted-foreground"
                  : "border-emerald-500/35 bg-emerald-500/8 text-emerald-600 dark:text-emerald-400",
              )}
            >
              +{model.diffAdded} −{model.diffDeleted} · {model.diffFiles}{" "}
              {t("workbench.files", { defaultValue: "files" })}
            </span>
            <span className="rounded border border-border/50 bg-muted/40 px-1.5 py-px text-muted-foreground">
              {t("workbench.local", { defaultValue: "Local" })}
            </span>
            {model.accessMode ? (
              <span className="rounded border border-border/50 bg-muted/40 px-1.5 py-px text-muted-foreground">
                {model.accessMode === "full"
                  ? t("workbench.fullAccess", { defaultValue: "Full access" })
                  : t("workbench.restrictedAccess", { defaultValue: "Restricted" })}
              </span>
            ) : null}
          </div>
          {model.projectName || model.projectPath ? (
            <p className="mt-1.5 truncate text-[11.5px] text-muted-foreground/80" title={model.projectPath}>
              {model.projectName || model.projectPath}
            </p>
          ) : null}
          <button
            type="button"
            disabled
            title={t("workbench.commitPushHint", { defaultValue: "Coming with the workbench API" })}
            className="mt-2 w-full rounded border border-dashed border-border/60 px-2 py-1 text-[11.5px] text-muted-foreground/55"
          >
            {t("workbench.commitPush", { defaultValue: "Commit or push" })}
          </button>
        </Section>

        <Section
          title={t("workbench.integrations", { defaultValue: "Skills & MCP" })}
          count={model.mcpUsed.length + model.cliUsed.length}
          defaultOpen={false}
        >
          {hasIntegrations ? (
            <ul className="space-y-1">
              {model.mcpUsed.map((item) => (
                <li key={`mcp:${item.name}`} className="flex min-w-0 items-center gap-1.5 text-[12px] text-muted-foreground">
                  <Wrench className="h-3 w-3 shrink-0 opacity-60" aria-hidden />
                  <span className="min-w-0 truncate">{item.name}</span>
                  <span className="ml-auto shrink-0 tabular-nums opacity-70">×{item.count}</span>
                </li>
              ))}
              {model.cliUsed.map((item) => (
                <li key={`cli:${item.name}`} className="flex min-w-0 items-center gap-1.5 text-[12px] text-muted-foreground">
                  <ChevronRight className="h-3 w-3 shrink-0 opacity-60" aria-hidden />
                  <span className="min-w-0 truncate">{item.name}</span>
                  <span className="ml-auto shrink-0 tabular-nums opacity-70">×{item.count}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[12px] text-muted-foreground/60">
              {t("workbench.emptyIntegrations", { defaultValue: "No MCP/CLI used this session" })}
            </p>
          )}
        </Section>

        <Section
          title={t("workbench.outputs", { defaultValue: "Outputs" })}
          count={model.outputs.length}
          defaultOpen={false}
        >
          {model.outputs.length ? (
            <ul className="space-y-0.5">
              {model.outputs.map((output) => (
                <li key={output.path}>
                  <button
                    type="button"
                    onClick={() => onOpenFilePreview?.(output.path)}
                    disabled={!onOpenFilePreview}
                    title={output.path}
                    className="flex w-full min-w-0 items-center gap-1.5 rounded px-1 py-0.5 text-left text-[12px] text-muted-foreground enabled:hover:bg-muted/45 enabled:hover:text-foreground disabled:cursor-default"
                  >
                    <span className="min-w-0 truncate">{output.display}</span>
                    <span className="ml-auto shrink-0 space-x-1 text-[11px] tabular-nums">
                      {output.added > 0 ? <span className="text-emerald-600 dark:text-emerald-400">+{output.added}</span> : null}
                      {output.deleted > 0 ? <span className="text-destructive/80">−{output.deleted}</span> : null}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[12px] text-muted-foreground/60">
              {t("workbench.emptyOutputs", { defaultValue: "No files changed yet" })}
            </p>
          )}
        </Section>

        <Section
          title={t("workbench.sources", { defaultValue: "Sources" })}
          count={model.sources.length}
          defaultOpen={false}
        >
          {model.sources.length ? (
            <ul className="space-y-0.5">
              {model.sources.map((source) => (
                <li key={source.href} className="min-w-0">
                  <SourceLink source={source} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[12px] text-muted-foreground/60">
              {t("workbench.emptySources", { defaultValue: "No web sources yet" })}
            </p>
          )}
        </Section>
      </div>

      {/* 保留同一位置的工具组件，切换摘要不丢失预览或终端状态。
          https://react.dev/learn/preserving-and-resetting-state */}
      <div hidden={!toolsOpen} className={cn("min-h-0 min-w-0 flex-1 flex-col", toolsOpen ? "flex" : "hidden")}>
        <StartDock
          variant="embedded"
          scope={sessionKey ?? HOME_DOCK_SCOPE}
          sessionKey={sessionKey ?? null}
          getToken={dockGetToken}
          openMenuSignal={menuSignal}
        />
      </div>
    </aside>
  );
}
