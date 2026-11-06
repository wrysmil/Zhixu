import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { listExperiences, type ExperiencePayload } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import { memorySelectClass } from "./MemoryPresentation";

const OUTCOMES = ["failed", "partial", "completed"] as const;
const KINDS = ["experience", "skill", "pitfall", "decision", "reference"] as const;
const STATUSES = ["active", "deprecated", "superseded"] as const;

function outcomeClass(outcome: string): string {
  if (outcome === "failed") return "bg-destructive/10 text-destructive";
  if (outcome === "partial") return "bg-amber-500/10 text-amber-700 dark:text-amber-400";
  return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400";
}

function kindClass(kind: string): string {
  if (kind === "pitfall") return "bg-rose-500/10 text-rose-700 dark:text-rose-300";
  if (kind === "skill") return "bg-violet-500/10 text-violet-700 dark:text-violet-300";
  if (kind === "decision") return "bg-amber-500/10 text-amber-700 dark:text-amber-300";
  if (kind === "reference") return "bg-slate-500/10 text-slate-700 dark:text-slate-300";
  return "bg-sky-500/10 text-sky-700 dark:text-sky-300";
}

function textField(metadata: Record<string, unknown>, key: string): string {
  const value = metadata[key];
  return typeof value === "string" ? value : "";
}

function listField(metadata: Record<string, unknown>, key: string): string[] {
  const value = metadata[key];
  return Array.isArray(value) ? value.map(String) : [];
}

function ExperienceDetails({ item }: { item: ExperiencePayload }) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const metadata = item.metadata ?? {};
  const kind = item.kind ?? "experience";

  if (kind === "skill") {
    const capability = textField(metadata, "capability");
    const steps = listField(metadata, "steps");
    return (
      <>
        {capability ? (
          <p className="mt-1.5 text-[12px] leading-5 text-foreground">
            <span className="text-muted-foreground">{tx("settings.memory.capability", "能力")}：</span>
            {capability}
          </p>
        ) : null}
        {steps.length > 0 ? (
          <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-[12px] leading-5 text-foreground">
            {steps.map((step) => <li key={step}>{step}</li>)}
          </ol>
        ) : null}
      </>
    );
  }

  if (kind === "pitfall") {
    const symptom = textField(metadata, "symptom");
    const fix = textField(metadata, "fix");
    return (
      <>
        {symptom ? (
          <p className="mt-1.5 text-[12px] leading-5 text-foreground">
            <span className="text-muted-foreground">{tx("settings.memory.symptom", "症状")}：</span>
            {symptom}
          </p>
        ) : null}
        {fix ? (
          <p className="mt-0.5 text-[12px] leading-5 text-foreground">
            <span className="text-muted-foreground">{tx("settings.memory.fix", "修复")}：</span>
            {fix}
          </p>
        ) : null}
      </>
    );
  }

  if (kind === "decision") {
    const choice = textField(metadata, "choice");
    const rationale = textField(metadata, "rationale");
    const options = Array.isArray(metadata.options)
      ? (metadata.options as Array<Record<string, unknown>>)
      : [];
    return (
      <>
        {choice ? (
          <p className="mt-1.5 text-[12px] leading-5 text-foreground">
            <span className="text-muted-foreground">{tx("settings.memory.choice", "结论")}：</span>
            {choice}
          </p>
        ) : null}
        {rationale ? (
          <p className="mt-0.5 text-[12px] leading-5 text-foreground">
            <span className="text-muted-foreground">{tx("settings.memory.rationale", "理由")}：</span>
            {rationale}
          </p>
        ) : null}
        {options.length > 0 ? (
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-[12px] leading-5 text-muted-foreground">
            {options.map((option, index) => (
              <li key={`${String(option.name)}-${index}`}>
                {String(option.name ?? "")}
                {option.pros ? ` · +${String(option.pros)}` : ""}
                {option.cons ? ` · -${String(option.cons)}` : ""}
              </li>
            ))}
          </ul>
        ) : null}
      </>
    );
  }

  if (kind === "reference") {
    const pointer = textField(metadata, "pointer");
    const whatFor = textField(metadata, "what_for");
    return (
      <>
        {whatFor ? (
          <p className="mt-1.5 text-[12px] leading-5 text-foreground">
            <span className="text-muted-foreground">{tx("settings.memory.whatFor", "用途")}：</span>
            {whatFor}
          </p>
        ) : null}
        {pointer ? (
          <p className="mt-0.5 break-all text-[12px] leading-5 text-foreground">
            <span className="text-muted-foreground">{tx("settings.memory.pointer", "指针")}：</span>
            {pointer}
          </p>
        ) : null}
      </>
    );
  }

  // experience 的 summary 已包含“根因/解法”，不再重复渲染详情。
  return null;
}

