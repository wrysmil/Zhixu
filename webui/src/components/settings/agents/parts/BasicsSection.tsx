import { useTranslation } from "react-i18next";
import { Lock } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AgentIconPicker } from "@/components/ui/agent-icon-picker";
import { Badge, ColorSwatch, FieldLabel } from "@/components/settings/agents/shared";
import type { AgentCategory, AgentProfile } from "@/lib/agents/types";
import { categoryById } from "@/lib/agents/types";

function formatUpdatedAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function BasicsSection({
  profile,
  categories,
  isNew,
  onChange,
  variant = "all",
}: {
  profile: AgentProfile;
  categories: AgentCategory[];
  isNew: boolean;
  onChange: (patch: Partial<AgentProfile>) => void;
  variant?: "all" | "identity" | "details";
}) {
  const { t } = useTranslation();
  const category = categoryById(profile.categoryId);

  return (
    <div className="space-y-4">
      {variant !== "identity" ? <div>
        <FieldLabel
          htmlFor="agent-id"
          hint={
            profile.type === "system"
              ? t("settings.agents.basics.idLocked", "系统预设不可修改")
              : undefined
          }
        >
          {t("settings.agents.basics.id", "ID")}
        </FieldLabel>
        <div className="relative">
          <Input
            id="agent-id"
            value={profile.id}
            readOnly
            disabled={!isNew}
            className="pr-8 font-mono text-[13px]"
          />
          {profile.type === "system" ? (
            <Lock
              className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
          ) : null}
        </div>
      </div> : null}

      {variant !== "details" ? <><div>
        <FieldLabel htmlFor="agent-name">{t("settings.agents.basics.name", "名称")}</FieldLabel>
        <Input
          id="agent-name"
          value={profile.name}
          onChange={(event) => onChange({ name: event.target.value })}
          placeholder={t("settings.agents.basics.namePlaceholder", "例如：SEO 写手")}
          className="text-[13px]"
        />
      </div>

      <div>
        <FieldLabel htmlFor="agent-description">
          {t("settings.agents.workspace.description", "一句话说明")}
        </FieldLabel>
        <Textarea
          id="agent-description"
          value={profile.description}
          onChange={(event) => onChange({ description: event.target.value })}
          placeholder={t(
            "settings.agents.basics.descriptionPlaceholder",
            "一句话说清它擅长什么、适合什么场景。",
          )}
          className="min-h-[4.5rem] text-[13px]"
        />
      </div></> : null}

      {variant !== "identity" ? <><div className="grid gap-4 sm:grid-cols-2">
        <div>
          <FieldLabel>{t("settings.agents.basics.category", "分类")}</FieldLabel>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="flex h-10 w-full items-center gap-2 rounded-control border border-border bg-background px-3 text-left text-[13px] transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {category ? (
                  <>
                    <span
                      aria-hidden
                      className="h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: category.color }}
                    />
                    <span className="min-w-0 flex-1 truncate">{category.name}</span>
                  </>
                ) : (
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {t("settings.agents.basics.noCategory", "未分类")}
                  </span>
                )}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-56">
              <DropdownMenuItem
                onSelect={() => onChange({ categoryId: null })}
                className="text-[13px]"
              >
                {t("settings.agents.basics.noCategory", "未分类")}
              </DropdownMenuItem>
              {categories.map((item) => (
                <DropdownMenuItem
                  key={item.id}
                  onSelect={() => onChange({ categoryId: item.id })}
                  className="flex items-center gap-2 text-[13px]"
                >
                  <span
                    aria-hidden
                    className="h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{ backgroundColor: item.color }}
                  />
                  {item.name}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        <div>
          <FieldLabel>{t("settings.agents.basics.appearance", "图标与颜色")}</FieldLabel>
          <div className="flex items-start gap-2">
            <AgentIconPicker
              value={profile.icon}
              onChange={(icon) => onChange({ icon })}
            />
            <div className="min-w-0 flex-1 pt-0.5">
              <ColorSwatch value={profile.color} onChange={(color) => onChange({ color })} />
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-t border-border/45 pt-3 text-[12px] text-muted-foreground">
        <Badge tone={profile.type === "system" ? "info" : "neutral"}>
          {profile.type === "system"
            ? t("settings.agents.basics.systemPreset", "系统预设")
            : t("settings.agents.basics.custom", "自定义")}
        </Badge>
        {profile.hidden ? (
          <Badge tone="muted">{t("settings.agents.basics.hidden", "已隐藏")}</Badge>
        ) : null}
        <span className="ml-auto">
          {t("settings.agents.basics.updatedAt", "上次修改", { time: formatUpdatedAt(profile.updatedAt) })}
        </span>
      </div></> : null}
    </div>
  );
}
