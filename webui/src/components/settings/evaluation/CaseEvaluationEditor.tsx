import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

export type MilestoneDraft = {
  id: string;
  weight: number;
  assertion_ids: string[];
};

export type EvaluationBudgetsDraft = {
  max_input_tokens: number | null;
  max_output_tokens: number | null;
  max_total_tokens: number | null;
  max_duration_ms: number | null;
  max_tool_calls: number | null;
  max_iterations: number | null;
};

/** 用例级 v2 判据草稿：与后端 CaseEvaluation 字段一一对应。 */
export type CaseEvaluationDraft = {
  version: 2;
  required_dimensions: string[];
  required_evidence: string[];
  milestones: MilestoneDraft[];
  reference_answer: string | null;
  judge_artifact_paths: string[];
  budgets: EvaluationBudgetsDraft;
  /** 硬约束（必选断言 id）：保存时映射为 assertions.required=true。 */
  hard_constraints: string[];
};

export type CaseEvaluationEditorProps = {
  caseId: string;
  caseName: string;
  evaluation: CaseEvaluationDraft | null;
  assertions?: Array<{ id: string; kind: string; required: boolean }>;
  rubric?: { required: boolean; dimensions: Array<{ id: string; name: string; pass_score: number }> } | null;
  environment?: { tool_mode?: string | null; model_mode?: string | null } | null;
  baseRevision?: number;
  /** 保存回调；未接入时保存按钮保持禁用。 */
  onSave?: (draft: CaseEvaluationDraft) => void | Promise<void>;
  onCancel?: () => void;
  className?: string;
};

const DIMENSIONS = ["task", "constraints", "trajectory", "efficiency", "safety"];
const EVIDENCE = ["final_answer", "turns", "artifacts", "trace", "usage", "delegation"];
const BUDGET_FIELDS: Array<{ key: keyof EvaluationBudgetsDraft; label: string }> = [
  { key: "max_input_tokens", label: "budgetMaxInputTokens" },
  { key: "max_output_tokens", label: "budgetMaxOutputTokens" },
  { key: "max_total_tokens", label: "budgetMaxTotalTokens" },
  { key: "max_duration_ms", label: "budgetMaxDurationMs" },
  { key: "max_tool_calls", label: "budgetMaxToolCalls" },
  { key: "max_iterations", label: "budgetMaxIterations" },
];

function emptyDraft(): CaseEvaluationDraft {
  return {
    version: 2,
    required_dimensions: [],
    required_evidence: [],
    milestones: [],
    reference_answer: null,
    judge_artifact_paths: [],
    budgets: {
      max_input_tokens: null,
      max_output_tokens: null,
      max_total_tokens: null,
      max_duration_ms: null,
      max_tool_calls: null,
      max_iterations: null,
    },
    hard_constraints: [],
  };
}

function toDraft(source: CaseEvaluationDraft | null): CaseEvaluationDraft {
  if (!source) return emptyDraft();
  return {
    ...emptyDraft(),
    ...source,
    budgets: { ...emptyDraft().budgets, ...(source.budgets ?? {}) },
    milestones: (source.milestones ?? []).map((item) => ({
      id: item.id,
      weight: item.weight,
      assertion_ids: [...(item.assertion_ids ?? [])],
    })),
    required_dimensions: [...(source.required_dimensions ?? [])],
    required_evidence: [...(source.required_evidence ?? [])],
    hard_constraints: [...(source.hard_constraints ?? [])],
  };
}

