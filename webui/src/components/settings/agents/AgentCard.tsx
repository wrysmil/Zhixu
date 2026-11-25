import { useTranslation } from "react-i18next";
import { Star } from "lucide-react";

import { RowMenu } from "@/components/settings/agents/AgentRow";
import { AgentGlyph } from "@/components/ui/agent-glyph";
import {
  isDefaultMainAgent,
  type AgentProfile,
  type ModelDescriptor,
  type SkillDescriptor,
  type ToolDescriptor,
} from "@/lib/agents/types";
import { AGENT_CATEGORY_LABEL_KEY } from "@/lib/agents/i18n";
import { cn } from "@/lib/utils";

export function AgentCard({
  agent,
  dimmed,
  onEdit,
  onDuplicate,
  onToggleHidden,
  onDelete,
}: {
  agent: AgentProfile;
  // 统计源数据保留在公开 props 里，卡片本身只呈现身份与职责，
  // 工具/技能/模型的细节留给编辑弹窗，避免每张卡重复同一组数字。
  agentIds: string[];
  tools: ToolDescriptor[];
  skills: SkillDescriptor[];
  models: ModelDescriptor[];
  dimmed?: boolean;
  onEdit: () => void;
  onDuplicate: () => void;
  onToggleHidden: () => void;
  onDelete?: () => void;
}) {
  const { t } = useTranslation();

  const categoryKey = agent.categoryId ? AGENT_CATEGORY_LABEL_KEY[agent.categoryId] : null;
  const isDefault = isDefaultMainAgent(agent);
  const hasSecondaryMeta = agent.type === "system" || categoryKey !== null;

  return (
    <div
      // 与 AgentRow 同语义：一张卡是一个档案的完整描述，读屏与测试都能按组取到它，
      // 不必关心当前是卡片还是列表视图。
      role="group"
      className={cn(
        "group flex flex-col rounded-panel border border-border bg-card p-4 transition-colors duration-200 ease-out",
        "hover:border-primary/30",
        isDefault && "border-primary/25 bg-primary/[0.035]",
        dimmed && "opacity-60",
      )}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control bg-accent text-[20px]"
          style={isDefault && !agent.customized ? undefined : { backgroundColor: `${agent.color}1a` }}
        >
          <AgentGlyph icon={agent.icon} size={20} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <button
              type="button"
              onClick={onEdit}
              className="block min-w-0 flex-1 cursor-pointer truncate text-left text-[15px] font-semibold text-foreground transition-colors hover:text-foreground/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {agent.name}
            </button>
            <RowMenu
              menu={{ onEdit, onDuplicate, onToggleHidden, onDelete, labelHidden: agent.hidden }}
            />
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[11px] font-medium text-muted-foreground">
            {isDefault ? (
              <span className="inline-flex items-center gap-1 text-accent-foreground">
                <Star className="h-3 w-3 fill-current" aria-hidden />
                {t("settings.agents.basics.defaultMainAgent", "Default agent")}
              </span>
            ) : null}
            {isDefault && hasSecondaryMeta ? (
              <span aria-hidden className="text-border">·</span>
            ) : null}
            {agent.type === "system" ? (
              <span>{t("settings.agents.basics.systemPreset", "系统预设")}</span>
            ) : null}
            {agent.type === "system" && categoryKey ? (
              <span aria-hidden className="text-border">·</span>
            ) : null}
            {categoryKey ? <span>{t(categoryKey, agent.categoryId ?? "")}</span> : null}
          </div>
        </div>
      </div>

      <p className="mt-3 line-clamp-2 min-h-[2.25rem] text-[12px] leading-relaxed text-muted-foreground">
        {agent.description || t("settings.agents.card.noDescription", "未填写描述")}
      </p>
    </div>
  );
}
