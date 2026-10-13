import { type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

import { SegmentedControl } from "@/components/ui/segmented-control";
import { AGENT_COLOR_PRESETS, type SelectionMode } from "@/lib/agents/types";
import { cn } from "@/lib/utils";

export function SectionLabel({
  index,
  children,
  action,
}: {
  index?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      {index ? (
        <span aria-hidden className="text-[12px] text-muted-foreground/60">
          {index}
        </span>
      ) : null}
      <h3 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground/90">
        {children}
      </h3>
      {action ? <div className="ml-auto">{action}</div> : null}
    </div>
  );
}

export function FieldLabel({
  htmlFor,
  children,
  hint,
}: {
  htmlFor?: string;
  children: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="mb-1.5 flex items-baseline justify-between gap-3">
      <label htmlFor={htmlFor} className="text-[12px] font-medium text-foreground/80">
        {children}
      </label>
      {hint ? <span className="text-[11px] text-muted-foreground">{hint}</span> : null}
    </div>
  );
}

export function ModeSelector({
  value,
  onChange,
  ariaLabel,
}: {
  value: SelectionMode;
  onChange: (mode: SelectionMode) => void;
  ariaLabel: string;
}) {
  const { t } = useTranslation();
  return (
    <SegmentedControl<SelectionMode>
      value={value}
      onChange={onChange}
      ariaLabel={ariaLabel}
      options={[
        { value: "all", label: t("settings.agents.mode.all", "全部") },
        { value: "include", label: t("settings.agents.mode.include", "仅所选") },
        { value: "exclude", label: t("settings.agents.mode.exclude", "排除所选") },
      ]}
    />
  );
}

export function ColorSwatch({
  value,
  onChange,
}: {
  value: string;
  onChange: (color: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {AGENT_COLOR_PRESETS.map((color) => {
        const active = color.toLowerCase() === value.toLowerCase();
        return (
          <button
            key={color}
            type="button"
            aria-label={color}
            aria-pressed={active}
            onClick={() => onChange(color)}
            className={cn(
              "h-6 w-6 rounded-full transition-transform",
              active ? "ring-2 ring-foreground/70 ring-offset-2 ring-offset-background" : "hover:scale-110",
            )}
            style={{ backgroundColor: color }}
          />
        );
      })}
      <label className="flex h-6 items-center gap-1.5 rounded-full border border-border px-2">
        <span
          aria-hidden
          className="h-3.5 w-3.5 rounded-full border border-border/60"
          style={{ backgroundColor: value }}
        />
        <span className="sr-only">{t("settings.agents.color.custom", "自定义颜色")}</span>
        <input
          type="text"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          spellCheck={false}
          aria-label={t("settings.agents.color.hex", "十六进制颜色")}
          className="w-[4.5rem] bg-transparent text-[11px] uppercase text-foreground/80 outline-none"
        />
      </label>
    </div>
  );
}

export type BadgeTone = "neutral" | "info" | "warn" | "ok" | "muted";

const BADGE_CLASS: Record<BadgeTone, string> = {
  neutral: "border-border bg-muted/60 text-foreground/75",
  info: "border-primary/25 bg-accent text-accent-foreground dark:border-primary/30 dark:bg-primary/10 dark:text-primary",
  warn: "border-amber-300/70 bg-amber-50 text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-200",
  ok: "border-emerald-300/70 bg-emerald-50 text-emerald-900 dark:border-emerald-700/40 dark:bg-emerald-950/30 dark:text-emerald-200",
  muted: "border-border/70 bg-transparent text-muted-foreground",
};

export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        BADGE_CLASS[tone],
      )}
    >
      {children}
    </span>
  );
}

export function StatusBlock({
  status,
  error,
}: {
  status: "loading" | "empty" | "ready";
  error: string | null;
}) {
  const { t } = useTranslation();
  if (error) {
    return (
      <div
        role="alert"
        className="rounded-panel border border-rose-300/70 bg-rose-50/70 px-4 py-3 text-[13px] text-rose-900 dark:border-rose-700/40 dark:bg-rose-950/30 dark:text-rose-200"
      >
        {error}
      </div>
    );
  }
  if (status === "loading") {
    return (
      <div className="flex h-32 items-center justify-center text-[13px] text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
        {t("settings.agents.loading", "加载中…")}
      </div>
    );
  }
  if (status === "empty") {
    return (
      <div
        role="status"
        className="flex h-32 items-center justify-center text-[13px] text-muted-foreground"
      >
        {t("settings.agents.empty", "暂无 Agent")}
      </div>
    );
  }
  return null;
}
