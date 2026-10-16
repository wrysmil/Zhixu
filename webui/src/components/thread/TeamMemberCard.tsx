import { useTranslation } from "react-i18next";
import { CircleStop } from "lucide-react";

import type { TeamTaskSnapshot, TaskStatus } from "@/lib/teams/types";
import { isTerminalTaskStatus } from "@/lib/teams/events";
import { cn } from "@/lib/utils";

const STATUS_TONE: Record<TaskStatus, string> = {
  pending: "text-muted-foreground",
  queued: "text-muted-foreground",
  running: "text-primary",
  cancelling: "text-amber-600 dark:text-amber-400",
  succeeded: "text-emerald-600 dark:text-emerald-400",
  failed: "text-destructive",
  cancelled: "text-muted-foreground",
  blocked: "text-muted-foreground",
  interrupted: "text-amber-600 dark:text-amber-400",
};

export interface TeamMemberCardProps {
  task: TeamTaskSnapshot;
  /** Display name of the frozen member this task was dispatched to. */
  memberName: string | null;
  onCancel?: (taskId: string) => void;
}

/**
 * One dispatched task, labelled by the member that owns it.
 *
 * Only what the server actually reported is shown: the last public action and the
 * result summary. No timers, no invented progress percentages — contract §7.
 */
export function TeamMemberCard({ task, memberName, onCancel }: TeamMemberCardProps) {
  const { t } = useTranslation();
  const cancelable = !isTerminalTaskStatus(task.status) && !!onCancel;

  return (
    <li
      data-team-task={task.taskId}
      className="rounded-lg border border-border/70 bg-card/40 px-3 py-2"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[12.5px] font-semibold text-foreground">{task.title}</p>
          <p className="truncate text-[11.5px] text-muted-foreground">
            {memberName ?? t("thread.teamProgress.memberFallback", { memberId: task.memberId })}
          </p>
        </div>
        <span
          data-team-task-status={task.status}
          className={cn(
            "shrink-0 text-[11.5px] font-medium",
            STATUS_TONE[task.status],
          )}
        >
          {t(`thread.teamProgress.taskStatus.${task.status}`)}
        </span>
      </div>

      {task.lastAction ? (
        <p data-team-task-action className="mt-1 text-[12px] text-muted-foreground">
          {task.lastAction}
        </p>
      ) : null}

      {task.result?.summary ? (
        <p data-team-task-summary className="mt-1 whitespace-pre-wrap text-[12px] text-foreground/85">
          {task.result.summary}
        </p>
      ) : null}

      {task.result?.artifacts.length ? (
        <p className="mt-1 truncate text-[11.5px] text-muted-foreground">
          {t("thread.teamProgress.artifacts", { names: task.result.artifacts.join("、") })}
        </p>
      ) : null}

      {cancelable ? (
        <button
          type="button"
          onClick={() => onCancel(task.taskId)}
          aria-label={t("thread.teamProgress.cancelTask")}
          className="mt-1.5 inline-flex items-center gap-1 text-[11.5px] text-muted-foreground transition-colors hover:text-destructive"
        >
          <CircleStop className="h-3 w-3" aria-hidden="true" />
          {t("thread.teamProgress.cancelTask")}
        </button>
      ) : null}
    </li>
  );
}
