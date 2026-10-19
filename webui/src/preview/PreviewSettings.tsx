import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import {
  ReadOnlyRow,
  SettingsGroup,
  SettingsRow,
} from "@/components/settings/shared/SettingsControls";
import { SettingsSectionFrame } from "@/components/settings/shared/SettingsSectionFrame";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { AppearanceEditor } from "@/components/settings/appearance/AppearanceEditor";

/** The 11 non-capability settings sections the live app exposes. The four
 *  capability pages (apps/agents/skills/automations) live in
 *  PreviewCapabilities instead. */
const NORMAL_SECTIONS = [
  "overview",
  "appearance",
  "models",
  "image",
  "voice",
  "browser",
  "channels",
  "runtime",
  "advanced",
] as const;

type NormalSection = (typeof NORMAL_SECTIONS)[number];

const SECTION_LABELS: Record<NormalSection, string> = {
  overview: "总览",
  appearance: "外观",
  models: "模型",
  image: "图像生成",
  voice: "语音",
  browser: "浏览器",
  channels: "渠道",
  runtime: "运行时",
  advanced: "安全",
};

function DisabledActionRow({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action: string;
}) {
  return (
    <SettingsRow title={title} description={description}>
      <div className="flex items-center gap-2">
        <Button type="button" size="sm" disabled className="rounded-control">
          {action}
        </Button>
      </div>
    </SettingsRow>
  );
}

/**
 * Offline settings surface for `?preview=1`. It reuses the real
 * `SettingsSectionFrame` and control primitives with in-memory sample fields,
 * so the unified section styling is visible without a backend. Secrets are
 * read-only placeholders; install / OAuth / restart actions are disabled and
 * labelled as preview-only.
 */
export function PreviewSettings({
  initialSection = "overview",
  onSectionChange,
}: {
  initialSection?: NormalSection;
  onSectionChange?: (section: NormalSection) => void;
}) {
  const { t } = useTranslation();
  const [section, setSection] = useState<NormalSection>(initialSection);
  const [saved, setSaved] = useState(false);
  const changeSection = (next: NormalSection) => {
    setSection(next);
    onSectionChange?.(next);
  };

  const body: Record<NormalSection, ReactNode> = {
    overview: (
      <SettingsGroup>
        <ReadOnlyRow title="运行状态" value="本地预览（示例）" description="不代表你的真实引擎状态。" />
        <ReadOnlyRow title="当前模型" value="gpt-example-mini" />
        <ReadOnlyRow title="本次会话消息" value="3" description="示例数据，刷新后重置。" />
      </SettingsGroup>
    ),
    appearance: <AppearanceEditor preview />,
    models: (
      <SettingsGroup>
        <ReadOnlyRow title="推理模型" value="gpt-example-mini" />
        <ReadOnlyRow title="API 密钥" value="••••••••••42" description="预览仅展示只读示例密钥。" />
        <DisabledActionRow title="添加自定义提供方" description="需连接后端与真实凭据。" action="OAuth 连接" />
      </SettingsGroup>
    ),
    image: (
      <SettingsGroup>
        <ReadOnlyRow title="图像模型" value="image-example-large" />
        <DisabledActionRow title="重启生效" description="图像设置变更需重启引擎。" action="重启引擎" />
      </SettingsGroup>
    ),
    voice: (
      <SettingsGroup>
        <ReadOnlyRow title="转写模型" value="whisper-example" />
        <ReadOnlyRow title="音色" value="nova（示例）" />
      </SettingsGroup>
    ),
    browser: (
      <SettingsGroup>
        <ReadOnlyRow title="无头浏览器" value="已启用（示例）" />
        <DisabledActionRow title="重启生效" description="浏览器配置变更需重启。" action="重启引擎" />
      </SettingsGroup>
    ),
    channels: (
      <SettingsGroup>
        <ReadOnlyRow title="WebSocket" value="已连接（示例）" />
        <DisabledActionRow title="安装渠道插件" description="安装会改动运行环境。" action="安装" />
      </SettingsGroup>
    ),
    runtime: (
      <SettingsGroup>
        <ReadOnlyRow title="运行面" value="本地预览" />
        <DisabledActionRow title="重启引擎" description="预览不会真正重启。" action="重启" />
      </SettingsGroup>
    ),
    advanced: (
      <SettingsGroup>
        <ReadOnlyRow title="PTH 安全守卫" value="已启用（示例）" />
        <ReadOnlyRow title="沙箱后端" value="受限（示例）" />
      </SettingsGroup>
    ),
  };

  return (
    <div className="flex h-full min-w-0 bg-[radial-gradient(ellipse_at_top_right,hsl(var(--primary)/0.045),transparent_60%)]">
      <nav
        aria-label={t("settings.sidebar.ariaLabel", { defaultValue: "Settings sections" })}
        className="hidden w-[190px] shrink-0 flex-col gap-1 overflow-y-auto border-r border-border/60 bg-card/35 px-3 py-6 md:flex"
      >
        <p className="mb-3 px-3 text-[10px] font-medium tracking-[0.18em] text-muted-foreground">偏好与设置</p>
        {NORMAL_SECTIONS.map((key) => (
          <button
            key={key}
            type="button"
            aria-current={section === key ? "page" : undefined}
            onClick={() => changeSection(key)}
            className={cn(
              "rounded-control border px-3 py-2 text-left text-[12px] transition-colors duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              section === key
                ? "border-primary/15 bg-primary/[0.08] font-medium text-primary"
                : "border-transparent text-foreground/75 hover:bg-accent/50",
            )}
          >
            {SECTION_LABELS[key]}
          </button>
        ))}
      </nav>
      <div className="min-w-0 flex-1 overflow-y-auto">
        <label className="mx-5 mt-5 flex items-center gap-3 text-[12px] text-muted-foreground md:hidden">
          设置页面
          <select aria-label="设置页面" value={section} onChange={(event) => changeSection(event.target.value as NormalSection)} className="min-w-0 flex-1 rounded-control border border-input bg-card px-3 py-2 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {NORMAL_SECTIONS.map((key) => <option key={key} value={key}>{SECTION_LABELS[key]}</option>)}
          </select>
        </label>
        <SettingsSectionFrame
          title={SECTION_LABELS[section]}
          description={"界面预览 · 示例内容，保存与操作均不会改动真实配置。"}
          actions={section === "appearance" ? undefined :
            <Button
              type="button"
              size="sm"
              className="h-8 rounded-control bg-primary text-primary-foreground"
              onClick={() => setSaved(true)}
            >
              {saved ? "已保存（示例）" : "保存"}
            </Button>
          }
        >
          {body[section]}
        </SettingsSectionFrame>
      </div>
    </div>
  );
}
