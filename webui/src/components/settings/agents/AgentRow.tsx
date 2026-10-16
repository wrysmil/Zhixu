import { useTranslation } from "react-i18next";
import { Copy, Eye, EyeOff, MoreHorizontal, Pencil, Trash2 } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/settings/agents/shared";
import type { AgentProfile } from "@/lib/agents/types";
import { AGENT_CATEGORY_LABEL_KEY } from "@/lib/agents/i18n";
import { cn } from "@/lib/utils";

export interface AgentRowMenu {
  onEdit: () => void;
  onDuplicate: () => void;
  onToggleHidden: () => void;
  /** system 类型后端会返 409，调用方不传就不渲染这一项。 */
  onDelete?: () => void;
  labelHidden: boolean;
}

export function RowMenu({ menu }: { menu: AgentRowMenu }) {  const { t } = useTranslation();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={t("settings.agents.card.menu", "更多操作")}
          className="shrink-0 rounded-sm p-1.5 text-muted-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <MoreHorizontal className="h-4 w-4" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-40">
        <DropdownMenuItem onSelect={menu.onEdit} className="cursor-pointer gap-2 text-[13px]">
          <Pencil className="h-3.5 w-3.5" aria-hidden />
          {t("settings.agents.card.edit", "编辑")}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={menu.onDuplicate} className="cursor-pointer gap-2 text-[13px]">
          <Copy className="h-3.5 w-3.5" aria-hidden />
          {t("settings.agents.card.duplicate", "复制")}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={menu.onToggleHidden} className="cursor-pointer gap-2 text-[13px]">
          {menu.labelHidden ? (
            <Eye className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <EyeOff className="h-3.5 w-3.5" aria-hidden />
          )}
          {menu.labelHidden
            ? t("settings.agents.card.show", "显示")
            : t("settings.agents.card.hide", "隐藏")}
        </DropdownMenuItem>
        {menu.onDelete ? (
          <DropdownMenuItem
            onSelect={menu.onDelete}
            className="cursor-pointer gap-2 text-[13px] text-destructive focus:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden />
            {t("settings.agents.card.delete", "删除")}
          </DropdownMenuItem>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AgentRow({
  agent,
  dimmed,
  onEdit,
  onDuplicate,
  onToggleHidden,
  onDelete,
}: {
  agent: AgentProfile;
  dimmed?: boolean;
  onEdit: () => void;
  onDuplicate: () => void;
  onToggleHidden: () => void;
  onDelete?: () => void;
}) {
  const { t } = useTranslation();
  const categoryKey = agent.categoryId ? AGENT_CATEGORY_LABEL_KEY[agent.categoryId] : null;

  return (
    <div
      role="group"
      className={cn(
        "group flex items-center gap-3 rounded-panel border border-transparent bg-settings-surface px-3.5 py-3 transition-colors",
        "hover:border-border/70 hover:bg-muted/40",
        dimmed && "opacity-60",
      )}
    >
      <button
        type="button"
        onClick={onEdit}
        className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span
          aria-hidden
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-control text-[20px]"
          style={{ backgroundColor: `${agent.color}1a` }}
        >
          {agent.icon}
        </span>

        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="truncate text-[14px] font-semibold text-foreground">
              {agent.name}
            </span>
            {agent.type === "system" ? (
              <Badge tone="info">{t("settings.agents.basics.systemPreset", "系统预设")}</Badge>
            ) : null}
            {agent.hidden ? (
              <Badge tone="muted">{t("settings.agents.basics.hidden", "已隐藏")}</Badge>
            ) : null}
            {categoryKey ? (
              <span className="text-[11px] text-muted-foreground">
                {t(categoryKey, agent.categoryId ?? "")}
              </span>
            ) : null}
          </span>

          <span className="mt-0.5 block truncate text-[12px] text-muted-foreground">
            {agent.description || t("settings.agents.card.noDescription", "未填写描述")}
          </span>
        </span>
      </button>

      <RowMenu
        menu={{ onEdit, onDuplicate, onToggleHidden, onDelete, labelHidden: agent.hidden }}
      />
    </div>
  );
}
