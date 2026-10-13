import { useCallback, useState } from "react";
import {
  ArrowRight,
  Bot,
  CalendarClock,
  Feather,
  Plug,
  Send,
  Sparkles,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import type { WorkspaceView } from "@/components/workspace/contracts";
import { cn } from "@/lib/utils";

interface WorkspaceHomeProps {
  preview?: boolean;
  /** Normal entry: seed the assistant composer and navigate. Preview: no send. */
  onSubmitTask: (text: string) => void;
  onOpenAssistant: () => void;
  onOpenCreative: () => void;
  onOpenCapability: (view: WorkspaceView) => void;
}

interface CapabilityLink {
  view: WorkspaceView;
  labelKey: string;
  defaultLabel: string;
  icon: typeof Bot;
}

const CAPABILITY_LINKS: CapabilityLink[] = [
  { view: "agents", labelKey: "sidebar.agents", defaultLabel: "Agents", icon: Bot },
  { view: "skills", labelKey: "sidebar.skills.title", defaultLabel: "Skills", icon: Sparkles },
  { view: "automations", labelKey: "sidebar.automations", defaultLabel: "Automations", icon: CalendarClock },
  { view: "apps", labelKey: "sidebar.apps", defaultLabel: "Apps", icon: Plug },
];

export function WorkspaceHome({
  preview = false,
  onSubmitTask,
  onOpenAssistant,
  onOpenCreative,
  onOpenCapability,
}: WorkspaceHomeProps) {
  const { t } = useTranslation();
  const [task, setTask] = useState("");

  const submit = useCallback(() => {
    const trimmed = task.trim();
    if (!trimmed) return;
    onSubmitTask(trimmed);
    setTask("");
  }, [onSubmitTask, task]);

  return (
    <div className="flex h-full w-full min-w-0 flex-col overflow-y-auto bg-background">
      <div className="mx-auto flex w-full max-w-[1040px] flex-1 flex-col gap-8 px-5 py-10 sm:px-8 lg:px-10">
        <header className="flex flex-col gap-2">
          <p className="text-[13px] font-medium text-primary">
            {t("workspace.home.eyebrow", { defaultValue: "知序工作台" })}
          </p>
          <h1 className="text-[30px] font-semibold leading-tight text-foreground">
            {t("workspace.home.title", { defaultValue: "今天想完成什么？" })}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("workspace.home.subtitle", {
              defaultValue: "一句自由任务直接开始，或从下面的能力进入。",
            })}
          </p>
        </header>

        <section className="flex flex-col gap-3">
          <div
            className={cn(
              "flex flex-col gap-3 rounded-prominent border border-border/70 bg-settings-surface p-4",
              "shadow-sm",
            )}
          >
            <textarea
              value={task}
              onChange={(event) => setTask(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  submit();
                }
              }}
              rows={3}
              aria-label={t("workspace.home.taskAria", { defaultValue: "自由任务" })}
              placeholder={t("workspace.home.taskPlaceholder", {
                defaultValue: "写下你的任务，例如「把这周的记忆整理成一篇笔记」。",
              })}
              className={cn(
                "w-full resize-none rounded-control border-0 bg-transparent text-[15px] text-foreground",
                "placeholder:text-muted-foreground/70 focus:outline-none",
              )}
            />
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-muted-foreground/80">
                {preview
                  ? t("workspace.home.previewHint", {
                      defaultValue: "界面预览：不会真正发送",
                    })
                  : t("workspace.home.submitHint", {
                      defaultValue: "点击后进入助手，仍需你确认发送。",
                    })}
              </span>
              <Button
                type="button"
                onClick={submit}
                disabled={!task.trim()}
                className="h-9 gap-2 rounded-control bg-primary px-4 text-primary-foreground"
              >
                <Send className="h-4 w-4" aria-hidden />
                {t("workspace.home.submit", { defaultValue: "开始" })}
              </Button>
            </div>
          </div>
        </section>

        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-[15px] font-semibold text-foreground">
              {t("workspace.home.capabilitiesTitle", { defaultValue: "能力入口" })}
            </h2>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {CAPABILITY_LINKS.map((link) => {
              const Icon = link.icon;
              return (
                <button
                  key={link.view}
                  type="button"
                  onClick={() => onOpenCapability(link.view)}
                  className={cn(
                    "flex items-center gap-3 rounded-panel border border-border/70 bg-settings-surface px-4 py-3 text-left",
                    "transition-colors hover:border-primary/40 hover:bg-accent/40",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
                  )}
                >
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-mark bg-primary/10 text-primary">
                    <Icon className="h-5 w-5" aria-hidden />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                    {t(link.labelKey, { defaultValue: link.defaultLabel })}
                  </span>
                  <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground/60" aria-hidden />
                </button>
              );
            })}
          </div>
        </section>

        <section className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <button
            type="button"
            onClick={onOpenCreative}
            className={cn(
              "flex items-start gap-3 rounded-panel border border-border/70 bg-settings-surface p-4 text-left",
              "transition-colors hover:border-primary/40 hover:bg-accent/40",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
            )}
          >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-mark bg-primary/10 text-primary">
              <Feather className="h-5 w-5" aria-hidden />
            </span>
            <span className="flex min-w-0 flex-col gap-1">
              <span className="text-sm font-semibold text-foreground">
                {t("workspace.home.creativeTitle", { defaultValue: "创作空间" })}
              </span>
              <span className="text-xs text-muted-foreground">
                {t("workspace.home.creativeDesc", {
                  defaultValue: "把碎片素材整理成一篇文章。",
                })}
              </span>
            </span>
          </button>
          <button
            type="button"
            onClick={onOpenAssistant}
            className={cn(
              "flex items-start gap-3 rounded-panel border border-border/70 bg-settings-surface p-4 text-left",
              "transition-colors hover:border-primary/40 hover:bg-accent/40",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
            )}
          >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-mark bg-primary/10 text-primary">
              <Bot className="h-5 w-5" aria-hidden />
            </span>
            <span className="flex min-w-0 flex-col gap-1">
              <span className="text-sm font-semibold text-foreground">
                {t("workspace.home.assistantTitle", { defaultValue: "通用助手" })}
              </span>
              <span className="text-xs text-muted-foreground">
                {t("workspace.home.assistantDesc", {
                  defaultValue: "保留 Skill、智能体等全部已有能力。",
                })}
              </span>
            </span>
          </button>
        </section>
      </div>
    </div>
  );
}
