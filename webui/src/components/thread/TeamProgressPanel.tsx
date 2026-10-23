import { useTranslation } from "react-i18next";
import { RefreshCw, Square } from "lucide-react";

import { TeamMemberCard } from "@/components/thread/TeamMemberCard";
import { isTerminalRunStatus } from "@/lib/teams/events";
import type { TeamRunState } from "@/lib/teams/reducer";
import { cn } from "@/lib/utils";

const RUN_TONE: Record<TeamRunState["status"], string> = {
  running: "text-primary",
  cancelling: "text-amber-600 dark:text-amber-400",
  completed: "text-emerald-600 dark:text-emerald-400",
  partial: "text-amber-600 dark:text-amber-400",
  failed: "text-destructive",
  cancelled: "text-muted-foreground",
  interrupted: "text-amber-600 dark:text-amber-400",
};

export interface TeamProgressPanelProps {
  run: TeamRunState | null;
  memberNames: Record<string, string>;
  /** Whether this surface may send control mutations (an authenticated WS session). */
  canControl?: boolean;
  onCancelRun?: (runId: string) => void;
  onCancelTask?: (runId: string, taskId: string) => void;
  onRetryRun?: (runId: string) => void;
}

/**
 * Live view of one team run: the leader's current action and every member's task.
 *
 * Stop and retry reflect the server's own state machine — a cancelling run keeps
 * saying "stopping" until the server says otherwise, and retry only appears once the
 * run is terminal with failed or interrupted tasks (contract §5/§6).
 */
export function TeamProgressPanel({
  run,
  memberNames,
  canControl = false,
  onCancelRun,
  onCancelTask,
  onRetryRun,
}: TeamProgressPanelProps) {
  const { t } = useTranslation();
  if (!run) return null;

  const stopping = run.status === "cancelling";
  const retryable = isTerminalRunStatus(run.status)
    && run.taskOrder.some((taskId) => {
      const task = run.tasks[taskId];
      return !!task && (task.status === "failed" || task.status === "interrupted");
    });
  const tasks = run.taskOrder
    .map((taskId) => run.tasks[taskId])
    .filter((task): task is NonNullable<typeof task> => !!task);

  return (
    <section
      data-team-panel={run.runId}
      aria-label={t("thread.teamProgress.title")}
      className="mx-auto w-full max-w-3xl rounded-xl border border-border/70 bg-muted/25 px-3 py-2.5"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-semibold text-foreground">
            {run.teamName || t("thread.teamProgress.title")}
          </p>
          <p className="text-[11.5px] text-muted-foreground">
            {t("thread.teamProgress.leader")} ·{" "}
            {t(`thread.teamProgress.leaderAction.${run.leaderAction}`)}
          </p>
        </div>
        <span
          data-team-run-status={run.status}
          className={cn("text-[11.5px] font-medium", RUN_TONE[run.status])}
        >
          {t(`thread.teamProgress.status.${run.status}`)}
        </span>
      </header>

      {run.needsReplay ? (
        <p data-team-resync className="mt-1.5 text-[11.5px] text-muted-foreground">
          {t("thread.teamProgress.resyncing")}
        </p>
      ) : null}

      {tasks.length ? (
        <ul className="mt-2 flex flex-col gap-1.5">
          {tasks.map((task) => (
            <TeamMemberCard
              key={task.taskId}
              task={task}
              memberName={memberNames[task.memberId] ?? null}
              onCancel={
                canControl && onCancelTask
                  ? (taskId) => onCancelTask(run.runId, taskId)
                  : undefined
              }
            />
          ))}
        </ul>
      ) : isTerminalRunStatus(run.status) ? (
        // 只有收口后仍无任务才敢说「这回合没派活」：运行中的空窗期不算。
        <p data-team-no-tasks className="mt-2 text-[12px] text-muted-foreground">
          {t("thread.teamProgress.noTasks")}
        </p>
      ) : null}

      {canControl ? (
        <footer className="mt-2 flex flex-wrap items-center gap-2">
          {!isTerminalRunStatus(run.status) ? (
            <button
              type="button"
              onClick={() => onCancelRun?.(run.runId)}
              disabled={stopping}
              className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11.5px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-60"
            >
              <Square className="h-3 w-3" aria-hidden="true" />
              {stopping ? t("thread.teamProgress.stopping") : t("thread.teamProgress.stop")}
            </button>
          ) : null}

          {retryable ? (
            <span className="inline-flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => onRetryRun?.(run.runId)}
                title={t("thread.teamProgress.retryHint")}
                className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11.5px] text-muted-foreground transition-colors hover:text-foreground"
              >
                <RefreshCw className="h-3 w-3" aria-hidden="true" />
                {t("thread.teamProgress.retry")}
              </button>
              <span className="text-[11px] text-muted-foreground">
                {t("thread.teamProgress.retryHint")}
              </span>
            </span>
          ) : null}
        </footer>
      ) : null}
    </section>
  );
}
