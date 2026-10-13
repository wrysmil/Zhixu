import {
  FileSearch,
  FolderOpen,
  ListTree,
  MemoryStick,
  Play,
  type LucideIcon,
} from "lucide-react";
import { useMemo } from "react";

import { ActivityStep } from "@/components/thread/activity/ActivityStep";
import { safeActivityDetail } from "@/components/thread/activity/activity-text";
import {
  describeGenericToolRun,
  type GenericToolRunItem,
  type GenericToolStatus,
  type ToolFamily,
} from "@/components/thread/activity/generic-tool-model";

interface GenericToolRunModel {
  status: GenericToolStatus;
  label: string;
  detail: string;
  aside: string;
  icon: LucideIcon;
}

export function GenericToolRun({ items }: { items: GenericToolRunItem[] }) {
  const model = useMemo(() => buildModel(items), [items]);
  const action = [model.label, model.detail].filter(Boolean).join(" ");
  const label = model.aside ? `${action} · ${model.aside}` : action;
  const failed = model.status === "error";

  return (
    <ActivityStep
      icon={failed ? undefined : model.icon}
      active={model.status === "running"}
      tone={failed ? "error" : model.status === "done" ? "success" : "active"}
      label={label}
      detail={items.length ? <GenericToolDetail items={items} /> : null}
    />
  );
}

function GenericToolDetail({ items }: { items: GenericToolRunItem[] }) {
  return (
    <div className="flex flex-col gap-1.5">
      {items.map((item, index) => (
        <div
          key={`${item.trace.groupKey}:${index}`}
          className={index > 0 ? "border-t border-border/40 pt-1.5" : undefined}
        >
          <span className="font-medium text-foreground/75">{item.trace.name}</span>
          {item.trace.fields.length > 0 ? (
            <span>
              {`(${item.trace.fields
                .map((field) => `${field.key}: ${safeActivityDetail(field.value, 240)}`)
                .join(", ")})`}
            </span>
          ) : null}
          {item.status === "error" && item.error ? (
            <span className="text-destructive/85"> · {safeActivityDetail(item.error, 240)}</span>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function buildModel(items: GenericToolRunItem[]): GenericToolRunModel {
  const family = items[0]?.trace.family ?? "generic";
  const presentation = describeGenericToolRun(items);
  return {
    ...presentation,
    icon: activityIcon(family),
  };
}

function activityIcon(family: ToolFamily): LucideIcon {
  if (family === "content-search" || family === "file-search") return FileSearch;
  if (family === "list") return ListTree;
  if (family === "read") return FolderOpen;
  if (family === "memory") return MemoryStick;
  return Play;
}
