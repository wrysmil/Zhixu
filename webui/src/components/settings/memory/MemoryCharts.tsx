import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { MemoryPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useMemoryCopy } from "./MemoryPresentation";

const TREND_DAYS = 14;
const EXPIRY_SOON_DAYS = 7;

const SOURCE_LABELS: Record<string, { zh: string; en: string }> = {
  extraction: { zh: "AI 抽取", en: "Extracted" },
  manual: { zh: "手动添加", en: "Manual" },
  consolidation: { zh: "整理合并", en: "Consolidated" },
  core_note: { zh: "核心笔记", en: "Core note" },
};

/** 分类色板：深浅主题下都能区分，不与主色混淆。 */
const PALETTE = [
  "bg-sky-500/70",
  "bg-emerald-500/70",
  "bg-violet-500/70",
  "bg-amber-500/70",
  "bg-rose-500/70",
  "bg-cyan-500/70",
  "bg-orange-500/70",
  "bg-teal-500/70",
];

function paletteClass(index: number): string {
  return PALETTE[index % PALETTE.length];
}

function dayKey(value: string): string | null {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/**
 * 记忆概览：四张图把「库里有什么」摊开成可读的形状。
 * 数据全部来自页面已经加载的列表，不额外打后端。
 */
export function MemoryCharts({ items }: { items: MemoryPayload[] }) {
  const { text } = useMemoryCopy();
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const trend = useMemo(() => {
    const buckets = new Map<string, number>();
    const days: string[] = [];
    const today = new Date();
    for (let offset = TREND_DAYS - 1; offset >= 0; offset -= 1) {
      const date = new Date(today.getFullYear(), today.getMonth(), today.getDate() - offset);
      const key = dayKey(date.toISOString());
      if (!key) continue;
      buckets.set(key, 0);
      days.push(key);
    }
    for (const item of items) {
      const key = dayKey(item.created_at);
      if (key && buckets.has(key)) buckets.set(key, (buckets.get(key) ?? 0) + 1);
    }
    return days.map((key) => ({ key, count: buckets.get(key) ?? 0 }));
  }, [items]);

  const byType = useMemo(() => {
    const counters = new Map<string, number>();
    for (const item of items) counters.set(item.type, (counters.get(item.type) ?? 0) + 1);
    return [...counters.entries()].sort((a, b) => b[1] - a[1]);
  }, [items]);

  const bySource = useMemo(() => {
    const counters = new Map<string, number>();
    for (const item of items) {
      const source = item.source || "unknown";
      counters.set(source, (counters.get(source) ?? 0) + 1);
    }
    return [...counters.entries()].sort((a, b) => b[1] - a[1]);
  }, [items]);

  const expiry = useMemo(() => {
    const now = Date.now();
    const soon = now + EXPIRY_SOON_DAYS * 24 * 3600 * 1000;
    let permanent = 0;
    let expiring = 0;
    let expired = 0;
    for (const item of items) {
      if (!item.expires_at) {
        permanent += 1;
        continue;
      }
      const at = new Date(item.expires_at).getTime();
      if (Number.isNaN(at)) {
        permanent += 1;
      } else if (at < now) {
        expired += 1;
      } else if (at <= soon) {
        expiring += 1;
      } else {
        permanent += 1;
      }
    }
    return { permanent, expiring, expired };
  }, [items]);

  if (items.length === 0) return null;

  const trendMax = Math.max(1, ...trend.map((day) => day.count));
  const typeMax = Math.max(1, ...byType.map(([, count]) => count));
  const sourceMax = Math.max(1, ...bySource.map(([, count]) => count));

  return (
    <section
      data-testid="memory-charts"
      className="rounded-panel border border-border/60 bg-settings-surface px-3 py-2"
    >
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="text-[12px] font-medium text-foreground">{text("记忆概览", "Memory overview")}</h2>
        <span className="text-[11px] text-muted-foreground">
          {text(`共 ${items.length} 条`, `${items.length} in total`)}
        </span>
        <button
          type="button"
          className="ml-auto rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40"
          aria-expanded={open}
          onClick={() => setOpen((current) => !current)}
        >
          {open ? text("收起图表", "Hide charts") : text("展开图表", "Show charts")}
        </button>
      </header>

      {open ? (
        <div className="mt-2.5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Chart title={text(`近 ${TREND_DAYS} 天新增`, `New in the last ${TREND_DAYS} days`)}>
            <div className="flex h-16 items-end gap-1" role="img" aria-label={text("每日新增记忆条数", "Daily new memory count")}>
              {trend.map((day) => (
                <div key={day.key} className="group flex flex-1 flex-col items-center gap-0.5">
                  <div className="flex h-full w-full items-end">
                    <div
                      className={cn(
                        "w-full rounded-t transition-opacity",
                        day.count === 0
                          ? "bg-muted/70"
                          : day.count === trendMax
                            ? "bg-emerald-500/80"
                            : day.count >= trendMax / 2
                              ? "bg-sky-500/75"
                              : "bg-sky-500/45",
                      )}
                      style={{ height: `${Math.max(2, (day.count / trendMax) * 100)}%` }}
                      title={`${day.key} · ${day.count}`}
                    />
                  </div>
                  <span className="text-[9px] tabular-nums text-muted-foreground">{day.count || ""}</span>
                </div>
              ))}
            </div>
            <div className="mt-0.5 flex justify-between text-[10px] text-muted-foreground">
              <span>{trend[0]?.key.slice(5) ?? ""}</span>
              <span>{trend[trend.length - 1]?.key.slice(5) ?? ""}</span>
            </div>
          </Chart>

          <Chart title={text("类型分布", "By type")}>
            <BarList
              rows={byType.map(([key, count], index) => ({
                key,
                label: t(`settings.memory.type.${key}`, { defaultValue: key }),
                count,
                max: typeMax,
                color: paletteClass(index),
              }))}
            />
          </Chart>

          <Chart title={text("来源分布", "By source")}>
            <BarList
              rows={bySource.map(([key, count], index) => ({
                key,
                label: SOURCE_LABELS[key] ? text(SOURCE_LABELS[key].zh, SOURCE_LABELS[key].en) : key,
                count,
                max: sourceMax,
                color: paletteClass(index),
              }))}
            />
          </Chart>

          <Chart title={text("有效期", "Retention")}>
            <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted/50" role="img" aria-label={text("记忆有效期分布", "Memory expiry distribution")}>
              {[
                { key: "permanent", count: expiry.permanent, className: "bg-sky-500/75" },
                { key: "expiring", count: expiry.expiring, className: "bg-amber-500/80" },
                { key: "expired", count: expiry.expired, className: "bg-rose-500/80" },
              ].map((segment) =>
                segment.count > 0 ? (
                  <div
                    key={segment.key}
                    className={segment.className}
                    style={{ width: `${(segment.count / items.length) * 100}%` }}
                    title={`${segment.key} · ${segment.count}`}
                  />
                ) : null,
              )}
            </div>
            <ul className="mt-1.5 space-y-0.5 text-[11px] text-muted-foreground">
              <li className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-sky-500/75" aria-hidden />
                {text(`长期保留 ${expiry.permanent} 条`, `${expiry.permanent} kept long term`)}
              </li>
              <li className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-amber-500/80" aria-hidden />
                {text(`${EXPIRY_SOON_DAYS} 天内到期 ${expiry.expiring} 条`, `${expiry.expiring} expiring within ${EXPIRY_SOON_DAYS} days`)}
              </li>
              <li className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-rose-500/80" aria-hidden />
                {text(`已过期 ${expiry.expired} 条`, `${expiry.expired} expired`)}
              </li>
            </ul>
          </Chart>
        </div>
      ) : null}
    </section>
  );
}

function Chart({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 space-y-1">
      <h3 className="text-[11px] font-medium text-muted-foreground">{title}</h3>
      {children}
    </div>
  );
}

function BarList({
  rows,
}: {
  rows: Array<{ key: string; label: string; count: number; max: number; color: string }>;
}) {
  if (rows.length === 0) {
    return <p className="text-[11px] text-muted-foreground">—</p>;
  }
  return (
    <ul className="space-y-1">
      {rows.map((row) => (
        <li key={row.key} className="flex items-center gap-2 text-[11px]">
          <span className="w-20 shrink-0 truncate text-muted-foreground" title={row.label}>
            {row.label}
          </span>
          <span className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-muted/50">
            <span
              className={cn("block h-full rounded-full", row.color)}
              style={{ width: `${Math.max(3, (row.count / row.max) * 100)}%` }}
            />
          </span>
          <span className="w-6 shrink-0 text-right tabular-nums text-muted-foreground">{row.count}</span>
        </li>
      ))}
    </ul>
  );
}
