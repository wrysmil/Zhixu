import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, RotateCcw, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  fetchKnowledgeSettings,
  updateKnowledgeSettings,
  type KnowledgeRerankSettings,
  type KnowledgeRetrievalSettings,
  type KnowledgeRunSettings,
  type WebUIMutationTransport,
} from "@/lib/api";

const LIMIT_OPTIONS = [4, 6, 8, 12, 16, 20];

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 border-b border-border/50 py-3 last:border-b-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="text-sm font-medium text-foreground">{label}</div>
        {hint ? <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div> : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">{children}</div>
    </div>
  );
}

function RangeField({
  value,
  min,
  max,
  step,
  onChange,
  label,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  label: string;
}) {
  return (
    <>
      <input
        type="range"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-1.5 w-40 cursor-pointer appearance-none rounded-full bg-muted"
        style={{ accentColor: "hsl(var(--primary))" }}
      />
      <span className="w-12 text-right text-sm tabular-nums">{value.toFixed(2)}</span>
    </>
  );
}

export function KnowledgeSettingsPanel({
  token,
  transport,
}: {
  token: string;
  transport: WebUIMutationTransport;
}) {
  const { t } = useTranslation();
  const [saved, setSaved] = useState<KnowledgeRunSettings | null>(null);
  const [draft, setDraft] = useState<KnowledgeRunSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchKnowledgeSettings(token);
      setSaved(payload.settings);
      setDraft(payload.settings);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = useMemo(() => {
    if (!saved || !draft) return false;
    return JSON.stringify(saved.retrieval) !== JSON.stringify(draft.retrieval)
      || JSON.stringify(saved.rerank) !== JSON.stringify(draft.rerank);
  }, [saved, draft]);

  const patchRetrieval = (patch: Partial<KnowledgeRetrievalSettings>) => {
    setNotice(null);
    setDraft((current) =>
      current ? { ...current, retrieval: { ...current.retrieval, ...patch } } : current,
    );
  };
  const patchRerank = (patch: Partial<KnowledgeRerankSettings>) => {
    setNotice(null);
    setDraft((current) =>
      current ? { ...current, rerank: { ...current.rerank, ...patch } } : current,
    );
  };

  const save = async () => {
    if (!draft || !saved || saving) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await updateKnowledgeSettings(transport, {
        retrieval: draft.retrieval,
        rerank: draft.rerank,
      });
      setSaved(result.settings);
      setDraft(result.settings);
      setNotice(
        result.applied
          ? t("knowledge.settings.savedLive", { defaultValue: "已保存，立即生效" })
          : t("knowledge.settings.savedNext", { defaultValue: "已保存，下次检索生效" }),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="rounded-panel border border-border/60 bg-background p-4 text-sm text-muted-foreground">
        {t("knowledge.settings.loading", { defaultValue: "正在读取检索参数…" })}
      </div>
    );
  }
  if (!draft) {
    return (
      <div role="alert" className="rounded-panel border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
        {error ?? t("knowledge.settings.loadFailed", { defaultValue: "读取检索参数失败" })}
      </div>
    );
  }

  const retrieval = draft.retrieval;
  const rerank = draft.rerank;

  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-panel border border-border/60 bg-background p-4">
        <div className="flex flex-wrap items-center gap-2 pb-3">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-primary/10 text-primary">
            <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden />
          </span>
          <h2 className="text-sm font-semibold text-foreground">
            {t("knowledge.settings.title", { defaultValue: "检索设置" })}
          </h2>
          <span className="rounded-full border border-border/70 px-2 py-0.5 text-xs text-muted-foreground">
            {t("knowledge.settings.live", { defaultValue: "即时生效 · 不重建索引" })}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Button type="button" variant="ghost" size="sm" disabled={!dirty || saving} onClick={() => setDraft(saved)}>
              <RotateCcw className="h-3.5 w-3.5" aria-hidden />
              {t("knowledge.settings.reset", { defaultValue: "撤销" })}
            </Button>
            <Button type="button" size="sm" disabled={!dirty || saving} onClick={() => void save()}>
              {saving ? t("knowledge.settings.saving", { defaultValue: "保存中…" }) : t("knowledge.settings.save", { defaultValue: "保存" })}
            </Button>
          </div>
        </div>

        <p className="pb-2 text-xs leading-5 text-muted-foreground">
          {t("knowledge.settings.hybridHint", {
            defaultValue: "按两路名次做加权 RRF 后归一化；双路都命中才有最高分（仅向量路命中上限为语义权重，仅关键词路命中上限为关键词权重）。",
          })}
        </p>

        <Field label={t("knowledge.settings.vectorWeight", { defaultValue: "语义权重" })} hint={t("knowledge.settings.weightHint", { defaultValue: "向量路在融合分里的占比" })}>
          <RangeField label="vector weight" min={0} max={1} step={0.05} value={retrieval.vectorWeight}
            onChange={(value) => patchRetrieval({ vectorWeight: value })} />
        </Field>
        <Field label={t("knowledge.settings.keywordWeight", { defaultValue: "关键词权重" })} hint={t("knowledge.settings.weightHint", { defaultValue: "关键词路在融合分里的占比" })}>
          <RangeField label="keyword weight" min={0} max={1} step={0.05} value={retrieval.keywordWeight}
            onChange={(value) => patchRetrieval({ keywordWeight: value })} />
        </Field>
        <Field label={t("knowledge.settings.rrfK", { defaultValue: "RRF 常数 k" })} hint={t("knowledge.settings.rrfKHint", { defaultValue: "越大名次差异越平缓，默认 60" })}>
          <Input type="number" aria-label="rrf k" min={1} max={1000} value={retrieval.rrfK}
            onChange={(event) => patchRetrieval({ rrfK: Number(event.target.value) })} className="h-8 w-24 text-sm" />
        </Field>

        <Field label={t("knowledge.settings.defaultLimit", { defaultValue: "返回条数" })} hint={t("knowledge.settings.defaultLimitHint", { defaultValue: "进入上下文的命中块数量，默认 8" })}>
          <select
            aria-label="default limit"
            value={retrieval.defaultLimit}
            onChange={(event) => patchRetrieval({ defaultLimit: Number(event.target.value) })}
            className="h-8 rounded-control border border-input bg-background px-2 text-sm"
          >
            {LIMIT_OPTIONS.filter((value) => value <= retrieval.maxLimit).map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        </Field>
        <Field label={t("knowledge.settings.vectorThreshold", { defaultValue: "向量阈值" })} hint={t("knowledge.settings.thresholdHint", { defaultValue: "余弦相似度下限，0 表示不过滤" })}>
          <Input type="number" aria-label="vector threshold" min={0} max={1} step={0.01} value={retrieval.vectorThreshold}
            onChange={(event) => patchRetrieval({ vectorThreshold: Number(event.target.value) })} className="h-8 w-24 text-sm" />
        </Field>
        <Field label={t("knowledge.settings.keywordThreshold", { defaultValue: "关键词阈值" })} hint={t("knowledge.settings.keywordThresholdHint", { defaultValue: "按组内最高分相对计算；BM25 无上界" })}>
          <Input type="number" aria-label="keyword threshold" min={0} max={1} step={0.01} value={retrieval.keywordThreshold}
            onChange={(event) => patchRetrieval({ keywordThreshold: Number(event.target.value) })} className="h-8 w-24 text-sm" />
        </Field>
        <Field label={t("knowledge.settings.mmr", { defaultValue: "MMR 去冗余强度" })} hint={t("knowledge.settings.mmrHint", { defaultValue: "调低更不重复，1.0 等价关闭" })}>
          <RangeField label="mmr lambda" min={0} max={1} step={0.05} value={retrieval.mmrLambda}
            onChange={(value) => patchRetrieval({ mmrLambda: value })} />
        </Field>

        <Field label={t("knowledge.settings.rerank", { defaultValue: "启用精排" })} hint={t("knowledge.settings.rerankHint", { defaultValue: "cross-encoder 重新打分；模型不可用时按召回顺序降级" })}>
          <input
            type="checkbox"
            aria-label="rerank enabled"
            checked={rerank.enabled}
            onChange={(event) => patchRerank({ enabled: event.target.checked })}
            className="h-4 w-4 cursor-pointer"
            style={{ accentColor: "hsl(var(--primary))" }}
          />
        </Field>
        <Field label={t("knowledge.settings.rerankModel", { defaultValue: "精排模型" })}>
          <Input type="text" aria-label="rerank model" value={rerank.model}
            placeholder={t("knowledge.settings.rerankModelPlaceholder", { defaultValue: "例如 BAAI/bge-reranker-v2-m3" })}
            onChange={(event) => patchRerank({ model: event.target.value })} className="h-8 w-64 text-sm" />
        </Field>
        <Field label={t("knowledge.settings.rerankThreshold", { defaultValue: "精排准入阈值" })} hint={t("knowledge.settings.rerankThresholdHint", { defaultValue: "低于阈值先降档，仍全拒时保留最高分 1 条" })}>
          <Input type="number" aria-label="rerank threshold" min={0} max={1} step={0.01} value={rerank.threshold}
            onChange={(event) => patchRerank({ threshold: Number(event.target.value) })} className="h-8 w-24 text-sm" />
        </Field>

        {error ? (
          <div role="alert" className="mt-3 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        ) : null}
        {notice ? (
          <div className="mt-3 flex items-center gap-1.5 rounded-control border border-emerald-300/60 bg-emerald-50 px-3 py-2 text-sm text-emerald-900 dark:border-emerald-800/40 dark:bg-emerald-950/30 dark:text-emerald-100">
            <Check className="h-3.5 w-3.5" aria-hidden />
            {notice}
          </div>
        ) : null}
      </section>

      <section className="rounded-panel border border-border/60 bg-background p-4">
        <h2 className="pb-2 text-sm font-semibold text-foreground">
          {t("knowledge.settings.chunkingTitle", { defaultValue: "分段与入库" })}
        </h2>
        <p className="text-xs leading-5 text-muted-foreground">
          {t("knowledge.settings.chunkingHint", {
            defaultValue: "分段大小与重叠和已入库数据绑定，修改后需要重新入库才会生效，本页暂不提供在线修改。",
          })}
        </p>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-muted-foreground">分段最大长度</dt>
            <dd className="tabular-nums">{draft.chunking.chunkSize}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">分段重叠</dt>
            <dd className="tabular-nums">{draft.chunking.chunkOverlap}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">分词模式</dt>
            <dd>{draft.chunking.tokenizerMode}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">段落切分</dt>
            <dd>{draft.chunking.paragraphMode}</dd>
          </div>
        </dl>
      </section>
    </div>
  );
}