export function CaseEvaluationEditor({
  caseId,
  caseName,
  evaluation,
  assertions = [],
  rubric = null,
  environment = null,
  baseRevision = 0,
  onSave,
  onCancel,
  className,
}: CaseEvaluationEditorProps) {
  const { t } = useTranslation();
  const base = "settings.evaluation.reliability.caseEditor.";
  const [draft, setDraft] = useState<CaseEvaluationDraft>(() => toDraft(evaluation));
  const [savedRevision, setSavedRevision] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDraft(toDraft(evaluation));
    setSavedRevision(null);
    setError(null);
  }, [evaluation]);

  const knownAssertions = useMemo(
    () => assertions.map((item) => item.id),
    [assertions],
  );

  const toggle = (list: string[], value: string): string[] =>
    list.includes(value) ? list.filter((item) => item !== value) : [...list, value];

  const updateMilestone = (index: number, patch: Partial<MilestoneDraft>) => {
    setDraft((prev) => ({
      ...prev,
      milestones: prev.milestones.map((item, position) =>
        position === index ? { ...item, ...patch } : item,
      ),
    }));
  };

  const validate = (value: CaseEvaluationDraft): string | null => {
    for (const milestone of value.milestones) {
      if (!milestone.id.trim()) return t(base + "invalidMilestone");
      if (!(milestone.weight > 0) || milestone.weight > 1) return t(base + "invalidMilestone");
      if (milestone.assertion_ids.length === 0) return t(base + "invalidMilestone");
      if (milestone.assertion_ids.some((id) => !knownAssertions.includes(id))) {
        return t(base + "unknownAssertion");
      }
    }
    return null;
  };

  const save = async () => {
    const problem = validate(draft);
    if (problem) {
      setError(problem);
      return;
    }
    if (!onSave) {
      setError(t(base + "saveUnavailable"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSave(draft);
      setSavedRevision(baseRevision + 1);
    } catch {
      setError(t(base + "saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-label={t(base + "title")}
      className={cn("space-y-3 rounded-floating border border-border/60 bg-settings-surface p-3", className)}
    >
      <header className="space-y-0.5">
        <h3 className="text-[13px] font-medium text-foreground">{t(base + "title")}</h3>
        <p className="text-[12px] text-muted-foreground">
          {caseName + " · " + caseId}
        </p>
      </header>

      <section aria-label={t(base + "taskTitle")} className="space-y-2">
        <h4 className="text-[13px] font-medium text-foreground">{t(base + "taskTitle")}</h4>
        <label className="block space-y-1">
          <span className="text-[12px] text-muted-foreground">{t(base + "referenceAnswer")}</span>
          <textarea
            className="w-full rounded border border-border bg-background px-2 py-1 text-[12px] text-foreground"
            rows={3}
            value={draft.reference_answer ?? ""}
            onChange={(event) =>
              setDraft((prev) => ({
                ...prev,
                reference_answer: event.target.value.length > 0 ? event.target.value : null,
              }))
            }
          />
        </label>

        <fieldset className="space-y-1">
          <legend className="text-[12px] text-muted-foreground">
            {t(base + "requiredDimensions")}
          </legend>
          <div className="flex flex-wrap gap-2">
            {DIMENSIONS.map((dimension) => (
              <label key={dimension} className="flex items-center gap-1 text-[12px] text-foreground">
                <input
                  type="checkbox"
                  checked={draft.required_dimensions.includes(dimension)}
                  onChange={() =>
                    setDraft((prev) => ({
                      ...prev,
                      required_dimensions: toggle(prev.required_dimensions, dimension),
                    }))
                  }
                />
                {dimension}
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="space-y-1">
          <legend className="text-[12px] text-muted-foreground">
            {t(base + "requiredEvidence")}
          </legend>
          <div className="flex flex-wrap gap-2">
            {EVIDENCE.map((item) => (
              <label key={item} className="flex items-center gap-1 text-[12px] text-foreground">
                <input
                  type="checkbox"
                  checked={draft.required_evidence.includes(item)}
                  onChange={() =>
                    setDraft((prev) => ({
                      ...prev,
                      required_evidence: toggle(prev.required_evidence, item),
                    }))
                  }
                />
                {item}
              </label>
            ))}
          </div>
        </fieldset>
      </section>

      <section aria-label={t(base + "environmentTitle")} className="space-y-1">
        <h4 className="text-[13px] font-medium text-foreground">
          {t(base + "environmentTitle")}
        </h4>
        <p className="text-[12px] text-muted-foreground">
          {t(base + "toolMode") + ": " + (environment?.tool_mode || t(base + "unknown"))}
          {" · "}
          {t(base + "modelMode") + ": " + (environment?.model_mode || t(base + "unknown"))}
        </p>
      </section>

      <section aria-label={t(base + "milestonesTitle")} className="space-y-2">
        <h4 className="text-[13px] font-medium text-foreground">
          {t(base + "milestonesTitle")}
        </h4>
        {draft.milestones.length === 0 ? (
          <p data-testid="no-milestones" className="text-[12px] text-muted-foreground">
            {t(base + "emptyList")}
          </p>
        ) : null}
        {draft.milestones.map((milestone, index) => (
          <div
            key={milestone.id + ":" + index}
            className="flex flex-wrap items-center gap-2 text-[12px]"
          >
            <label className="flex items-center gap-1">
              <span className="text-muted-foreground">{t(base + "milestoneId")}</span>
              <input
                className="rounded border border-border bg-background px-1 py-0.5 text-[12px] text-foreground"
                value={milestone.id}
                onChange={(event) => updateMilestone(index, { id: event.target.value })}
              />
            </label>
            <label className="flex items-center gap-1">
              <span className="text-muted-foreground">{t(base + "milestoneWeight")}</span>
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="1"
                className="w-20 rounded border border-border bg-background px-1 py-0.5 text-[12px] text-foreground"
                value={milestone.weight}
                onChange={(event) =>
                  updateMilestone(index, { weight: Number(event.target.value) })
                }
              />
            </label>
            <label className="flex items-center gap-1">
              <span className="text-muted-foreground">{t(base + "milestoneAssertions")}</span>
              <input
                className="w-40 rounded border border-border bg-background px-1 py-0.5 text-[12px] text-foreground"
                value={milestone.assertion_ids.join(",")}
                onChange={(event) =>
                  updateMilestone(index, {
                    assertion_ids: event.target.value
                      .split(",")
                      .map((item) => item.trim())
                      .filter((item) => item.length > 0),
                  })
                }
              />
            </label>
            <button
              type="button"
              className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground"
              onClick={() =>
                setDraft((prev) => ({
                  ...prev,
                  milestones: prev.milestones.filter((_, position) => position !== index),
                }))
              }
            >
              {t(base + "removeMilestone")}
            </button>
          </div>
        ))}
        <button
          type="button"
          className="rounded border border-border px-2 py-0.5 text-[11px] text-foreground"
          onClick={() =>
            setDraft((prev) => ({
              ...prev,
              milestones: [
                ...prev.milestones,
                { id: "m" + (prev.milestones.length + 1), weight: 1, assertion_ids: [] },
              ],
            }))
          }
        >
          {t(base + "addMilestone")}
        </button>
      </section>

      <section aria-label={t(base + "constraintsTitle")} className="space-y-1">
        <h4 className="text-[13px] font-medium text-foreground">
          {t(base + "constraintsTitle")}
        </h4>
        {assertions.length === 0 ? (
          <p data-testid="no-assertions" className="text-[12px] text-muted-foreground">
            {t(base + "emptyList")}
          </p>
        ) : (
          <ul className="space-y-1">
            {assertions.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-2 text-[12px]">
                <label className="flex items-center gap-1 text-foreground">
                  <input
                    type="checkbox"
                    checked={draft.hard_constraints.includes(item.id)}
                    onChange={() =>
                      setDraft((prev) => ({
                        ...prev,
                        hard_constraints: toggle(prev.hard_constraints, item.id),
                      }))
                    }
                  />
                  {item.id}
                </label>
                <span className="text-muted-foreground">{item.kind}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label={t(base + "rubricTitle")} className="space-y-1">
        <h4 className="text-[13px] font-medium text-foreground">{t(base + "rubricTitle")}</h4>
        {rubric ? (
          <ul className="space-y-0.5">
            {rubric.dimensions.map((dimension) => (
              <li key={dimension.id} className="text-[12px] text-foreground">
                {dimension.id + " · " + dimension.name + " · pass@" + dimension.pass_score}
              </li>
            ))}
          </ul>
        ) : (
          <p data-testid="no-rubric" className="text-[12px] text-muted-foreground">
            {t(base + "emptyList")}
          </p>
        )}
      </section>

      <section aria-label={t(base + "budgetTitle")} className="space-y-1">
        <h4 className="text-[13px] font-medium text-foreground">{t(base + "budgetTitle")}</h4>
        <div className="flex flex-wrap gap-2">
          {BUDGET_FIELDS.map((field) => (
            <label key={field.key} className="flex items-center gap-1 text-[12px] text-foreground">
              <span className="text-muted-foreground">{t(base + field.label)}</span>
              <input
                type="number"
                min="0"
                className="w-24 rounded border border-border bg-background px-1 py-0.5 text-[12px] text-foreground"
                value={draft.budgets[field.key] ?? ""}
                onChange={(event) =>
                  setDraft((prev) => ({
                    ...prev,
                    budgets: {
                      ...prev.budgets,
                      [field.key]:
                        event.target.value === "" ? null : Number(event.target.value),
                    },
                  }))
                }
              />
            </label>
          ))}
        </div>
      </section>

      {error ? (
        <p role="alert" data-testid="case-editor-error" className="text-[12px] text-destructive">
          {error}
        </p>
      ) : null}
      {savedRevision != null ? (
        <p data-testid="case-editor-saved" className="text-[12px] text-foreground">
          {t(base + "saved", { revision: savedRevision })}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy}
          className="rounded border border-border px-2 py-1 text-[12px] text-foreground disabled:opacity-50"
          onClick={() => void save()}
        >
          {t(base + "save")}
        </button>
        {onCancel ? (
          <button
            type="button"
            className="rounded border border-border px-2 py-1 text-[12px] text-foreground"
            onClick={onCancel}
          >
            {t(base + "cancel")}
          </button>
        ) : null}
        <p className="text-[11px] text-muted-foreground">{t(base + "importExportHint")}</p>
      </div>
    </section>
  );
}
