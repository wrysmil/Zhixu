import { useTranslation } from "react-i18next";
import { Brain, FolderTree, Sparkles } from "lucide-react";

import { Badge, DefaultMainAgentBadge } from "@/components/settings/agents/shared";
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
import { breakdown, describeCapability, describeSubAgents } from "@/lib/agents/summary";
import { cn } from "@/lib/utils";

function Stat({ icon, value }: { icon: React.ReactNode; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
      <span aria-hidden className="text-muted-foreground/70">
        {icon}
      </span>
      <span className="truncate">{value}</span>
    </span>
  );
}

export function AgentCard({
  agent,
  agentIds,
  tools,
  skills,
  models,
  dimmed,
  onEdit,
  onDuplicate,
  onToggleHidden,
  onDelete,
}: {
  agent: AgentProfile;
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

  const toolIds = tools.map((tool) => tool.name);
  const lockedToolIds = tools.filter((tool) => tool.locked).map((tool) => tool.name);
  const skillIds = skills.map((skill) => skill.name);
  const selectableAgentIds = agentIds.filter((id) => id !== agent.id);

  const toolText = describeCapability(
    breakdown(agent.tools, toolIds, lockedToolIds),
    t("settings.agents.noun.tools", "工具"),
    t,
  );
  const skillText = describeCapability(
    breakdown(agent.skills, skillIds),
    t("settings.agents.noun.skills", "技能"),
    t,
  );
  const subAgentText = describeSubAgents(breakdown(agent.subAgents, selectableAgentIds), t);
  const modelLabel = models.find((model) => model.id === agent.modelId)?.label ?? null;
  const categoryKey = agent.categoryId ? AGENT_CATEGORY_LABEL_KEY[agent.categoryId] : null;
  const isDefault = isDefaultMainAgent(agent);

  return (
    <div
      // 与 AgentRow 同语义：一张卡是一个档案的完整描述，读屏与测试都能按组取到它，
      // 不必关心当前是卡片还是列表视图。
      role="group"
      className={cn(
        "group flex flex-col rounded-panel border border-border/50 bg-settings-surface p-4 transition-all duration-200",
        "hover:border-border hover:shadow-md hover:-translate-y-0.5",
        isDefault && "border-amber-300/70 bg-amber-50/45 ring-1 ring-amber-300/50 hover:border-amber-400/70 dark:border-amber-700/40 dark:bg-amber-950/15 dark:ring-amber-700/40",
        dimmed && "opacity-60",
      )}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-[22px]"
          style={{ backgroundColor: `${agent.color}1a` }}
        >
          <AgentGlyph icon={agent.icon} size={22} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <button
                type="button"
                onClick={onEdit}
                className="block w-full cursor-pointer truncate text-left text-[15px] font-semibold text-foreground transition-colors hover:text-foreground/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {agent.name}
              </button>
              <code className="mt-0.5 block truncate font-mono text-[11px] text-muted-foreground">
                {agent.id}
              </code>
            </div>
            <RowMenu
              menu={{ onEdit, onDuplicate, onToggleHidden, onDelete, labelHidden: agent.hidden }}
            />
          </div>
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-1">
        {isDefault ? <DefaultMainAgentBadge /> : null}
        {agent.type === "system" ? (
          <Badge tone="info">{t("settings.agents.basics.systemPreset", "系统预设")}</Badge>
        ) : null}
        {categoryKey ? (
          <span className="text-[11px] text-muted-foreground">
            {t(categoryKey, agent.categoryId ?? "")}
          </span>
        ) : null}
      </div>

      <p className="mt-2 line-clamp-2 min-h-[2.25rem] text-[12px] leading-relaxed text-muted-foreground">
        {agent.description || t("settings.agents.card.noDescription", "未填写描述")}
      </p>

      <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border/50 pt-2.5">
        <Stat icon={<Sparkles className="h-3 w-3" />} value={toolText} />
        <Stat icon={<Brain className="h-3 w-3" />} value={skillText} />
        <Stat icon={<FolderTree className="h-3 w-3" />} value={subAgentText} />
      </div>

      <p className="mt-1.5 truncate text-[11px] text-muted-foreground/85">
        {modelLabel ?? t("settings.agents.card.modelAuto", "跟随全局模型")}
      </p>
    </div>
  );
}