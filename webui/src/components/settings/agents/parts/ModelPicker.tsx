import { useId, useState, type KeyboardEvent } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Check, ChevronDown, CircleAlert, CircleX, Cpu } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Badge } from "@/components/settings/agents/shared";
import type { ModelDescriptor, ModelHealth } from "@/lib/agents/types";
import { MODEL_HEALTH_LABEL_KEY } from "@/lib/agents/i18n";
import { cn } from "@/lib/utils";

const HEALTH_ORDER: ModelHealth[] = ["healthy", "degraded", "unavailable"];

const HEALTH_META: Record<
  ModelHealth,
  { tone: "ok" | "warn" | "muted"; icon: typeof Check }
> = {
  healthy: { tone: "ok", icon: Check },
  degraded: { tone: "warn", icon: CircleAlert },
  unavailable: { tone: "muted", icon: CircleX },
};

function healthLabel(health: ModelHealth, t: TFunction) {
  return t(MODEL_HEALTH_LABEL_KEY[health], { defaultValue: health });
}

function formatContext(tokens: number): string {
  return tokens >= 1000 ? `${Math.round(tokens / 1000)}K` : String(tokens);
}

export function ModelPicker({
  models,
  value,
  onChange,
  globalModelLabel,
}: {
  models: ModelDescriptor[];
  value: string | null;
  onChange: (modelId: string | null) => void;
  globalModelLabel: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const selected = models.find((model) => model.id === value) ?? null;
  const missing = value !== null && !selected;
  const listId = useId();

  const moveOptionFocus = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const options = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button[role="option"]:not(:disabled)')];
    if (!options.length) return;
    event.preventDefault();
    const current = options.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
      : (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options[next]?.focus();
  };

  const grouped = HEALTH_ORDER.map((health) => ({
    health,
    models: models.filter((model) => model.health === health),
  })).filter((group) => group.models.length > 0);

  return (
    <div className="space-y-2">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-haspopup="listbox"
            aria-label={t("settings.agents.model.label", "模型")}
            onKeyDown={(event) => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setOpen(true); } }}
            className="flex min-h-11 w-full items-center gap-2 rounded-control border border-border bg-background px-3 py-2 text-left text-[13px] transition-colors duration-150 ease-out hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none"
          >
            <Cpu className="h-4 w-4 shrink-0 text-accent-foreground" aria-hidden />
            <span className="min-w-0 flex-1 break-words text-foreground/90">
              {selected
                ? selected.label
                : missing
                ? `${t("settings.agents.workspace.modelMissing", "Saved model is currently unavailable")} · ${value}`
                : `${t("settings.agents.model.auto", "自动（跟随全局）")} · ${globalModelLabel}`}
            </span>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="max-h-[350px] w-[--radix-popover-trigger-width] overflow-y-auto p-1">
          <div id={listId} role="listbox" aria-label={t("settings.agents.model.label", "模型")} onKeyDown={moveOptionFocus}>
          <button
            type="button"
            role="option"
            aria-selected={value === null}
            onClick={() => {
              onChange(null);
              setOpen(false);
            }}
            className={cn(
              "flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-[13px] hover:bg-muted/60",
              value === null && "bg-muted/60",
            )}
          >
            <Cpu className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
            <span className="min-w-0 flex-1 truncate">
              {t("settings.agents.model.auto", "自动（跟随全局）")}
            </span>
            <span className="max-w-[40%] truncate text-[11px] text-muted-foreground">{globalModelLabel}</span>
          </button>

          {grouped.map((group) => {
            const meta = HEALTH_META[group.health];
            const Icon = meta.icon;
            return (
              <div key={group.health} className="mt-1 border-t border-border/45 pt-1">
                <p className="px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {healthLabel(group.health, t)}
                </p>
                {group.models.map((model) => {
                  const disabled = model.health === "unavailable";
                  return (
                    <button
                      key={model.id}
                      type="button"
                      role="option"
                      aria-selected={value === model.id}
                      disabled={disabled}
                      title={model.statusNote}
                      onClick={() => {
                        onChange(model.id);
                        setOpen(false);
                      }}
                      className={cn(
                        "flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-[13px]",
                        disabled ? "cursor-not-allowed opacity-45" : "hover:bg-muted/60",
                        value === model.id && !disabled && "bg-muted/60",
                      )}
                    >
                      <Icon className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                      <span className="min-w-0 flex-1 truncate">{model.label}</span>
                      {model.statusNote ? (
                        <span className="max-w-[40%] truncate text-[11px] text-muted-foreground">
                          {model.statusNote}
                        </span>
                      ) : null}
                      {value === model.id ? <Check className="h-3 w-3 shrink-0" aria-hidden /> : null}
                    </button>
                  );
                })}
              </div>
            );
          })}
          </div>
        </PopoverContent>
      </Popover>

      {selected ? (
        <div className="flex flex-wrap items-center gap-1.5 text-[12px] text-muted-foreground">
          <Badge tone={HEALTH_META[selected.health].tone}>
            {healthLabel(selected.health, t)}
          </Badge>
          <span>{formatContext(selected.contextWindow)} {t("settings.agents.model.context", "上下文")}</span>
          <span aria-hidden>·</span>
          <span>{selected.provider}</span>
          {selected.vision ? <span>{t("settings.agents.model.vision", "支持视觉")}</span> : null}
          {selected.toolUse ? <span>{t("settings.agents.model.toolUse", "支持工具调用")}</span> : null}
        </div>
      ) : missing ? (
        <div className="flex items-start gap-2 rounded-control bg-amber-500/10 px-3 py-2 text-[12px] leading-5 text-amber-900 dark:text-amber-200">
          <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <p>{t("settings.agents.workspace.modelMissingHint", "The saved model is preserved. Choose another model or follow the global setting.")}</p>
        </div>
      ) : (
        <p className="text-[12px] text-muted-foreground">
          {t("settings.agents.model.autoHint", "该 Agent 跟随全局模型设置。")}
        </p>
      )}
    </div>
  );
}
