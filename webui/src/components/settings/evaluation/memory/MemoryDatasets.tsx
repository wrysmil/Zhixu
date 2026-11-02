import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** 契约 §1：数据集修订状态。staging 不可运行。 */
export type MemoryDatasetStatus = "staging" | "ready" | "invalid";

export type MemoryDatasetSourceView = {
  repoUrl: string;
  revision: string;
  license: string;
  sha256?: string | null;
  loaderVersion?: string | null;
  gradingProtocol?: string | null;
};

export type MemoryDatasetRevisionView = {
  /** DatasetRevision id（UUID）。 */
  id: string;
  datasetVersion: string;
  revision?: number;
  status: MemoryDatasetStatus;
  splitCounts: Record<string, number>;
  scenarioCount: number;
  probeCount?: number;
  /** 大白话能力覆盖标签，例如「事实记忆」「遗忘」。 */
  coverage: string[];
  source: MemoryDatasetSourceView;
  definitionDigest?: string | null;
  /** 导出只能引用服务端已登记 artifact，不在前端重新打包数据。 */
  exportArtifactRef?: string | null;
  createdAt?: string | null;
};

export type MemoryDatasetImportFormat = "json" | "jsonl" | "longmemeval";

export type MemoryDatasetImportErrorView = {
  /** JSONL 物理行号；JSON 数组解析失败时可为 null。 */
  line?: number | null;
  /** 出错字段路径，例如 events[0].messages[1].role。 */
  field?: string | null;
  scenarioId?: string | null;
  message: string;
};

export type MemoryDatasetImportOutcome = {
  status: MemoryDatasetStatus | "running";
  datasetRevisionId?: string | null;
  counts?: { scenarios: number; probes?: number };
  errors?: MemoryDatasetImportErrorView[];
};

export type MemoryDatasetImportMeta = {
  format: MemoryDatasetImportFormat;
  sourceRepo: string;
  sourceRevision: string;
  license: string;
};

/** 契约 §6：内置集合走 POST /samples/import 的 sample_id。 */
export type MemoryDatasetBuiltin = {
  sampleId: string;
  scenarioCount: number;
  split: "development" | "calibration" | "regression";
  /** i18n key 片段：settings.evaluation.memory.datasets.builtin.<slug>.* */
  slug: string;
};

export const MEMORY_BUILTIN_DATASETS: readonly MemoryDatasetBuiltin[] = [
  { sampleId: "memory-smoke-v1", scenarioCount: 20, split: "development", slug: "smoke" },
  { sampleId: "memory-core-v1", scenarioCount: 120, split: "regression", slug: "core" },
  { sampleId: "memory-calibration-v1", scenarioCount: 20, split: "calibration", slug: "calibration" },
];

export type MemoryDatasetsProps = {
  datasets?: MemoryDatasetRevisionView[];
  builtins?: readonly MemoryDatasetBuiltin[];
  loading?: boolean;
  error?: string | null;
  /** WU-11 注入 memory-evaluation-api；本组件不直接发请求。 */
  onImportDataset?: (file: File, meta: MemoryDatasetImportMeta) => Promise<MemoryDatasetImportOutcome>;
  onImportBuiltin?: (sampleId: string) => Promise<MemoryDatasetImportOutcome>;
  onExport?: (dataset: MemoryDatasetRevisionView) => void;
  onOpenScenarios?: (dataset: MemoryDatasetRevisionView) => void;
  onRunExperiment?: (dataset: MemoryDatasetRevisionView) => void;
};

const SPLIT_LABEL: Record<string, string> = {
  development: "开发",
  calibration: "校准",
  regression: "正式回归",
};

const STATUS_LABEL: Record<string, string> = {
  staging: "暂存中",
  ready: "可用",
  invalid: "不可用",
  running: "导入中",
};

const STATUS_CLASS: Record<string, string> = {
  staging: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  ready: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  invalid: "border-destructive/30 bg-destructive/10 text-destructive",
  running: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
};

function detectFormat(fileName: string): MemoryDatasetImportFormat {
  const lower = fileName.toLowerCase();
  if (lower.endsWith(".jsonl") || lower.endsWith(".ndjson")) return "jsonl";
  return "json";
}

