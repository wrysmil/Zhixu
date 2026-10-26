import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  CaseEvaluationEditor,
  type CaseEvaluationDraft,
} from "@/components/settings/evaluation/CaseEvaluationEditor";
import {
  evaluationErrorText,
  formatDateTime,
} from "@/components/settings/evaluation/evaluation-utils";
import { Button } from "@/components/ui/button";
import {
  evaluationApi,
  type DatasetImportResult,
  type EvaluationCase,
  type EvaluationCaseDetail,
  type Suite,
} from "@/lib/evaluation-api";
import { cn } from "@/lib/utils";

export function EvaluationDatasets() {
  const { t } = useTranslation();
  const [suites, setSuites] = useState<Suite[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<DatasetImportResult | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [expandedSuiteId, setExpandedSuiteId] = useState<string | null>(null);
  const [casesBySuite, setCasesBySuite] = useState<Record<string, EvaluationCase[]>>({});
  const [editingCase, setEditingCase] = useState<EvaluationCaseDetail | null>(null);
  const [editorBusy, setEditorBusy] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadSuites = useCallback(async () => {
    try {
      const page = await evaluationApi.listSuites();
      setSuites(page.data);
      setLoadError(null);
    } catch (error) {
      setLoadError(evaluationErrorText(error, t));
    }
  }, [t]);

  useEffect(() => {
    void loadSuites();
  }, [loadSuites]);

  const viewCases = async (suiteId: string) => {
    if (expandedSuiteId === suiteId) {
      setExpandedSuiteId(null);
      return;
    }
    setExpandedSuiteId(suiteId);
    try {
      const page = await evaluationApi.listCases(suiteId);
      setCasesBySuite((prev) => ({ ...prev, [suiteId]: page.data }));
      setActionError(null);
    } catch (error) {
      setActionError(evaluationErrorText(error, t));
    }
  };

  const importFile = async (file: File) => {
    setImporting(true);
    setImportError(null);
    setImportResult(null);
    try {
      const result = await evaluationApi.importDataset(file);
      setImportResult(result);
      void loadSuites();
    } catch (error) {
      setImportError(evaluationErrorText(error, t));
    } finally {
      setImporting(false);
    }
  };

  const openCaseEditor = async (caseId: string) => {
    setEditorError(null);
    try {
      const detail = await evaluationApi.getCase(caseId);
      setEditingCase(detail);
    } catch (error) {
      setEditorError(evaluationErrorText(error, t));
    }
  };

  const saveCaseCriteria = async (draft: CaseEvaluationDraft) => {
    if (!editingCase) return;
    setEditorBusy(true);
    setEditorError(null);
    try {
      const payload: Record<string, unknown> = { ...editingCase };
      delete payload.id;
      delete payload.revision;
      delete payload.created_at;
      delete payload.updated_at;
      delete payload.is_deleted;
      const hard = new Set(draft.hard_constraints);
      const assertions = (editingCase.assertions ?? []).map((item) => ({
        ...item,
        required: hard.has(item.id) ? true : item.required,
      }));
      await evaluationApi.updateCase(editingCase.id, {
        ...payload,
        assertions,
        evaluation: {
          version: 2,
          required_dimensions: draft.required_dimensions,
          required_evidence: draft.required_evidence,
          milestones: draft.milestones,
          reference_answer: draft.reference_answer,
          judge_artifact_paths: draft.judge_artifact_paths,
          budgets: draft.budgets,
        },
        base_revision: editingCase.revision,
      });
      setEditingCase(null);
      if (expandedSuiteId) {
        const page = await evaluationApi.listCases(expandedSuiteId);
        setCasesBySuite((prev) => ({ ...prev, [expandedSuiteId]: page.data }));
      }
    } catch (error) {
      setEditorError(evaluationErrorText(error, t));
    } finally {
      setEditorBusy(false);
    }
  };

  const runDownload = async (download: () => Promise<void>) => {
    try {
      await download();
      setActionError(null);
    } catch (error) {
      setActionError(evaluationErrorText(error, t));
    }
  };

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void runDownload(() => evaluationApi.downloadDatasetTemplate())}
        >
          {t("settings.evaluation.datasets.template")}
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={importing}
          onClick={() => fileInputRef.current?.click()}
        >
          {importing
            ? t("settings.evaluation.datasets.importing")
            : t("settings.evaluation.datasets.import")}
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".xlsx"
          className="sr-only"
          aria-hidden
          tabIndex={-1}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void importFile(file);
            event.target.value = "";
          }}
        />
      </div>

      {importResult ? (
        <div className="flex flex-wrap items-center gap-2 rounded-floating border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-[13px] text-emerald-700 dark:text-emerald-300">
          <span>{t("settings.evaluation.datasets.imported")}</span>
          <span className="font-medium">{importResult.suite.name}</span>
          <span>· {importResult.case_count}</span>
        </div>
      ) : null}
      {importResult && importResult.errors.length > 0 ? (
        <div className="space-y-1 rounded-floating border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 dark:text-amber-300">
          <p className="font-medium">{t("settings.evaluation.datasets.errors")}</p>
          <ul className="space-y-0.5">
            {importResult.errors.map((rowError, index) => (
              <li key={index}>{JSON.stringify(rowError)}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {importError ? (
        <p role="alert" className="text-[13px] text-destructive">
          {importError}
        </p>
      ) : null}
      {loadError ? (
        <p role="alert" className="text-[13px] text-destructive">
          {loadError}
        </p>
      ) : null}
      {actionError ? (
        <p role="alert" className="text-[13px] text-destructive">
          {actionError}
        </p>
      ) : null}

      {suites.length === 0 && !loadError ? (
        <p className="text-[13px] text-muted-foreground">{t("settings.evaluation.datasets.empty")}</p>
      ) : null}

      <ul className="space-y-1.5">
        {suites.map((suite) => (
          <li
            key={suite.id}
            className="space-y-2 rounded-floating border border-border/60 bg-settings-surface px-3 py-2"
          >
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <span className="font-medium text-foreground">{suite.name}</span>
              <span className="text-muted-foreground">
                {t("settings.evaluation.datasets.cases")}: {suite.case_count}
              </span>
              <span className="ml-auto flex flex-wrap items-center gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => void viewCases(suite.id)}
                >
                  {t("settings.evaluation.datasets.viewCases")}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => void runDownload(() => evaluationApi.exportDatasetXlsx(suite.id))}
                >
                  {t("settings.evaluation.datasets.export")}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    void runDownload(() => evaluationApi.downloadSummaryReport(suite.id))
                  }
                >
                  {t("settings.evaluation.datasets.summary")}
                </Button>
              </span>
            </div>
            {expandedSuiteId === suite.id && casesBySuite[suite.id] ? (
              <ol className="space-y-1">
                {casesBySuite[suite.id].map((item) => (
                  <li
                    key={item.id}
                    className="flex flex-wrap items-center gap-2 text-[13px] text-foreground"
                  >
                    <span className={cn(!item.enabled && "text-muted-foreground")}>{item.name}</span>
                    <span className="text-[11px] text-muted-foreground">
                      {formatDateTime(item.updated_at)}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => void openCaseEditor(item.id)}
                    >
                      {t("settings.evaluation.reliability.caseEditor.editCriteria")}
                    </Button>
                  </li>
                ))}
              </ol>
            ) : null}
          </li>
        ))}
      </ul>

      {editorError ? (
        <p role="alert" className="text-[13px] text-destructive">
          {editorError}
        </p>
      ) : null}

      {editingCase ? (
        <div className="rounded-panel border border-border/60 bg-settings-surface p-3 sm:p-4">
          <CaseEvaluationEditor
            caseId={editingCase.id}
            caseName={editingCase.name}
            baseRevision={editingCase.revision}
            evaluation={
              editingCase.evaluation
                ? {
                    version: 2,
                    required_dimensions: editingCase.evaluation.required_dimensions ?? [],
                    required_evidence: editingCase.evaluation.required_evidence ?? [],
                    milestones: editingCase.evaluation.milestones ?? [],
                    reference_answer: editingCase.evaluation.reference_answer ?? null,
                    judge_artifact_paths: editingCase.evaluation.judge_artifact_paths ?? [],
                    budgets: {
                      max_input_tokens: null,
                      max_output_tokens: null,
                      max_total_tokens: null,
                      max_duration_ms: null,
                      max_tool_calls: null,
                      max_iterations: null,
                      ...(editingCase.evaluation.budgets ?? {}),
                    },
                    hard_constraints: (editingCase.assertions ?? [])
                      .filter((item) => item.required)
                      .map((item) => item.id),
                  }
                : null
            }
            assertions={editingCase.assertions}
            rubric={editingCase.rubric}
            environment={{
              tool_mode: editingCase.execution?.tool_mode ?? null,
            }}
            onSave={(draft) => void saveCaseCriteria(draft)}
            onCancel={() => setEditingCase(null)}
          />
          {editorBusy ? (
            <p className="mt-2 text-[12px] text-muted-foreground">
              {t("settings.evaluation.reliability.caseEditor.saving")}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
