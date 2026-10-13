import { Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ActivityStep } from "@/components/thread/activity/ActivityStep";
import { WebActivityRow } from "@/components/thread/activity/WebActivityRow";
import { safeActivityDetail } from "@/components/thread/activity/activity-text";
import {
  presentWebSearchAction,
  type WebSearchRunModel,
} from "@/components/thread/activity/web-search-model";

export function WebSearchRun({ run, turnActive }: { run: WebSearchRunModel; turnActive: boolean }) {
  const { t } = useTranslation();
  const active = run.status === "running" && turnActive;
  const status = run.status === "running" && !turnActive ? "done" : run.status;
  const label = presentWebSearchAction(run.query, status, run.target);
  const failed = status === "error";

  return (
    <>
      <ActivityStep
        icon={failed ? undefined : Search}
        active={active}
        tone={failed ? "error" : status === "done" ? "success" : "active"}
        label={label}
        detail={run.query ? (
          <div className="flex flex-col gap-1">
            <div>{`${run.target === "x" ? "X" : "Web"} · ${safeActivityDetail(run.query, 240)}`}</div>
            {run.sources.length > 0 ? (
              <div>
                {t("thread.activity.sourceCount", {
                  count: run.sources.length,
                  defaultValue: "{{count}} sources",
                })}
              </div>
            ) : null}
            {failed && run.error ? (
              <div className="text-destructive/85">{safeActivityDetail(run.error, 240)}</div>
            ) : null}
          </div>
        ) : null}
      />
      {run.sources.map((source) => (
        <WebActivityRow
          key={source.href}
          title={source.title}
          href={source.href}
          host={source.host}
          displayUrl={source.displayUrl}
        />
      ))}
    </>
  );
}
