import { useTranslation } from "react-i18next";
import { Bot, CalendarClock, Plug, Sparkles } from "lucide-react";

import { StatusPill } from "@/components/settings/shared/SettingsControls";
import { SettingsSectionFrame } from "@/components/settings/shared/SettingsSectionFrame";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type CapabilityKind = "apps" | "agents" | "skills" | "automations";

interface CapabilityItem {
  name: string;
  summary: string;
  status: string;
  tone: "neutral" | "success" | "warning";
  action: string;
}

const CAPABILITIES: Record<
  CapabilityKind,
  { title: string; description: string; icon: typeof Bot; items: CapabilityItem[] }
> = {
  apps: {
    title: "应用",
    description: "接入的第三方 CLI 应用与渠道适配器。",
    icon: Plug,
    items: [
      { name: "GitHub", summary: "仓库、议题与 Pull Request 操作。", status: "已连接", tone: "success", action: "配置" },
      { name: "文件系统", summary: "受限工作区读写。", status: "内置", tone: "neutral", action: "查看" },
    ],
  },
  agents: {
    title: "智能体",
    description: "子智能体的模型、工具、技能与子代理配置。",
    icon: Bot,
    items: [
      { name: "研究员", summary: "多轮检索与汇总。", status: "启用", tone: "success", action: "编辑" },
      { name: "代码助手", summary: "带终端与文件工具。", status: "停用", tone: "warning", action: "编辑" },
    ],
  },
  skills: {
    title: "Skill",
    description: "内置技能与市场可安装技能。",
    icon: Sparkles,
    items: [
      { name: "cron", summary: "周期任务调度。", status: "已启用", tone: "success", action: "查看" },
      { name: "image-generation", summary: "图像生成封装。", status: "可安装", tone: "warning", action: "安装" },
    ],
  },
  automations: {
    title: "自动化",
    description: "定时任务与长时目标。",
    icon: CalendarClock,
    items: [
      { name: "每日回顾", summary: "每天 09:00 生成摘要。", status: "运行中", tone: "success", action: "编辑" },
      { name: "周报整理", summary: "每周一聚合上周记忆。", status: "已暂停", tone: "neutral", action: "编辑" },
    ],
  },
};

/**
 * Offline capability pages for `?preview=1`. Cards use the same section frame
 * and status pill as the live capability pages, but with fixed in-memory
 * examples; install / connect / edit actions are disabled and labelled as
 * preview-only so nothing mutates real configuration.
 */
export function PreviewCapabilities({ kind }: { kind: CapabilityKind }) {
  const { t } = useTranslation();
  const config = CAPABILITIES[kind];
  const Icon = config.icon;

  return (
    <div className="h-full min-w-0 overflow-y-auto bg-background">
      <SettingsSectionFrame
        title={config.title}
        description={`${config.description}界面预览 · 示例内容。`}
        actions={
          <Button type="button" size="sm" disabled className="h-8 rounded-control">
            新建
          </Button>
        }
      >
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {config.items.map((item) => (
            <div
              key={item.name}
              className={cn(
                "flex items-start gap-3 rounded-panel border border-border/70",
                "bg-settings-surface p-4",
              )}
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-mark bg-primary/10 text-primary">
                <Icon className="h-5 w-5" aria-hidden />
              </span>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-semibold text-foreground">
                    {item.name}
                  </span>
                  <StatusPill tone={item.tone}>{item.status}</StatusPill>
                </div>
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {item.summary}
                </p>
                <div className="mt-1 flex items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    disabled
                    className="h-7 rounded-control"
                    aria-label={t("capabilities.previewDisabled", {
                      defaultValue: `${item.action}（预览已禁用）`,
                    })}
                  >
                    {item.action}
                  </Button>
                  <span className="text-[11px] text-amber-700 dark:text-amber-300">
                    预览禁用
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </SettingsSectionFrame>
    </div>
  );
}