export function ExperienceListView() {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { token } = useClient();
  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [items, setItems] = useState<ExperiencePayload[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listExperiences(token, {
        q: query.trim() || undefined,
        outcome: outcome || undefined,
        kind: kind || undefined,
        status: status || undefined,
        limit: 100,
      });
      setItems(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [kind, outcome, query, status, token]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), query ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [load, query]);

  return (
    <div className="flex flex-col gap-3" data-testid="experience-list-view">
      <div className="flex flex-wrap items-center gap-2">
        <input
          aria-label={tx("settings.memory.experienceSearch", "Search experiences")}
          className="h-8 min-w-48 flex-1 rounded-md border border-border/70 bg-background px-2.5 text-[12.5px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
          placeholder={tx("settings.memory.experienceSearchPlaceholder", "搜索现象、根因或解法")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <select
          aria-label={tx("settings.memory.experienceKind", "Type filter")}
          className={memorySelectClass}
          value={kind}
          onChange={(event) => setKind(event.target.value)}
        >
          <option value="">{tx("settings.memory.kindAll", "全部类型")}</option>
          {KINDS.map((value) => (
            <option key={value} value={value}>
              {tx(`settings.memory.kind.${value}`, value)}
            </option>
          ))}
        </select>
        <select
          aria-label={tx("settings.memory.experienceStatus", "Status filter")}
          className={memorySelectClass}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="">{tx("settings.memory.statusAll", "全部状态")}</option>
          {STATUSES.map((value) => (
            <option key={value} value={value}>
              {tx(`settings.memory.entryStatus.${value}`, value)}
            </option>
          ))}
        </select>
        <select
          aria-label={tx("settings.memory.experienceOutcome", "Outcome filter")}
          className="h-8 rounded-md border border-border/70 bg-background px-2 text-[12.5px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={outcome}
          onChange={(event) => setOutcome(event.target.value)}
        >
          <option value="">{tx("settings.memory.outcomeAll", "全部结果")}</option>
          {OUTCOMES.map((value) => (
            <option key={value} value={value}>
              {tx(`settings.memory.outcome.${value}`, value)}
            </option>
          ))}
        </select>
        <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>
          <RefreshCw className="mr-1.5 h-4 w-4" aria-hidden />
          {tx("settings.memory.refresh", "Refresh")}
        </Button>
      </div>

      {error ? (
        <p role="alert" className="text-[12px] text-destructive">
          {error}
        </p>
      ) : null}

      {loading ? (
        <div className="grid min-h-32 place-items-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-label={tx("common.loading", "Loading")} />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border/70 px-4 py-8 text-center text-[12.5px] text-muted-foreground">
          {tx("settings.memory.experienceEmpty", "还没有经验记录。任务失败或修复后，可以让知序沉淀一条。")}
        </div>
      ) : (
        <ul className="divide-y divide-border/55" aria-label={tx("settings.memory.experienceList", "Experience list")}>
          {items.map((item) => {
            const entryKind = item.kind ?? "experience";
            const entryStatus = item.status ?? "active";
            return (
              <li key={item.id} className="py-3">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${kindClass(entryKind)}`}>
                    {tx(`settings.memory.kind.${entryKind}`, entryKind)}
                  </span>
                  {entryStatus !== "active" ? (
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                      {tx(`settings.memory.entryStatus.${entryStatus}`, entryStatus)}
                    </span>
                  ) : null}
                  <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${outcomeClass(item.outcome)}`}>
                    {tx(`settings.memory.outcome.${item.outcome}`, item.outcome)}
                  </span>
                  <span className="text-[13px] font-medium text-foreground">
                    {item.title || item.summary.split("\n")[0]}
                  </span>
                  <span className="text-[11px] text-muted-foreground">{item.ended_at.slice(0, 10)}</span>
                  {item.version ? (
                    <span className="text-[11px] text-muted-foreground">v{item.version}</span>
                  ) : null}
                </div>
                {entryKind === "experience" ? (
                  <p className="mt-1.5 line-clamp-4 whitespace-pre-wrap break-words text-[12.5px] leading-5 text-muted-foreground [overflow-wrap:anywhere]">
                    {item.summary}
                  </p>
                ) : null}
                <ExperienceDetails item={item} />
                {item.workspace_id && item.workspace_id !== "default" ? (
                  <p className="mt-1 text-[11px] text-muted-foreground">{item.workspace_id}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
