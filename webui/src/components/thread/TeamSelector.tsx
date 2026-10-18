import { useState } from "react";
import { Check, ChevronDown, ChevronRight, Users } from "lucide-react";
import { useTranslation } from "react-i18next";

import { AgentPickerList, selectableAgents } from "@/components/thread/AgentPicker";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  floatingItemClassName,
  floatingItemFocusClassName,
} from "@/components/ui/floating-surface";
import type { AgentProfile } from "@/lib/agents/types";
import type { NewChatTeam, TeamBinding } from "@/lib/types";
import type { TeamOption } from "@/lib/teams/types";
import { cn } from "@/lib/utils";

export type { TeamOption } from "@/lib/teams/types";

/** 「不使用团队」这一行在二级展开态里的标识，与任何 teamId 都不会撞。 */
const NO_TEAM_SCOPE = "";

/** 团队二级列表：Leader 在前，成员按定义顺序跟后；解析不到的档案直接跳过。 */
function teamAgents(agents: AgentProfile[], option: TeamOption): AgentProfile[] {
  const byId = new Map(selectableAgents(agents).map((agent) => [agent.id, agent]));
  return [option.leaderAgentId, ...option.memberAgentIds]
    .map((agentId) => byId.get(agentId))
    .filter((agent): agent is AgentProfile => agent !== undefined);
}

export interface TeamSelectorProps {
  options: TeamOption[];
  /** Local choice for a chat that has no messages yet. */
  value: NewChatTeam | null;
  valueName: string | null;
  /** Server-confirmed binding; the picker becomes a read-only chip when set. */
  bound: TeamBinding | null;
  onChange: (team: NewChatTeam | null, teamName: string | null) => void;
  /** 二级 Agent 选择的候选档案；为空时二级退化，团队行仍是单击即选。 */
  agents?: AgentProfile[];
  /** 当前生效的档案 id，用于二级列表打勾。 */
  agentValue?: string | null;
  onAgentChange?: (agentId: string) => void;
  isHero?: boolean;
  disabled?: boolean;
}

/**
 * Whether the chat surface shows the picker, a read-only binding, or nothing.
 *
 * Contract §4: temporary chats never offer a team, a chat that already has messages
 * keeps the binding it was created with, and only the server can confirm one.
 * ``temporaryRequested`` is the hero's "next chat is temporary" toggle — that send
 * goes to ``new_temporary_chat`` and would silently drop any team choice.
 */
export function teamPickerVisibility(state: {
  temporary: boolean;
  temporaryRequested?: boolean;
  hasMessages: boolean;
  bound: TeamBinding | null;
  canSelect: boolean;
}): "select" | "bound" | "hidden" {
  if (state.bound) return "bound";
  if (state.temporary || state.temporaryRequested || state.hasMessages || !state.canSelect) {
    return "hidden";
  }
  return "select";
}

export function TeamSelector({
  options,
  value,
  valueName,
  bound,
  onChange,
  agents = [],
  agentValue = null,
  onAgentChange,
  isHero = false,
  disabled = false,
}: TeamSelectorProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const selectedName = valueName ?? value?.teamId ?? null;
  // 拿不到档案列表时不留一个进不去的二级入口（例如档案接口失败）。
  const canPickAgent = selectableAgents(agents).length > 0 && onAgentChange !== undefined;

  const pickAgent = (agentId: string) => {
    onAgentChange?.(agentId);
    setOpen(false);
    setExpanded(null);
  };

  if (bound) {
    return (
      <div className="inline-flex">
        <span
          data-team-bound
          title={t("thread.composer.team.bound", {
            name: bound.teamName,
            revision: bound.teamRevision,
          })}
          className={cn(
            "inline-flex h-7 max-w-[15rem] items-center gap-1.5 rounded-lg px-2",
            "text-[12px] font-medium text-primary/90",
            isHero ? "max-w-[18rem]" : null,
          )}
        >
          <Users className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">
            {t("thread.composer.team.chip", { name: bound.teamName })}
          </span>
        </span>
      </div>
    );
  }

  return (
    <div className="inline-flex">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            aria-label={
              selectedName
                ? t("thread.composer.team.chip", { name: selectedName })
                : t("thread.composer.team.aria")
            }
            className={cn(
              "inline-flex h-7 max-w-[15rem] items-center gap-1.5 rounded-lg px-2",
              "text-[12px] font-medium text-muted-foreground/90 transition-colors",
              "hover:bg-muted/55 hover:text-foreground disabled:pointer-events-none disabled:opacity-55",
              selectedName && "text-primary",
            )}
          >
            <Users className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">
              {selectedName ?? t("thread.composer.team.select")}
            </span>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          side="bottom"
          sideOffset={8}
          className="w-[min(22rem,calc(100vw-2rem))]"
        >
          {options.length === 0 ? (
            <p className="px-3 py-2 text-[12.5px] text-muted-foreground">
              {t("thread.composer.team.empty")}
            </p>
          ) : (
            <div role="group" className="flex flex-col gap-0.5">
              {options.map((option) => (
                <div key={option.id} className="flex flex-col gap-0.5">
                  <button
                    type="button"
                    onClick={() => {
                      onChange({ teamId: option.id, teamRevision: option.revision }, option.name);
                      if (!canPickAgent) {
                        setOpen(false);
                        return;
                      }
                      // 选团队即绑定，二级默认落在该团队 Leader 上，用户可在二级里改写。
                      const scoped = teamAgents(agents, option);
                      if (scoped.length > 0) onAgentChange?.(scoped[0].id);
                      setExpanded(option.id);
                    }}
                    aria-expanded={canPickAgent ? expanded === option.id : undefined}
                    className={cn(
                      floatingItemClassName,
                      floatingItemFocusClassName,
                      "flex min-h-[40px] w-full cursor-default items-center gap-2 px-3 py-2 text-left",
                    )}
                  >
                    <Users className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-foreground">
                      {option.name}
                    </span>
                    <span className="shrink-0 text-[11.5px] text-muted-foreground">
                      {t("thread.composer.team.revision", { revision: option.revision })}
                    </span>
                    {value?.teamId === option.id ? (
                      <Check className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                    ) : null}
                    {canPickAgent ? (
                      <ChevronRight
                        aria-hidden="true"
                        className={cn(
                          "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200",
                          expanded === option.id && "rotate-90",
                        )}
                      />
                    ) : null}
                  </button>
                  {canPickAgent && expanded === option.id ? (
                    <AgentPickerList
                      agents={teamAgents(agents, option)}
                      value={agentValue}
                      onPick={pickAgent}
                      className="border-l border-muted-foreground/20 pl-2"
                    />
                  ) : null}
                </div>
              ))}
              <button
                type="button"
                onClick={() => {
                  onChange(null, null);
                  if (!canPickAgent) {
                    setOpen(false);
                    return;
                  }
                  setExpanded(NO_TEAM_SCOPE);
                }}
                aria-expanded={canPickAgent ? expanded === NO_TEAM_SCOPE : undefined}
                className={cn(
                  floatingItemClassName,
                  floatingItemFocusClassName,
                  "flex min-h-[40px] w-full cursor-default items-center gap-2 px-3 py-2 text-left",
                  "text-[13px] text-muted-foreground",
                )}
              >
                {t("thread.composer.team.none")}
              </button>
              {canPickAgent && expanded === NO_TEAM_SCOPE ? (
                <AgentPickerList
                  agents={agents}
                  value={agentValue}
                  onPick={pickAgent}
                  className="border-l border-muted-foreground/20 pl-2"
                />
              ) : null}
            </div>
          )}
        </PopoverContent>
      </Popover>
    </div>
  );
}