export function MemoryDatasets({
  datasets = [],
  builtins = MEMORY_BUILTIN_DATASETS,
  loading = false,
  error = null,
  onImportDataset,
  onImportBuiltin,
  onExport,
  onOpenScenarios,
  onRunExperiment,
}: MemoryDatasetsProps) {
  const { t } = useTranslation();
  const tr = (key: string, defaultValue: string, options?: Record<string, unknown>) =>
    t(`settings.evaluation.memory.datasets.${key}`, { defaultValue, ...options });

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [format, setFormat] = useState<MemoryDatasetImportFormat>("jsonl");
  const [file, setFile] = useState<File | null>(null);
  const [sourceRepo, setSourceRepo] = useState("");
  const [sourceRevision, setSourceRevision] = useState("");
  const [license, setLicense] = useState("");
  const [importing, setImporting] = useState(false);
  const [importingBuiltin, setImportingBuiltin] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<MemoryDatasetImportOutcome | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);

  const sortedErrors = useMemo(() => {
    const rows = [...(outcome?.errors ?? [])];
    rows.sort((left, right) => (left.line ?? Number.MAX_SAFE_INTEGER) - (right.line ?? Number.MAX_SAFE_INTEGER));
    return rows;
  }, [outcome]);

  const submitImport = async () => {
    if (importing) return;
    if (!file) {
      setFormError(tr("import.fileRequired", "请选择 JSON 或 JSONL 文件"));
      return;
    }
    if (!sourceRepo.trim() || !sourceRevision.trim() || !license.trim()) {
      setFormError(tr("import.metaRequired", "导入前必须填写来源仓库、revision 与许可"));
      return;
    }
    if (!onImportDataset) return;
    setFormError(null);
    setRequestError(null);
    setOutcome(null);
    setImporting(true);
    try {
      const result = await onImportDataset(file, {
        format,
        sourceRepo: sourceRepo.trim(),
        sourceRevision: sourceRevision.trim(),
        license: license.trim(),
      });
      setOutcome(result);
    } catch (importFailure) {
      setRequestError(
        importFailure instanceof Error
          ? importFailure.message
          : tr("import.failed", "导入失败，请修正后重试"),
      );
    } finally {
      setImporting(false);
    }
  };

  const importBuiltin = async (sampleId: string) => {
    if (!onImportBuiltin || importingBuiltin) return;
    setImportingBuiltin(sampleId);
    setRequestError(null);
    setFormError(null);
    try {
      setOutcome(await onImportBuiltin(sampleId));
    } catch (importFailure) {
      setRequestError(
        importFailure instanceof Error
          ? importFailure.message
          : tr("import.failed", "导入失败，请修正后重试"),
      );
    } finally {
      setImportingBuiltin(null);
    }
  };

  return (
    <section className="min-w-0 space-y-6">
      <header className="space-y-1">
        <h3 className="text-base font-semibold text-foreground">{tr("title", "记忆测试集")}</h3>
        <p className="text-[12px] text-muted-foreground">
          {tr(
            "description",
            "内置 20 条冒烟、120 条核心回归与独立校准集；导入的集合先进入暂存，校验通过后才能运行。",
          )}
        </p>
      </header>

      {error ? (
        <p role="alert" className="text-[13px] text-destructive">
          {error}
        </p>
      ) : null}
      {requestError ? (
        <p role="alert" className="text-[13px] text-destructive">
          {requestError}
        </p>
      ) : null}

      <div className="rounded-panel border border-border/60 bg-settings-surface p-4">
        <h4 className="text-sm font-semibold text-foreground">{tr("builtin.title", "内置集合")}</h4>
        <ul className="mt-3 space-y-2">
          {builtins.map((builtin) => (
            <li
              key={builtin.sampleId}
              className="flex flex-wrap items-center gap-2 rounded-control border border-border/60 bg-background px-3 py-2 text-[13px]"
            >
              <span className="font-medium text-foreground">
                {tr(`builtin.${builtin.slug}.name`, builtin.sampleId)}
              </span>
              <span className="text-muted-foreground">{builtin.scenarioCount} 个场景</span>
              <span className="text-muted-foreground">
                {SPLIT_LABEL[builtin.split] ?? builtin.split}
              </span>
              <span className="ml-auto flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!onImportBuiltin || importingBuiltin === builtin.sampleId}
                  onClick={() => void importBuiltin(builtin.sampleId)}
                >
                  {importingBuiltin === builtin.sampleId
                    ? tr("builtin.importing", "导入中…")
                    : tr("builtin.import", "导入内置集合")}
                </Button>
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="rounded-panel border border-border/60 bg-settings-surface p-4">
        <h4 className="text-sm font-semibold text-foreground">{tr("import.title", "导入 JSON / JSONL")}</h4>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="space-y-1.5" htmlFor="memory-import-format">
            <span className="text-[12px] font-medium text-muted-foreground">
              {tr("import.format", "文件格式")}
            </span>
            <select
              id="memory-import-format"
              className={SELECT_CLASS}
              value={format}
              onChange={(event) => setFormat(event.target.value as MemoryDatasetImportFormat)}
            >
              <option value="jsonl">JSONL</option>
              <option value="json">JSON</option>
              <option value="longmemeval">LongMemEval</option>
            </select>
          </label>
          <div className="space-y-1.5">
            <span className="text-[12px] font-medium text-muted-foreground">
              {tr("import.file", "选择文件")}
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
              >
                {tr("import.chooseFile", "选择文件")}
              </Button>
              <span className="truncate text-[12px] text-muted-foreground">
                {file ? file.name : tr("import.noFile", "尚未选择文件")}
              </span>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,.jsonl,.ndjson"
              className="sr-only"
              aria-hidden
              tabIndex={-1}
              onChange={(event) => {
                const picked = event.target.files?.[0] ?? null;
                setFile(picked);
                if (picked) setFormat(detectFormat(picked.name));
                event.target.value = "";
              }}
            />
          </div>
          <label className="space-y-1.5" htmlFor="memory-import-repo">
            <span className="text-[12px] font-medium text-muted-foreground">
              {tr("import.sourceRepo", "来源仓库")}
            </span>
            <Input
              id="memory-import-repo"
              value={sourceRepo}
              onChange={(event) => setSourceRepo(event.target.value)}
              placeholder="https://…"
            />
          </label>
          <label className="space-y-1.5" htmlFor="memory-import-revision">
            <span className="text-[12px] font-medium text-muted-foreground">
              {tr("import.sourceRevision", "来源 revision")}
            </span>
            <Input
              id="memory-import-revision"
              value={sourceRevision}
              onChange={(event) => setSourceRevision(event.target.value)}
              placeholder="sha / tag"
            />
          </label>
          <label className="space-y-1.5" htmlFor="memory-import-license">
            <span className="text-[12px] font-medium text-muted-foreground">
              {tr("import.license", "许可")}
            </span>
            <Input
              id="memory-import-license"
              value={license}
              onChange={(event) => setLicense(event.target.value)}
              placeholder="CC-BY-4.0"
            />
          </label>
        </div>
        {formError ? (
          <p role="alert" className="mt-3 text-[12px] text-destructive">
            {formError}
          </p>
        ) : null}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" disabled={importing || !onImportDataset} onClick={() => void submitImport()}>
            {importing ? tr("import.importing", "导入中…") : tr("import.submit", "导入")}
          </Button>
          <span className="text-[12px] text-muted-foreground">
            {tr("import.hint", "导入结果先进入暂存；单条错误会定位到行号与字段。")}
          </span>
        </div>
      </div>

      {outcome ? (
        <div
          role="status"
          className={cn(
            "space-y-2 rounded-panel border px-4 py-3 text-[13px]",
            outcome.status === "invalid"
              ? "border-destructive/30 bg-destructive/5"
              : "border-border/60 bg-muted/20",
          )}
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-foreground">{tr("import.result", "导入结果")}</span>
            <span className={cn("rounded-full border px-2 py-0.5 text-[11px]", STATUS_CLASS[outcome.status])}>
              {STATUS_LABEL[outcome.status] ?? outcome.status}
            </span>
            {outcome.counts ? (
              <span className="text-muted-foreground">
                {tr("import.counts", "{{scenarios}} 个场景", { scenarios: outcome.counts.scenarios })}
                {typeof outcome.counts.probes === "number"
                  ? ` · ${tr("import.probeCounts", "{{probes}} 个问题", { probes: outcome.counts.probes })}`
                  : null}
              </span>
            ) : null}
          </div>
          {outcome.status === "staging" ? (
            <p className="text-[12px] text-amber-700 dark:text-amber-300">
              {tr("import.stagingHint", "当前为暂存版本，校验通过前不可运行。")}
            </p>
          ) : null}
          {sortedErrors.length > 0 ? (
            <div className="space-y-1">
              <p className="text-[12px] font-medium text-destructive">
                {tr("import.errors", "导入错误")}
              </p>
              <ul className="space-y-0.5">
                {sortedErrors.map((row, index) => (
                  <li key={index} className="text-[12px] text-foreground">
                    <span className="font-medium">
                      {row.line != null
                        ? tr("import.errorAt", "第 {{line}} 行", { line: row.line })
                        : tr("import.errorUnknownLine", "位置未知")}
                    </span>
                    {row.scenarioId ? (
                      <span className="text-muted-foreground"> · {row.scenarioId}</span>
                    ) : null}
                    {row.field ? (
                      <span className="text-muted-foreground">
                        {" "}
                        · {tr("import.errorField", "字段 {{field}}", { field: row.field })}
                      </span>
                    ) : null}
                    <span> · {row.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="space-y-2">
        <h4 className="text-sm font-semibold text-foreground">{tr("revisions.title", "已导入版本")}</h4>
        {loading ? (
          <p className="text-[13px] text-muted-foreground">{tr("loading", "加载中…")}</p>
        ) : null}
        {!loading && datasets.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">
            {tr("empty", "还没有已发布版本，先导入内置集合或 JSON/JSONL。")}
          </p>
        ) : null}
        <ul className="space-y-2">
          {datasets.map((dataset) => {
            const runnable = dataset.status === "ready";
            return (
              <li
                key={dataset.id}
                className="space-y-3 rounded-panel border border-border/60 bg-settings-surface px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-2 text-[13px]">
                  <span className="font-medium text-foreground">{dataset.datasetVersion}</span>
                  <span
                    className={cn(
                      "rounded-full border px-2 py-0.5 text-[11px] font-medium",
                      STATUS_CLASS[dataset.status],
                    )}
                  >
                    {STATUS_LABEL[dataset.status] ?? dataset.status}
                  </span>
                  <span className="text-muted-foreground">
                    {tr("meta.scenarios", "场景")}: {dataset.scenarioCount}
                  </span>
                  {typeof dataset.probeCount === "number" ? (
                    <span className="text-muted-foreground">
                      {tr("meta.probes", "问题")}: {dataset.probeCount}
                    </span>
                  ) : null}
                  {typeof dataset.revision === "number" ? (
                    <span className="text-muted-foreground">
                      {tr("meta.revision", "版本")}: {dataset.revision}
                    </span>
                  ) : null}
                  <span className="ml-auto flex flex-wrap items-center gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={!onOpenScenarios}
                      onClick={() => onOpenScenarios?.(dataset)}
                    >
                      {tr("actions.editScenarios", "查看场景")}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={!dataset.exportArtifactRef || !onExport}
                      title={
                        dataset.exportArtifactRef
                          ? dataset.exportArtifactRef
                          : tr("export.unavailable", "暂无导出引用")
                      }
                      onClick={() => onExport?.(dataset)}
                    >
                      {tr("actions.export", "导出")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant={runnable ? "default" : "outline"}
                      disabled={!runnable || !onRunExperiment}
                      title={runnable ? undefined : tr("run.staging", "暂存集不可运行，校验通过后才能运行")}
                      onClick={() => onRunExperiment?.(dataset)}
                    >
                      {tr("actions.run", "用于新实验")}
                    </Button>
                  </span>
                </div>
                <dl className="grid gap-x-6 gap-y-1 text-[12px] text-muted-foreground sm:grid-cols-2">
                  <div className="flex gap-1">
                    <dt>{tr("meta.source", "来源")}:</dt>
                    <dd className="min-w-0 break-all text-foreground">{dataset.source.repoUrl}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt>{tr("meta.sourceRevision", "来源版本")}:</dt>
                    <dd className="min-w-0 break-all text-foreground">{dataset.source.revision}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt>{tr("meta.license", "许可")}:</dt>
                    <dd className="text-foreground">{dataset.source.license}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt>{tr("meta.split", "split")}:</dt>
                    <dd className="text-foreground">
                      {Object.entries(dataset.splitCounts)
                        .map(([split, count]) => `${SPLIT_LABEL[split] ?? split} ${count}`)
                        .join(" · ") || "—"}
                    </dd>
                  </div>
                  {dataset.source.loaderVersion ? (
                    <div className="flex gap-1">
                      <dt>{tr("meta.loader", "加载器")}:</dt>
                      <dd className="text-foreground">{dataset.source.loaderVersion}</dd>
                    </div>
                  ) : null}
                  {dataset.definitionDigest ? (
                    <div className="flex gap-1">
                      <dt>{tr("meta.digest", "定义摘要")}:</dt>
                      <dd className="min-w-0 break-all text-foreground">{dataset.definitionDigest}</dd>
                    </div>
                  ) : null}
                  {dataset.exportArtifactRef ? (
                    <div className="flex gap-1 sm:col-span-2">
                      <dt>{tr("meta.exportArtifact", "导出引用")}:</dt>
                      <dd className="min-w-0 break-all text-foreground">{dataset.exportArtifactRef}</dd>
                    </div>
                  ) : null}
                </dl>
                <div className="flex flex-wrap items-center gap-1.5">
                  {dataset.coverage.length > 0 ? (
                    dataset.coverage.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full border border-border/60 bg-muted/30 px-2 py-0.5 text-[11px] text-muted-foreground"
                      >
                        {tag}
                      </span>
                    ))
                  ) : (
                    <span className="text-[11px] text-muted-foreground">
                      {tr("meta.coverageMissing", "能力覆盖未标注")}
                    </span>
                  )}
                </div>
                {!runnable ? (
                  <p className="text-[12px] text-amber-700 dark:text-amber-300">
                    {dataset.status === "staging"
                      ? tr("run.staging", "暂存集不可运行，校验通过后才能运行")
                      : tr("run.invalid", "该版本校验未通过，不能用于实验")}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

const SELECT_CLASS =
  "h-9 w-full rounded-control border border-border/70 bg-background px-2.5 text-[13px] text-foreground";
