import { useState } from "react";
import { Check, ChevronDown, Users } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  floatingItemClassName,
  floatingItemFocusClassName,
} from "@/components/ui/floating-surface";
import type { NewChatTeam, TeamBinding } from "@/lib/types";
import type { TeamOption } from "@/lib/teams/types";
import { cn } from "@/lib/utils";

export type { TeamOption } from "@/lib/teams/types";

export interface TeamSelectorProps {
  options: TeamOption[];
  /** Local choice for a chat that has no messages yet. */
  value: NewChatTeam | null;
  valueName: string | null;
  /** Server-confirmed binding; the picker becomes a read-only chip when set. */
  bound: TeamBinding | null;
  onChange: (team: NewChatTeam | null, teamName: string | null) => void;
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
  isHero = false,
  disabled = false,
}: TeamSelectorProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const selectedName = valueName ?? value?.teamId ?? null;

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
                <button
                  key={option.id}
                  type="button"
                  onClick={() => {
                    onChange({ teamId: option.id, teamRevision: option.revision }, option.name);
                    setOpen(false);
                  }}
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
                </button>
              ))}
              <button
                type="button"
                onClick={() => {
                  onChange(null, null);
                  setOpen(false);
                }}
                className={cn(
                  floatingItemClassName,
                  floatingItemFocusClassName,
                  "flex min-h-[40px] w-full cursor-default items-center gap-2 px-3 py-2 text-left",
                  "text-[13px] text-muted-foreground",
                )}
              >
                {t("thread.composer.team.none")}
              </button>
            </div>
          )}
        </PopoverContent>
      </Popover>
    </div>
  );
}
