import { Check, Copy, Loader2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import { formatClock, formatDuration, recordName } from "@/features/trajectory/projection";
import type {
  ContentRef,
  TrajectoryBodyPage,
  TrajectoryRecord,
} from "@/features/trajectory/types";

type InspectorTab = "content" | "request" | "schema" | "json";

interface TrajectoryInspectorProps {
  record: TrajectoryRecord;
  onClose: () => void;
  bodies: Record<string, TrajectoryBodyPage>;
  bodyLoading: Set<string>;
  loadBody: (bodyId: string, offset?: number) => Promise<void>;
  overlay: boolean;
}

export function TrajectoryInspector({
  record,
  onClose,
  bodies,
  bodyLoading,
  loadBody,
  overlay,
}: TrajectoryInspectorProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<InspectorTab>("content");
  const [copied, setCopied] = useState(false);

  const refs = useMemo(() => {
    if (tab === "request") return record.requestRef ? [record.requestRef] : [];
    if (tab === "schema") return record.schemaRef ? [record.schemaRef] : [];
    if (tab === "content") return record.blockSummaries.map((block) => block.content);
    return [];
  }, [record, tab]);

  useEffect(() => {
    const first = refs.find((ref) => ref.bodyId);
    if (first?.bodyId && !bodies[first.bodyId] && !bodyLoading.has(first.bodyId)) {
      void loadBody(first.bodyId);
    }
  }, [refs, bodies, bodyLoading, loadBody]);

  const jsonText = useMemo(() => JSON.stringify(record, null, 2), [record]);
  const copyValue = () => {
    const text = tab === "json"
      ? jsonText
      : refs
          .map((ref) => (ref.bodyId && bodies[ref.bodyId]?.text) || ref.preview)
          .filter(Boolean)
          .join("\n\n");
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    }).catch(() => undefined);
  };

  const extra = record.extra ?? {};
  const provider = typeof extra.provider === "string" ? extra.provider : null;
  const model = typeof extra.model === "string" ? extra.model : null;

  return (
    <aside
      role="region"
      aria-label={t("thread.trajectory.inspector.title")}
      data-testid="trajectory-inspector"
      className={cn(
        "z-30 flex min-h-0 flex-col border-border/60 bg-settings-surface",
        overlay
          ? "absolute inset-0 border-t"
          : "relative w-full shrink-0 border-l",
      )}
    >
      <div className="flex items-start gap-2 border-b border-border/60 px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] font-semibold">
            {t(`thread.trajectory.kind.${record.kind}`)}
            {record.kind === "tool" || record.kind === "subtool" ? ` · ${recordName(record)}` : ""}
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
            <span>{t(`thread.trajectory.status.${record.status}`)}</span>
            <span>{formatDuration(record.durationMs)}</span>
            <span>{formatClock(record.startedAtMs)}</span>
            {record.step != null ? <span>step {record.step}</span> : null}
          </div>
        </div>
        <button
          type="button"
          onClick={copyValue}
          aria-label={t("thread.trajectory.inspector.copy")}
          className="rounded p-1 text-muted-foreground hover:bg-accent/40 hover:text-foreground"
        >
          {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("thread.trajectory.inspector.close")}
          className="rounded p-1 text-muted-foreground hover:bg-accent/40 hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>

      <dl className="grid grid-cols-[5.5rem_1fr] gap-x-2 gap-y-1 border-b border-border/60 px-3 py-2 text-[11px]">
        {provider ? (
          <>
            <dt className="text-muted-foreground">{t("thread.trajectory.inspector.provider")}</dt>
            <dd className="truncate">{provider}</dd>
          </>
        ) : null}
        {model ? (
          <>
            <dt className="text-muted-foreground">{t("thread.trajectory.inspector.model")}</dt>
            <dd className="truncate">{model}</dd>
          </>
        ) : null}
        <dt className="text-muted-foreground">{t("thread.trajectory.inspector.recordId")}</dt>
        <dd className="truncate font-mono text-[10px]">{record.recordId}</dd>
        {record.turnId ? (
          <>
            <dt className="text-muted-foreground">{t("thread.trajectory.inspector.turn")}</dt>
            <dd className="truncate font-mono text-[10px]">{record.turnId}</dd>
          </>
        ) : null}
      </dl>

      <div role="tablist" aria-label={t("thread.trajectory.inspector.title")} className="flex shrink-0 gap-1 border-b border-border/60 px-2 py-1">
        {(["content", "request", "schema", "json"] as InspectorTab[]).map((item) => {
          const disabled = (item === "request" && !record.requestRef)
            || (item === "schema" && !record.schemaRef)
            || (item === "content" && record.blockSummaries.length === 0);
          return (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={tab === item}
              disabled={disabled}
              onClick={() => setTab(item)}
              className={cn(
                "rounded-full px-2.5 py-1 text-[11px]",
                tab === item
                  ? "bg-primary/10 text-foreground"
                  : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
                disabled && "cursor-not-allowed opacity-40 hover:bg-transparent",
              )}
            >
              {t(`thread.trajectory.inspector.tabs.${item}`)}
            </button>
          );
        })}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2 text-[12px]">
        {tab === "json" ? (
          <pre className="whitespace-pre-wrap break-words font-mono text-[10.5px] leading-relaxed text-foreground/90">
            {jsonText}
          </pre>
        ) : (
          <div className="space-y-2">
            {refs.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">
                {t("thread.trajectory.inspector.noContent")}
              </p>
            ) : null}
            {refs.map((ref, index) => (
              <ContentBlock
                key={`${ref.bodyId ?? "preview"}-${index}`}
                refValue={ref}
                body={ref.bodyId ? bodies[ref.bodyId] : undefined}
                loading={ref.bodyId ? bodyLoading.has(ref.bodyId) : false}
                onLoadMore={ref.bodyId
                  ? () => {
                      const current = bodies[ref.bodyId!];
                      if (current?.hasMore) void loadBody(ref.bodyId!, current.nextOffsetBytes);
                    }
                  : undefined}
              />
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

function ContentBlock({
  refValue,
  body,
  loading,
  onLoadMore,
}: {
  refValue: ContentRef;
  body: TrajectoryBodyPage | undefined;
  loading: boolean;
  onLoadMore?: () => void;
}) {
  const { t } = useTranslation();
  const text = body?.text ?? refValue.preview;
  return (
    <div className="rounded-md border border-border/50 bg-background/50">
      <div className="flex items-center gap-2 border-b border-border/40 px-2 py-1 text-[10px] text-muted-foreground">
        <span className="font-mono">{refValue.contentType}</span>
        {refValue.redacted ? (
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-700 dark:text-amber-300">
            {t("thread.trajectory.inspector.redacted")}
          </span>
        ) : null}
        {refValue.truncated || body?.truncated ? (
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-700 dark:text-amber-300">
            {t("thread.trajectory.inspector.truncated")}
          </span>
        ) : null}
        {loading ? <Loader2 className="ml-auto h-3 w-3 animate-spin" aria-hidden /> : null}
      </div>
      <div className="max-h-[45vh] overflow-y-auto px-2 py-1.5">
        {refValue.bodyId ? (
          <pre className="whitespace-pre-wrap break-words font-sans text-[11.5px] leading-relaxed text-foreground/90">
            {text || (loading ? "" : t("thread.trajectory.inspector.noContent"))}
          </pre>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            {refValue.unavailableReason
              ? t(`thread.trajectory.unavailable.${refValue.unavailableReason}`)
              : t("thread.trajectory.inspector.noContent")}
          </p>
        )}
        {body?.hasMore && onLoadMore ? (
          <button
            type="button"
            onClick={onLoadMore}
            className="mt-1 text-[11px] text-primary hover:underline"
          >
            {t("thread.trajectory.inspector.loadMore")}
          </button>
        ) : null}
      </div>
    </div>
  );
}
