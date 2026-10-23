import { useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";

import { AgentGlyph } from "@/components/ui/agent-glyph";
import {
  floatingItemClassName,
  floatingItemFocusClassName,
} from "@/components/ui/floating-surface";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  DEFAULT_MAIN_AGENT_ID,
  isDefaultMainAgent,
  type AgentProfile,
} from "@/lib/agents/types";
import { cn } from "@/lib/utils";

/** 未显式指定时接管主回合的档案，与后端 catalog 的 commander 一致。 */
export const DEFAULT_SESSION_AGENT_ID = DEFAULT_MAIN_AGENT_ID;

/** 可选档案：隐藏档案不进入任何选择列表（与 @ 提及面板同一口径）。 */
export function selectableAgents(agents: AgentProfile[]): AgentProfile[] {
  return agents.filter((agent) => !agent.hidden);
}

function pickableAgent(agents: AgentProfile[], agentId: string | null): AgentProfile | null {
  if (!agentId) return null;
  return selectableAgents(agents).find((agent) => agent.id === agentId) ?? null;
}

/**
 * 会话选择的落点。档案被隐藏或删除时回落到默认主 Agent，
 * 避免把已经不可用的 agentId 发给服务端。
 */
export function resolveSessionAgent(
  agents: AgentProfile[],
  agentId: string,
): AgentProfile | null {
  return pickableAgent(agents, agentId) ?? pickableAgent(agents, DEFAULT_SESSION_AGENT_ID);
}

export interface AgentPickerListProps {
  agents: AgentProfile[];
  value: string | null;
  onPick: (agentId: string) => void;
  className?: string;
}

/** 档案列表：默认主 Agent 打标，当前生效项打勾。 */
export function AgentPickerList({ agents, value, onPick, className }: AgentPickerListProps) {
  const { t } = useTranslation();
  const options = selectableAgents(agents);

  if (options.length === 0) {
    return (
      <p className="px-3 py-2 text-[12.5px] text-muted-foreground">
        {t("thread.composer.agent.empty", { defaultValue: "No agents available yet" })}
      </p>
    );
  }

  return (
    <div role="group" className={cn("flex flex-col gap-0.5", className)}>
      {options.map((agent) => (
        <button
          key={agent.id}
          type="button"
          data-agent-id={agent.id}
          onClick={() => onPick(agent.id)}
          className={cn(
            floatingItemClassName,
            floatingItemFocusClassName,
            "flex min-h-[40px] w-full cursor-default items-center gap-2 px-3 py-2 text-left",
          )}
        >
          <AgentGlyph icon={agent.icon} size={16} className="text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-foreground">
            {agent.name}
          </span>
          {isDefaultMainAgent(agent) ? (
            <span className="shrink-0 text-[11.5px] text-muted-foreground">
              {t("thread.composer.agent.defaultTag", { defaultValue: "Default" })}
            </span>
          ) : null}
          {agent.id === value ? (
            <Check className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
          ) : null}
        </button>
      ))}
    </div>
  );
}

export interface AgentChipProps {
  /** 本轮实际生效的档案；null 时整个胶囊不渲染。 */
  agent: AgentProfile | null;
  agents: AgentProfile[];
  onPick: (agentId: string) => void;
  disabled?: boolean;
  isHero?: boolean;
}

/**
 * 模型徽章旁的可点击胶囊：显示本轮接管主回合的档案，点击直接改选。
 * 显示的是生效值而不是会话值——草稿里写了 ``@档案`` 时以草稿为准。
 */
export function AgentChip({ agent, agents, onPick, disabled = false, isHero = false }: AgentChipProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  if (!agent) return null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={t("thread.composer.agent.chip", {
            name: agent.name,
            defaultValue: "Current agent: {{name}}",
          })}
          className={cn(
            "inline-flex h-7 max-w-[10rem] items-center gap-1.5 rounded-lg px-2",
            "text-[12px] font-medium text-muted-foreground/90 transition-colors",
            "hover:bg-muted/55 hover:text-foreground disabled:pointer-events-none disabled:opacity-55",
            isHero ? "max-w-[12rem]" : null,
          )}
        >
          <AgentGlyph icon={agent.icon} size={14} className="shrink-0" />
          <span className="truncate">{agent.name}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        side="top"
        sideOffset={8}
        className="max-h-[min(20rem,60vh)] w-[min(18rem,calc(100vw-2rem))] overflow-y-auto"
      >
        <AgentPickerList
          agents={agents}
          value={agent.id}
          onPick={(agentId) => {
            onPick(agentId);
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
