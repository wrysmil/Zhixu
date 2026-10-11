import { useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronRight,
  PanelRightClose,
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

// Mirrors the rail's literal `w-[19rem]` below (Tailwind can't see dynamic classes).
export const SESSION_WORKBENCH_RAIL_WIDTH = "19rem";

interface SessionWorkbenchProps {
  model: SessionWorkbenchModel;
  onOpenFilePreview?: (path: string) => void;
  onCollapse: () => void;
  sessionKey?: string | null;
  getToken?: () => string;
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
}: SessionWorkbenchProps) {
  const { t, i18n } = useTranslation();
  const updated = formatUpdatedAt(model.updatedAtMs, i18n.language);
  const hasIntegrations = model.mcpUsed.length > 0 || model.cliUsed.length > 0;
  // StartDock 以 getToken 引用为壳列表拉取依赖；这里保持跨渲染稳定。
  const dockGetToken = useCallback(() => getToken?.() ?? "", [getToken]);

  return (
    <aside
      data-testid="session-workbench"
      aria-label={t("workbench.title", { defaultValue: "Workbench" })}
      className="relative flex w-[19rem] shrink-0 flex-col overflow-hidden border-l border-border/60 bg-muted/20"
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
          aria-label={t("workbench.collapse", { defaultValue: "Collapse workbench" })}
          onClick={onCollapse}
          className="ml-auto grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 hover:bg-muted/60 hover:text-foreground"
        >
          <PanelRightClose className="h-3.5 w-3.5" aria-hidden />
        </button>
      </header>

      <div className="max-h-[45%] min-h-0 shrink overflow-y-auto scrollbar-thin">
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

      {/* 与首页同源的标签式右坞（embedded）：文件/浏览器/终端统一在此，scope=会话 key，
          标签与接管清单按 scope 独立持久化；预览仍上抛宿主外置面板。 */}
      <StartDock
        variant="embedded"
        scope={sessionKey ?? HOME_DOCK_SCOPE}
        sessionKey={sessionKey ?? null}
        getToken={dockGetToken}
        onOpenFilePreview={onOpenFilePreview}
      />
    </aside>
  );
}
