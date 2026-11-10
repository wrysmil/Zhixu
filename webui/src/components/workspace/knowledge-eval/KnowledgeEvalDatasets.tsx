import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState } from "@/components/workspace/EmptyState";
import type { KnowledgeEvalCase, KnowledgeEvalDataset } from "@/lib/knowledge-eval-api";
import { cn } from "@/lib/utils";

interface KnowledgeEvalDatasetsProps {
  datasets: KnowledgeEvalDataset[];
  busy: boolean;
  onCreate: (input: {
    name: string;
    notes?: string;
    cases?: Array<Record<string, unknown>>;
  }) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onUpdateCases: (dataset: KnowledgeEvalDataset, caseId: string, expectAny: string[]) => Promise<void>;
}

interface DraftCase {
  query: string;
  expectAny: string;
}

function toCasePayload(draft: DraftCase): Record<string, unknown> {
  const expectAny = draft.expectAny
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
  return {
    query: draft.query.trim(),
    expect_any: expectAny,
  };
}

/**
 * 数据集分区：查询用例 + `expect_any` 编辑 + 标注来源徽标。
 *
 * `expect_any` 非空的用例走关键词标注（expect），其余走 LLM 判定（judge）——
 * 标注来源由服务端推导，这里只负责录入。
 */
export function KnowledgeEvalDatasets({
  datasets,
  busy,
  onCreate,
  onDelete,
  onUpdateCases,
}: KnowledgeEvalDatasetsProps) {
  const { t } = useTranslation();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [drafts, setDrafts] = useState<DraftCase[]>([{ query: "", expectAny: "" }]);

  const submit = async () => {
    const usable = drafts.filter((draft) => draft.query.trim());
    if (!name.trim() || usable.length === 0) return;
    await onCreate({
      name: name.trim(),
      notes: notes.trim(),
      cases: usable.map(toCasePayload),
    });
    setName("");
    setNotes("");
    setDrafts([{ query: "", expectAny: "" }]);
    setCreating(false);
  };

  return (
    <section data-testid="knowledge-eval-datasets" className="mt-4 rounded-panel border border-border/60 bg-background p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[13px] font-semibold text-foreground">
          {t("knowledge.eval.tab.datasets")}
        </h2>
        <Button
          type="button"
          size="sm"
          onClick={() => setCreating((open) => !open)}
          className="h-8 gap-1.5 rounded-full px-3 text-[12px] font-semibold"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          {t("knowledge.eval.action.createDataset")}
        </Button>
      </div>

      {creating ? (
        <form
          className="mt-3 flex flex-col gap-2 rounded-control border border-border/60 bg-muted/20 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label className="flex flex-col gap-1 text-[11px] font-medium text-muted-foreground">
            {t("knowledge.eval.dataset.name")}
            <Input
              aria-label={t("knowledge.eval.dataset.name")}
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="h-8 text-[12.5px]"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] font-medium text-muted-foreground">
            {t("knowledge.eval.dataset.notes")}
            <Textarea
              aria-label={t("knowledge.eval.dataset.notes")}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              rows={2}
              className="text-[12.5px]"
            />
          </label>

          {drafts.map((draft, index) => (
            <div key={index} className="flex flex-col gap-1 sm:flex-row">
              <Input
                aria-label={t("knowledge.eval.dataset.query")}
                value={draft.query}
                placeholder={t("knowledge.eval.dataset.queryPlaceholder")}
                onChange={(event) =>
                  setDrafts((current) =>
                    current.map((item, itemIndex) =>
                      itemIndex === index ? { ...item, query: event.target.value } : item,
                    ),
                  )
                }
                className="h-8 flex-1 text-[12.5px]"
              />
              <Input
                aria-label={t("knowledge.eval.dataset.expect")}
                value={draft.expectAny}
                placeholder={t("knowledge.eval.dataset.expectPlaceholder")}
                onChange={(event) =>
                  setDrafts((current) =>
                    current.map((item, itemIndex) =>
                      itemIndex === index ? { ...item, expectAny: event.target.value } : item,
                    ),
                  )
                }
                className="h-8 flex-1 text-[12.5px]"
              />
            </div>
          ))}

          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setDrafts((current) => [...current, { query: "", expectAny: "" }])}
              className="h-8 gap-1.5 rounded-full px-3 text-[11.5px]"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden />
              {t("knowledge.eval.action.addCase")}
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={busy || !name.trim()}
              className="h-8 rounded-full px-3.5 text-[12px] font-semibold"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
              {t("knowledge.eval.action.createDataset")}
            </Button>
          </div>
        </form>
      ) : null}

      {datasets.length === 0 ? (
        <div className="mt-3">
          <EmptyState
            title={t("knowledge.eval.empty.datasets")}
            description={t("knowledge.eval.dataset.hint")}
          />
        </div>
      ) : (
        <ul className="mt-3 flex flex-col gap-3">
          {datasets.map((dataset) => (
            <li
              key={dataset.id}
              className="rounded-control border border-border/60 bg-background p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-semibold text-foreground">
                    {dataset.name}
                  </p>
                  <p className="mt-0.5 text-[10.5px] text-muted-foreground">
                    {t("knowledge.eval.dataset.caseCount", { count: dataset.cases.length })}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={t("knowledge.eval.action.deleteDataset")}
                  title={t("knowledge.eval.action.deleteDataset")}
                  disabled={busy}
                  onClick={() => void onDelete(dataset.id)}
                  className="h-7 shrink-0 rounded-full px-2 text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden />
                </Button>
              </div>

              <ul className="mt-2 flex flex-col gap-1.5">
                {dataset.cases.map((item) => (
                  <CaseRow
                    key={item.id}
                    item={item}
                    busy={busy}
                    onSave={(expectAny) => void onUpdateCases(dataset, item.id, expectAny)}
                  />
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CaseRow({
  item,
  busy,
  onSave,
}: {
  item: KnowledgeEvalCase;
  busy: boolean;
  onSave: (expectAny: string[]) => void;
}) {
  const { t } = useTranslation();
  const [expectAny, setExpectAny] = useState(item.expectAny.join(", "));
  const dirty = expectAny !== item.expectAny.join(", ");

  return (
    <li className="flex flex-wrap items-center gap-2 rounded-control bg-muted/25 px-2.5 py-1.5">
      <span className="min-w-[160px] flex-1 truncate text-[12px] text-foreground">
        {item.query}
      </span>
      <span
        className={cn(
          "rounded-full px-1.5 py-0.5 text-[10px]",
          item.labelSource === "expect"
            ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
            : "bg-violet-100 text-violet-900 dark:bg-violet-950/40 dark:text-violet-200",
        )}
      >
        {item.labelSource === "expect"
          ? t("knowledge.eval.badge.labelSource.expect")
          : t("knowledge.eval.badge.labelSource.judge")}
      </span>
      <Input
        aria-label={t("knowledge.eval.dataset.expect")}
        value={expectAny}
        placeholder={t("knowledge.eval.dataset.expectPlaceholder")}
        onChange={(event) => setExpectAny(event.target.value)}
        className="h-7 w-[180px] text-[11.5px]"
      />
      {dirty ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() =>
            onSave(
              expectAny
                .split(/[,，]/)
                .map((value) => value.trim())
                .filter(Boolean),
            )
          }
          className="h-7 rounded-full px-2.5 text-[11px]"
        >
          {t("knowledge.eval.action.save")}
        </Button>
      ) : null}
    </li>
  );
}
