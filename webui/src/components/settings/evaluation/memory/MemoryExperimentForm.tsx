import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export type MemoryTrack = "extraction" | "retrieval" | "end_to_end";
export type MemorySplit = "development" | "calibration" | "regression";
export type MemoryModelMode = "real" | "scripted";
export type MemoryCandidateMode = "zhixu" | "no_persistent_memory";

export const MEMORY_TRACKS: readonly MemoryTrack[] = ["extraction", "retrieval", "end_to_end"];
export const MEMORY_DEFAULT_MAX_TRIALS = 200;
/** 契约 §2：每个子 run 仍有 200 trials 上限。 */
export const MEMORY_MAX_TRIALS_PER_RUN = 200;
export const MEMORY_MAX_BATCH_SIZE = 40;

/** 契约 §2：实验候选可声明的改动项（JSON pointer 白名单示例）。 */
export const MEMORY_DEFAULT_CHANGED_FIELDS: readonly string[] = [
  "candidate.agent_profile_id",
  "candidate.extractor_profile_id",
  "memory_settings.retrieval_channels",
  "memory_settings.retrieval_threshold",
  "memory_settings.recall_limit",
  "memory_settings.injection_budget_tokens",
  "memory_settings.extraction_strategy_version",
  "memory_settings.embedding_model",
  "memory_settings.index_version",
];

export function memoryPlannedTrials(args: {
  scenarios: number;
  repetitions: number;
  candidates: number;
}): number {
  const scenarios = Math.max(0, Math.floor(args.scenarios));
  const repetitions = Math.max(1, Math.floor(args.repetitions) || 1);
  const candidates = Math.max(0, Math.floor(args.candidates));
  return scenarios * repetitions * candidates;
}

/** 契约 §2：分批大小 = min(40, floor(200/repetitions))，每候选独立分批。 */
export function memoryBatchPlan(args: {
  scenarios: number;
  repetitions: number;
  candidates: number;
}): { batchSize: number; batchesPerCandidate: number; childRuns: number } {
  const repetitions = Math.min(10, Math.max(1, Math.floor(args.repetitions) || 1));
  const batchSize = Math.max(
    1,
    Math.min(MEMORY_MAX_BATCH_SIZE, Math.floor(MEMORY_MAX_TRIALS_PER_RUN / repetitions)),
  );
  const scenarios = Math.max(0, Math.floor(args.scenarios));
  const batches = scenarios === 0 ? 0 : Math.ceil(scenarios / batchSize);
  return {
    batchSize,
    batchesPerCandidate: batches,
    childRuns: batches * Math.max(0, Math.floor(args.candidates)),
  };
}

export type MemoryCapabilityView = {
  status: "supported" | "unsupported" | "partial";
  detail?: string;
};

/** GET /memory/capabilities 的视图；缺失能力必须显式展示。 */
export type MemoryExperimentCapabilitiesView = {
  tracks: Partial<Record<MemoryTrack, MemoryCapabilityView>>;
  /** memory_settings 白名单：只渲染这里声明的参数。 */
  settings: string[];
  judge_calibration?: "calibrated" | "not_calibrated" | "unknown";
  delete?: MemoryCapabilityView;
  clock_coverage?: MemoryCapabilityView;
  scope_coverage?: MemoryCapabilityView;
};

export type MemoryDatasetOption = {
  id: string;
  label: string;
  split: MemorySplit;
  status: "staging" | "ready" | "invalid";
  scenarioCount: number;
  splitCounts?: Record<string, number>;
  /** 含 forget 事件的场景数：决定删除能力缺失是否影响本次实验。 */
  forgetScenarios?: number;
};

export type MemoryBaselineOption = {
  id: string;
  label: string;
  datasetRevisionId: string;
  track: MemoryTrack;
  repetitions: number;
  modelMode: MemoryModelMode;
  primaryMetric?: string | null;
};

export type MemoryProfileOption = {
  id: string;
  label: string;
  kind: "agent" | "judge" | "both";
  model?: string;
  provider?: string;
};

export type MemoryMetricOption = {
  id: string;
  label: string;
  unit: string;
};

export type MemoryScriptFixtureOption = {
  id: string;
  label: string;
};

export type MemoryBudgetsDraft = {
  max_trials: number | null;
  max_model_requests: number | null;
  max_input_tokens: number | null;
  max_output_tokens: number | null;
  max_total_cost: number | null;
  per_trial_timeout_s: number | null;
  group_timeout_s: number | null;
  concurrency: number | null;
};

export type MemoryCandidateDraft = {
  id: string;
  name: string;
  mode: MemoryCandidateMode;
  agent_profile_id: string;
  extractor_profile_id: string | null;
  memory_settings: Record<string, unknown>;
};

export type MemoryExperimentDraft = {
  dataset_revision_id: string;
  split: MemorySplit;
  track: MemoryTrack;
  candidates: MemoryCandidateDraft[];
  repetitions: number;
  model_mode: MemoryModelMode;
  script_fixture_id?: string;
  judge_profile_id: string | null;
  budgets: MemoryBudgetsDraft;
  primary_metric: string;
  baseline_experiment_id: string | null;
  changed_fields: string[];
};

export type MemoryExperimentFormProps = {
  capabilities: MemoryExperimentCapabilitiesView;
  datasets: MemoryDatasetOption[];
  baselines?: MemoryBaselineOption[];
  profiles?: MemoryProfileOption[];
  metrics: MemoryMetricOption[];
  changedFieldOptions?: readonly string[];
  scriptFixtures?: readonly MemoryScriptFixtureOption[];
  defaultDatasetId?: string;
  defaultTrack?: MemoryTrack;
  defaultRepetitions?: number;
  defaultMaxTrials?: number;
  budgetDefaults?: Partial<MemoryBudgetsDraft>;
  busy?: boolean;
  error?: string | null;
  onSubmit?: (draft: MemoryExperimentDraft) => void | Promise<void>;
  onCancel?: () => void;
};

type CandidateState = {
  name: string;
  mode: MemoryCandidateMode;
  agentProfileId: string;
  extractorProfileId: string;
  settings: Record<string, string | boolean>;
};

type SettingDefinition = {
  label: string;
  kind: "boolean" | "number" | "text";
};

const SETTING_DEFINITIONS: Record<string, SettingDefinition> = {
  extraction_enabled: { label: "启用抽取", kind: "boolean" },
  retrieval_channels: { label: "检索通道（逗号分隔）", kind: "text" },
  retrieval_threshold: { label: "检索阈值", kind: "number" },
  recall_limit: { label: "召回上限", kind: "number" },
  injection_budget_tokens: { label: "注入预算（token）", kind: "number" },
  extraction_strategy_version: { label: "抽取策略版本", kind: "text" },
  merge_strategy_version: { label: "合并策略版本", kind: "text" },
  embedding_model: { label: "嵌入模型", kind: "text" },
  index_version: { label: "索引版本", kind: "text" },
};

const SPLIT_LABEL: Record<MemorySplit, string> = {
  development: "开发",
  calibration: "校准",
  regression: "正式回归",
};

const TRACK_LABEL: Record<MemoryTrack, string> = {
  extraction: "是否记下来",
  retrieval: "是否找得到",
  end_to_end: "完整记忆效果",
};

const PRIMARY_METRIC_BY_TRACK: Record<MemoryTrack, string> = {
  extraction: "extraction_recall",
  retrieval: "evidence_recall_at_k",
  end_to_end: "qa_accuracy_scenario_macro",
};

// 与后端 experiments.validate_primary_metric 的判分阶段保持一致。
const METRICS_BY_TRACK: Record<MemoryTrack, readonly string[]> = {
  extraction: [
    "extraction_precision", "extraction_recall", "extraction_f1",
    "unsupported_memory_rate", "negative_write_success", "duplicate_memory_rate",
  ],
  retrieval: [
    "evidence_recall_at_k", "retrieval_precision_returned_at_k", "mrr", "injection_recall",
  ],
  end_to_end: [
    "qa_accuracy", "grounded_memory_success", "fact_coverage", "qa_accuracy_scenario_macro",
    "qa_accuracy_probe_micro", "qa_accuracy_category_macro", "strict_scenario_pass_rate",
    "repeat_stability", "preference_compliance", "update_accuracy", "stale_use_rate",
    "abstention_accuracy", "false_abstention_rate", "cross_session_accuracy", "restart_accuracy",
  ],
};

function defaultMetric(track: MemoryTrack, metrics: MemoryMetricOption[]): string {
  return (
    metrics.find((metric) => metric.id === PRIMARY_METRIC_BY_TRACK[track])?.id ??
    metrics.find((metric) => METRICS_BY_TRACK[track].includes(metric.id))?.id ?? ""
  );
}

function toNumberOrNull(raw: string): number | null {
  if (raw.trim() === "") return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeSettings(settings: Record<string, string | boolean>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(settings)) {
    const definition = SETTING_DEFINITIONS[key];
    if (typeof value === "boolean") {
      result[key] = value;
      continue;
    }
    if (value.trim() === "") continue;
    if (definition?.kind === "number") {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) result[key] = parsed;
      continue;
    }
    if (key === "retrieval_channels") {
      result[key] = value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      continue;
    }
    result[key] = value.trim();
  }
  return result;
}

export function MemoryExperimentForm({
  capabilities,
  datasets,
  baselines = [],
  profiles = [],
  metrics,
  changedFieldOptions = MEMORY_DEFAULT_CHANGED_FIELDS,
  scriptFixtures = [],
  defaultDatasetId,
  defaultTrack,
  defaultRepetitions = 1,
  defaultMaxTrials = MEMORY_DEFAULT_MAX_TRIALS,
  budgetDefaults,
  busy = false,
  error = null,
  onSubmit,
  onCancel,
}: MemoryExperimentFormProps) {
  const { t } = useTranslation();
  const tr = (key: string, defaultValue: string, options?: Record<string, unknown>) =>
    t(`settings.evaluation.memory.form.${key}`, { defaultValue, ...options });

  const [modelMode, setModelMode] = useState<MemoryModelMode>("real");
  const modelProfiles = useMemo(
    () => profiles.filter((profile) =>
      profile.kind !== "judge" && (modelMode === "scripted" || profile.provider !== "controlled")),
    [profiles, modelMode],
  );
  const judgeProfiles = profiles.filter((profile) =>
    profile.kind !== "agent" && (modelMode === "scripted" || profile.provider !== "controlled"));
  const firstSupportedTrack =
    MEMORY_TRACKS.find((track) => (capabilities.tracks[track]?.status ?? "unsupported") === "supported") ??
    "end_to_end";
  const preferredTrack =
    defaultTrack && (capabilities.tracks[defaultTrack]?.status ?? "unsupported") === "supported"
      ? defaultTrack
      : (capabilities.tracks.end_to_end?.status ?? "unsupported") === "supported"
        ? "end_to_end"
        : firstSupportedTrack;

  const initialDataset =
    datasets.find((dataset) => dataset.id === defaultDatasetId && dataset.status === "ready") ??
    datasets.find((dataset) => dataset.label === "memory-smoke-v1" && dataset.status === "ready") ??
    datasets.find((dataset) => dataset.status === "ready") ??
    null;

  const [datasetId, setDatasetId] = useState(initialDataset?.id ?? "");
  const [split, setSplit] = useState<MemorySplit>(initialDataset?.split ?? "development");
  const [track, setTrack] = useState<MemoryTrack>(preferredTrack);
  const [repetitionText, setRepetitionText] = useState(String(defaultRepetitions));
  const repetitions = Math.min(10, Math.max(1, Number.parseInt(repetitionText, 10) || 1));
  const [scriptFixtureId, setScriptFixtureId] = useState("");
  const [judgeProfileId, setJudgeProfileId] = useState("");
  const [primaryMetric, setPrimaryMetric] = useState(() => defaultMetric(preferredTrack, metrics));
  const [advanced, setAdvanced] = useState(false);
  const [baselineId, setBaselineId] = useState("");
  const [changedFields, setChangedFields] = useState<string[]>([]);
  const [budgets, setBudgets] = useState<MemoryBudgetsDraft>({
    max_trials: defaultMaxTrials,
    max_model_requests: null,
    max_input_tokens: null,
    max_output_tokens: null,
    max_total_cost: null,
    // 受 RunLimits.timeout_s 上限约束（5..900s），默认给到上限。
    per_trial_timeout_s: 900,
    group_timeout_s: null,
    concurrency: 1,
    ...budgetDefaults,
  });
  const [acknowledged, setAcknowledged] = useState(false);
  const [candidates, setCandidates] = useState<CandidateState[]>([
    {
      name: "候选 1",
      mode: "zhixu",
      agentProfileId: modelProfiles[0]?.id ?? "",
      extractorProfileId: "",
      settings: {},
    },
  ]);
  const [localError, setLocalError] = useState<string | null>(null);

  const selectedDataset = datasets.find((dataset) => dataset.id === datasetId) ?? null;
  const trackMetrics = metrics.filter((metric) => METRICS_BY_TRACK[track].includes(metric.id));
  const hasSupportedTrack = MEMORY_TRACKS.some((value) => capabilities.tracks[value]?.status === "supported");
  const baseline = baselines.find((item) => item.id === baselineId) ?? null;
  const scenarioCount =
    selectedDataset == null
      ? 0
      : selectedDataset.splitCounts?.[split] ?? selectedDataset.scenarioCount;

  const plannedTrials = memoryPlannedTrials({
    scenarios: scenarioCount,
    repetitions,
    candidates: candidates.length,
  });
  const batchPlan = memoryBatchPlan({
    scenarios: scenarioCount,
    repetitions,
    candidates: candidates.length,
  });
  const maxTrials = budgets.max_trials;
  const overBudget = maxTrials != null && plannedTrials > maxTrials;

  const baselineMismatches = useMemo(() => {
    if (!baseline) return [] as string[];
    const mismatches: string[] = [];
    if (baseline.datasetRevisionId !== datasetId) mismatches.push(tr("field.dataset", "测试集"));
    if (baseline.track !== track) mismatches.push(tr("field.track", "测试方式"));
    if (baseline.repetitions !== repetitions) mismatches.push(tr("field.repetitions", "重复次数"));
    if (baseline.modelMode !== modelMode) mismatches.push(tr("field.modelMode", "模型模式"));
    if (baseline.primaryMetric && baseline.primaryMetric !== primaryMetric) {
      mismatches.push(tr("field.primaryMetric", "主要指标"));
    }
    return mismatches;
  }, [baseline, datasetId, modelMode, primaryMetric, repetitions, t, track]);

  const warnings = useMemo(() => {
    const items: string[] = [];
    if (!judgeProfileId) {
      items.push(tr("warning.judgeNone", "未选择 Judge：语义评分项将标记为待复核。"));
    } else if (capabilities.judge_calibration !== "calibrated") {
      items.push(tr("warning.judgeUncalibrated", "Judge 未校准：语义类指标将显示待复核。"));
    }
    if (
      (selectedDataset?.forgetScenarios ?? 0) > 0 &&
      (capabilities.delete?.status ?? "unsupported") !== "supported"
    ) {
      items.push(tr("warning.deleteUnsupported", "后端不支持删除：遗忘类场景无法通过，将标记为不支持。"));
    }
    if (capabilities.clock_coverage && capabilities.clock_coverage.status !== "supported") {
      items.push(tr("warning.clockCoverage", "时钟覆盖不足：时间相关判定将不可评分。"));
    }
    if (capabilities.scope_coverage && capabilities.scope_coverage.status !== "supported") {
      items.push(tr("warning.scopeCoverage", "作用域覆盖不足：工作区隔离类指标将不可评分。"));
    }
    return items;
  }, [capabilities, judgeProfileId, selectedDataset, t]);

  const blocking = useMemo(() => {
    const items: string[] = [];
    if (!selectedDataset || selectedDataset.status !== "ready") {
      items.push(tr("require.dataset", "请选择可运行的测试集版本。"));
    }
    if ((capabilities.tracks[track]?.status ?? "unsupported") !== "supported") {
      items.push(tr("require.track", "所选测试方式当前版本不支持。"));
    }
    if (!METRICS_BY_TRACK[track].includes(primaryMetric) || !metrics.some((metric) => metric.id === primaryMetric)) {
      items.push(tr("require.metric", "请选择主要指标。"));
    }
    if (candidates.some((candidate) => !modelProfiles.some((profile) => profile.id === candidate.agentProfileId))) {
      items.push(tr("require.candidateProfile", "每个候选都需要被测模型。"));
    }
    if (modelMode === "scripted" && !scriptFixtureId) {
      items.push(tr("require.scriptFixture", "脚本模式必须选择受控 fixture。"));
    }
    if (baseline && changedFields.length === 0) {
      items.push(tr("require.changedFields", "与历史基线比较前必须声明本次改动项。"));
    }
    if (overBudget) {
      items.push(
        tr("overBudget", "计划 {{trials}} 次试验超过总预算 {{budget}}，请显式调高预算或减少候选/重复次数。", {
          trials: plannedTrials,
          budget: maxTrials,
        }),
      );
    }
    return items;
  }, [
    baseline,
    capabilities,
    candidates,
    changedFields.length,
    maxTrials,
    modelMode,
    modelProfiles,
    metrics,
    overBudget,
    plannedTrials,
    primaryMetric,
    scriptFixtureId,
    selectedDataset,
    t,
    track,
  ]);

  const canSubmit =
    blocking.length === 0 && (warnings.length === 0 || acknowledged) && !busy;

  const applyTrack = (next: MemoryTrack) => {
    setTrack(next);
    setPrimaryMetric(defaultMetric(next, metrics));
    setCandidates((current) =>
      current.map((candidate) =>
        next === "end_to_end" || candidate.mode !== "no_persistent_memory"
          ? candidate
          : { ...candidate, mode: "zhixu" },
      ),
    );
  };

  const applyDataset = (id: string) => {
    setDatasetId(id);
    const dataset = datasets.find((item) => item.id === id);
    if (dataset) setSplit(dataset.split);
  };

  const updateCandidate = (index: number, patch: Partial<CandidateState>) => {
    setCandidates((current) =>
      current.map((candidate, position) => (position === index ? { ...candidate, ...patch } : candidate)),
    );
  };

  const updateSetting = (index: number, key: string, value: string | boolean) => {
    setCandidates((current) =>
      current.map((candidate, position) =>
        position === index
          ? { ...candidate, settings: { ...candidate.settings, [key]: value } }
          : candidate,
      ),
    );
  };

  const submit = async () => {
    if (!canSubmit || !onSubmit) return;
    setLocalError(null);
    const draft: MemoryExperimentDraft = {
      dataset_revision_id: datasetId,
      split,
      track,
      candidates: candidates.map((candidate, index) => ({
        id: `candidate-${index + 1}`,
        name: candidate.name.trim() || `候选 ${index + 1}`,
        mode: candidate.mode,
        agent_profile_id: candidate.agentProfileId,
        extractor_profile_id: candidate.extractorProfileId || null,
        memory_settings:
          candidate.mode === "zhixu" ? normalizeSettings(candidate.settings) : {},
      })),
      repetitions,
      model_mode: modelMode,
      ...(modelMode === "scripted" && scriptFixtureId
        ? { script_fixture_id: scriptFixtureId }
        : {}),
      judge_profile_id: judgeProfileId || null,
      budgets,
      primary_metric: primaryMetric,
      baseline_experiment_id: baselineId || null,
      changed_fields: baselineId ? changedFields : [],
    };
    try {
      await onSubmit(draft);
    } catch (submitFailure) {
      setLocalError(
        submitFailure instanceof Error
          ? submitFailure.message
          : tr("submitFailed", "提交失败，请重试。"),
      );
    }
  };

  return (
    <section aria-label={tr("title", "新建记忆实验")} className="min-w-0 space-y-6">
      <header className="space-y-1">
        <h3 className="text-base font-semibold text-foreground">{tr("title", "新建记忆实验")}</h3>
        <p className="text-[12px] text-muted-foreground">
          {tr(
            "subtitle",
            "选一套测试题和模型，看看 AI 能否记住信息，并在之后的对话中用对。",
          )}
        </p>
      </header>

      <div className="grid gap-5 rounded-panel border border-border/60 bg-settings-surface p-4 lg:grid-cols-2">
        <label className="space-y-1.5" htmlFor="memory-experiment-dataset">
          <span className="text-[12px] font-medium text-muted-foreground">
            {tr("dataset", "测试集版本")}
          </span>
          <select
            id="memory-experiment-dataset"
            className={SELECT_CLASS}
            value={datasetId}
            onChange={(event) => applyDataset(event.target.value)}
          >
            <option value="">{tr("datasetNone", "请选择可用测试集")}</option>
            {datasets.map((dataset) => (
              <option key={dataset.id} value={dataset.id} disabled={dataset.status !== "ready"}>
                {dataset.label}
                {dataset.status !== "ready"
                  ? ` · ${tr("datasetNotRunnable", "暂存集不可运行")}`
                  : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1.5" htmlFor="memory-candidate-agent-0">
          <span className="text-[12px] font-medium text-muted-foreground">
            {tr("agentProfile", "被测模型")}
          </span>
          <select
            id="memory-candidate-agent-0"
            className={SELECT_CLASS}
            value={candidates[0].agentProfileId}
            onChange={(event) => updateCandidate(0, { agentProfileId: event.target.value })}
          >
            <option value="">{tr("profileNone", "请选择模型")}</option>
            {modelProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.label}{profile.model ? ` (${profile.model})` : ""}
              </option>
            ))}
          </select>
        </label>
        <p className="text-[12px] leading-relaxed text-muted-foreground lg:col-span-2">
          {tr("quickHint", "默认测试完整记忆效果：先回放历史对话，再提问检查回答。首次推荐 20 场景冒烟集。")}
        </p>
      </div>

      {!hasSupportedTrack ? (
        <p role="alert" className="rounded-panel border border-destructive/20 bg-destructive/5 p-3 text-[13px] text-destructive">
          {tr("notReady", "记忆评测尚未就绪：服务未提供可运行的记忆能力。请刷新重试；仍不可用时检查评估服务的记忆模块装配。")}
        </p>
      ) : null}
      {!datasets.some((dataset) => dataset.status === "ready") ? (
        <p role="alert" className="text-[12px] text-destructive">
          {tr("noDatasetHint", "还没有可运行的记忆测试集，请到“记忆测试集”导入内置 20 场景冒烟集。")}
        </p>
      ) : null}
      {modelProfiles.length === 0 ? (
        <p role="alert" className="text-[12px] text-destructive">
          {tr("noModelHint", "没有可用的被测模型，请先在模型设置中添加模型预设，再刷新此页。")}
        </p>
      ) : null}

      <div className="space-y-1 rounded-panel border border-border/60 bg-muted/20 px-4 py-3 text-[12px] text-muted-foreground">
        <p className="font-medium text-foreground">{tr(`trackLabel.${track}`, TRACK_LABEL[track])}</p>
        <p>
          {tr("plan", "计划 {{trials}} 次试验（{{scenarios}} 个场景 × {{repetitions}} 次重复 × {{candidates}} 个候选）", {
            trials: plannedTrials, scenarios: scenarioCount, repetitions, candidates: candidates.length,
          })}
        </p>
        <p>{modelMode === "real"
          ? tr("quickBudget", "最多 {{budget}} 次试验；调用真实模型，费用按实际用量计量。", { budget: maxTrials ?? MEMORY_DEFAULT_MAX_TRIALS })
          : tr("quickScriptedBudget", "最多 {{budget}} 次试验；使用固定脚本，只验证流程，不代表真实记忆效果。", { budget: maxTrials ?? MEMORY_DEFAULT_MAX_TRIALS })}</p>
      </div>

      <div className="space-y-2 border-t border-border/60 pt-3">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-expanded={advanced}
          aria-controls="memory-advanced-settings"
          onClick={() => setAdvanced((current) => !current)}
        >
          {tr("advanced", "高级设置")}
        </Button>
        <p className="text-[11px] text-muted-foreground">
          {tr("advancedHint", "需要专项排查或版本对比时，再调整测试方式、对照组、记忆参数和预算。")}
        </p>
      </div>

      {advanced ? (
        <div id="memory-advanced-settings" className="space-y-6">
        <div className="grid gap-5 rounded-panel border border-border/60 bg-settings-surface p-4 lg:grid-cols-2">
        <label className="space-y-1.5" htmlFor="memory-experiment-split">
          <span className="text-[12px] font-medium text-muted-foreground">{tr("split", "范围")}</span>
          <select
            id="memory-experiment-split"
            className={SELECT_CLASS}
            value={split}
            onChange={(event) => setSplit(event.target.value as MemorySplit)}
          >
            {(Object.keys(SPLIT_LABEL) as MemorySplit[]).map((value) => (
              <option key={value} value={value}>
                {SPLIT_LABEL[value]}
                {selectedDataset?.splitCounts?.[value] != null
                  ? ` · ${selectedDataset.splitCounts[value]} 个场景`
                  : ""}
              </option>
            ))}
          </select>
        </label>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold text-foreground">{tr("track", "测试方式")}</legend>
        <div className="grid gap-2 sm:grid-cols-3">
          {MEMORY_TRACKS.map((value) => {
            const capability = capabilities.tracks[value] ?? { status: "unsupported" as const };
            const supported = capability.status === "supported";
            return (
              <label
                key={value}
                className={cn(
                  "flex cursor-pointer flex-col gap-1 rounded-control border px-3 py-2 text-[13px]",
                  track === value
                    ? "border-primary/40 bg-primary/5"
                    : "border-border/60 bg-background",
                  !supported && "cursor-not-allowed opacity-70",
                )}
              >
                <span className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="memory-experiment-track"
                    value={value}
                    checked={track === value}
                    disabled={!supported}
                    onChange={() => applyTrack(value)}
                  />
                  <span className="font-medium text-foreground">{tr(`trackLabel.${value}`, TRACK_LABEL[value])}</span>
                </span>
                <span className="text-[11px] text-muted-foreground">
                  {capability.status === "supported"
                    ? tr("trackSupported", "当前版本支持")
                    : capability.status === "partial"
                      ? tr("trackPartial", "部分支持：{{detail}}", {
                          detail: capability.detail ?? "",
                        })
                      : tr("trackUnsupported", "当前版本不支持此项评测")}
                  {!supported && capability.detail ? ` · ${capability.detail}` : ""}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>

      <div className="grid gap-5 rounded-panel border border-border/60 bg-settings-surface p-4 lg:grid-cols-2">
        <div className="space-y-1.5">
          <label className="space-y-1.5" htmlFor="memory-experiment-baseline">
            <span className="text-[12px] font-medium text-muted-foreground">
              {tr("baseline", "历史基线")}
            </span>
            <select
              id="memory-experiment-baseline"
              className={SELECT_CLASS}
              value={baselineId}
              onChange={(event) => setBaselineId(event.target.value)}
            >
              <option value="">{tr("baselineNone", "不设置基线")}</option>
              {baselines.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <span className="block text-[11px] text-muted-foreground">
            {tr("baselineHint", "基线从已保存实验选择；本版本不支持从界面切换 Git 源码。")}
          </span>
        </div>
        <label className="space-y-1.5" htmlFor="memory-experiment-metric">
          <span className="text-[12px] font-medium text-muted-foreground">
            {tr("primaryMetric", "主要指标")}
          </span>
          <select
            id="memory-experiment-metric"
            className={SELECT_CLASS}
            value={primaryMetric}
            onChange={(event) => setPrimaryMetric(event.target.value)}
          >
            {trackMetrics.map((metric) => (
              <option key={metric.id} value={metric.id}>
                {tr(`metric.${metric.id}`, metric.label)} ({metric.unit})
              </option>
            ))}
          </select>
        </label>
        {baseline && baselineMismatches.length > 0 ? (
          <p role="status" className="text-[12px] text-amber-700 lg:col-span-2 dark:text-amber-300">
            {tr("baselineMismatch", "基线条件不一致：{{fields}}；只能作有限比较。", {
              fields: baselineMismatches.join("、"),
            })}
          </p>
        ) : null}
        <fieldset className="space-y-1.5 lg:col-span-2">
          <legend className="text-[12px] font-medium text-muted-foreground">
            {tr("changedFields", "本次改了什么（冻结条件）")}
          </legend>
          <div className="grid gap-1 sm:grid-cols-2">
            {changedFieldOptions.map((field) => (
              <label key={field} className="flex items-center gap-2 text-[12px] text-foreground">
                <input
                  type="checkbox"
                  className="h-4 w-4"
                  checked={changedFields.includes(field)}
                  onChange={(event) =>
                    setChangedFields((current) =>
                      event.target.checked
                        ? [...current, field]
                        : current.filter((item) => item !== field),
                    )
                  }
                />
                <span className="break-all">{field}</span>
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      <fieldset className="space-y-3">
        <legend className="text-sm font-semibold text-foreground">
          {tr("candidates", "候选（{{count}}/3）", { count: candidates.length })}
        </legend>
        <ul className="space-y-3">
          {candidates.map((candidate, index) => (
            <li
              key={index}
              className="space-y-3 rounded-panel border border-border/60 bg-settings-surface p-3"
            >
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="space-y-1.5" htmlFor={`memory-candidate-name-${index}`}>
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {tr("candidateName", "候选名称")}
                  </span>
                  <Input
                    id={`memory-candidate-name-${index}`}
                    value={candidate.name}
                    onChange={(event) => updateCandidate(index, { name: event.target.value })}
                  />
                </label>
                <label className="space-y-1.5" htmlFor={`memory-candidate-mode-${index}`}>
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {tr("candidateMode", "候选模式")}
                  </span>
                  <select
                    id={`memory-candidate-mode-${index}`}
                    className={SELECT_CLASS}
                    value={candidate.mode}
                    onChange={(event) =>
                      updateCandidate(index, {
                        mode: event.target.value as MemoryCandidateMode,
                      })
                    }
                  >
                    <option value="zhixu">{tr("mode.zhixu", "知序记忆")}</option>
                    <option value="no_persistent_memory" disabled={track !== "end_to_end"}>
                      {tr("mode.noPersistentMemory", "无持久记忆参考组")}
                    </option>
                  </select>
                </label>
                {index > 0 ? <label className="space-y-1.5" htmlFor={`memory-candidate-agent-${index}`}>
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {tr("agentProfile", "被测模型")}
                  </span>
                  <select
                    id={`memory-candidate-agent-${index}`}
                    className={SELECT_CLASS}
                    value={candidate.agentProfileId}
                    onChange={(event) =>
                      updateCandidate(index, { agentProfileId: event.target.value })
                    }
                  >
                    <option value="">{tr("profileNone", "请选择模型")}</option>
                    {modelProfiles.map((profile) => (
                      <option key={profile.id} value={profile.id}>
                        {profile.label}
                        {profile.model ? ` (${profile.model})` : ""}
                      </option>
                    ))}
                  </select>
                </label> : null}
                <div className="space-y-1.5">
                  <label htmlFor={`memory-candidate-extractor-${index}`} className="text-[12px] font-medium text-muted-foreground">
                    {tr("extractorProfile", "抽取模型")}
                  </label>
                  <select
                    id={`memory-candidate-extractor-${index}`}
                    className={SELECT_CLASS}
                    value={candidate.extractorProfileId}
                    disabled
                  >
                    <option value="">{tr("extractorNone", "使用被测模型")}</option>
                  </select>
                  <span className="block text-[11px] text-muted-foreground">
                    {tr("extractorShared", "当前评测的记忆抽取与回答共用被测模型。")}
                  </span>
                </div>
              </div>

              {candidate.mode === "zhixu" && capabilities.settings.length > 0 ? (
                <div className="space-y-2 rounded-control border border-border/60 bg-background p-3">
                  <p className="text-[12px] font-medium text-muted-foreground">
                    {tr("settings", "记忆参数（仅展示后端支持且会生效的字段）")}
                  </p>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {capabilities.settings.map((key) => {
                      const definition = SETTING_DEFINITIONS[key] ?? {
                        label: key,
                        kind: "text" as const,
                      };
                      const inputId = `memory-setting-${index}-${key}`;
                      if (definition.kind === "boolean") {
                        return (
                          <label key={key} className="flex items-center gap-2 text-[12px] text-foreground">
                            <input
                              id={inputId}
                              type="checkbox"
                              className="h-4 w-4"
                              checked={candidate.settings[key] === true}
                              onChange={(event) => updateSetting(index, key, event.target.checked)}
                            />
                            {definition.label}
                          </label>
                        );
                      }
                      return (
                        <label key={key} className="space-y-1.5" htmlFor={inputId}>
                          <span className="text-[12px] text-muted-foreground">{definition.label}</span>
                          <Input
                            id={inputId}
                            type={definition.kind === "number" ? "number" : "text"}
                            value={
                              typeof candidate.settings[key] === "string"
                                ? (candidate.settings[key] as string)
                                : ""
                            }
                            onChange={(event) => updateSetting(index, key, event.target.value)}
                          />
                        </label>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {candidates.length > 1 ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setCandidates((current) => current.filter((_, position) => position !== index))
                  }
                >
                  {tr("removeCandidate", "删除候选")}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
        {candidates.length < 3 ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={modelProfiles.length === 0}
            onClick={() =>
              setCandidates((current) => [
                ...current,
                {
                  name: `候选 ${current.length + 1}`,
                  mode: "zhixu",
                  agentProfileId: modelProfiles[0]?.id ?? "",
                  extractorProfileId: "",
                  settings: {},
                },
              ])
            }
          >
            {tr("addCandidate", "添加候选")}
          </Button>
        ) : null}
        <p className="text-[11px] text-muted-foreground">
          {tr("modeHint", "无持久记忆参考组只用于完整记忆效果，不参与抽取算法排名。")}
        </p>
      </fieldset>

      <div className="grid gap-4 rounded-panel border border-border/60 bg-settings-surface p-4 lg:grid-cols-2">
        <label className="space-y-1.5" htmlFor="memory-experiment-judge">
          <span className="text-[12px] font-medium text-muted-foreground">
            {tr("judge", "评分 Judge")}
          </span>
          <select
            id="memory-experiment-judge"
            className={SELECT_CLASS}
            value={judgeProfileId}
            onChange={(event) => setJudgeProfileId(event.target.value)}
          >
            <option value="">{tr("judgeNone", "未选择")}</option>
            {judgeProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.label}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1.5" htmlFor="memory-experiment-model-mode">
          <span className="text-[12px] font-medium text-muted-foreground">
            {tr("modelMode", "模型模式")}
          </span>
          <select
            id="memory-experiment-model-mode"
            className={SELECT_CLASS}
            value={modelMode}
            onChange={(event) => {
              const next = event.target.value as MemoryModelMode;
              setModelMode(next);
              if (next === "real") {
                setScriptFixtureId("");
                const realProfiles = profiles.filter((profile) => profile.provider !== "controlled");
                setCandidates((current) => current.map((candidate) => ({
                  ...candidate,
                  agentProfileId: realProfiles.some((profile) => profile.kind !== "judge" && profile.id === candidate.agentProfileId)
                    ? candidate.agentProfileId
                    : realProfiles.find((profile) => profile.kind !== "judge")?.id ?? "",
                })));
                if (!realProfiles.some((profile) => profile.kind !== "agent" && profile.id === judgeProfileId)) setJudgeProfileId("");
              }
            }}
          >
            <option value="real">{tr("modelMode.real", "真实模型")}</option>
            {/* 没有受控 fixture 时不给这个选项：灰着但选不了只会让人以为能选。 */}
            {scriptFixtures.length > 0 ? (
              <option value="scripted">
                {tr("modelMode.scripted", "固定脚本（受控 fixture）")}
              </option>
            ) : null}
          </select>
          {scriptFixtures.length === 0 ? (
            <span aria-hidden="true" className="text-[11px] leading-5 text-muted-foreground">
              {tr(
                "modelMode.scriptedUnavailable",
                "当前部署没有受控 fixture，只能使用真实模型。",
              )}
            </span>
          ) : null}
        </label>
        {modelMode === "scripted" ? (
          <label className="space-y-1.5 lg:col-span-2" htmlFor="memory-experiment-script-fixture">
            <span className="text-[12px] font-medium text-muted-foreground">
              {tr("scriptFixture", "脚本 fixture")}
            </span>
            <select
              id="memory-experiment-script-fixture"
              className={SELECT_CLASS}
              value={scriptFixtureId}
              onChange={(event) => setScriptFixtureId(event.target.value)}
            >
              <option value="">{tr("scriptFixtureNone", "请选择受控 fixture")}</option>
              {scriptFixtures.map((fixture) => (
                <option key={fixture.id} value={fixture.id}>
                  {fixture.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label className="space-y-1.5" htmlFor="memory-experiment-repetitions">
          <span className="text-[12px] font-medium text-muted-foreground">
            {tr("repetitions", "重复次数")}
          </span>
          <Input
            id="memory-experiment-repetitions"
            type="number"
            min={1}
            max={10}
            value={repetitionText}
            onChange={(event) => setRepetitionText(event.target.value.replace(/[^\d]/g, ""))}
            onBlur={() => setRepetitionText(String(repetitions))}
          />
        </label>
        {metrics.some((metric) => metric.unit === "currency") ? (
          <p className="text-[11px] text-muted-foreground lg:col-span-2">
            {tr("costHint", "费用无单价依据时按实际计量显示，不填 0 元。")}
          </p>
        ) : null}
      </div>

      <fieldset className="space-y-3 rounded-panel border border-border/60 bg-settings-surface p-4">
        <legend className="text-sm font-semibold text-foreground">
          {tr("budget", "预算")}
        </legend>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <BudgetField
            id="memory-budget-max-trials"
            label={tr("maxTrials", "总试验预算")}
            min={1}
            value={budgets.max_trials}
            onChange={(value) => setBudgets((current) => ({ ...current, max_trials: value }))}
          />
          <BudgetField
            id="memory-budget-model-requests"
            label={tr("maxModelRequests", "模型请求预算")}
            value={budgets.max_model_requests}
            onChange={(value) => setBudgets((current) => ({ ...current, max_model_requests: value }))}
          />
          <BudgetField
            id="memory-budget-input-tokens"
            label={tr("maxInputTokens", "输入 token 预算")}
            value={budgets.max_input_tokens}
            onChange={(value) => setBudgets((current) => ({ ...current, max_input_tokens: value }))}
          />
          <BudgetField
            id="memory-budget-output-tokens"
            label={tr("maxOutputTokens", "输出 token 预算")}
            value={budgets.max_output_tokens}
            onChange={(value) => setBudgets((current) => ({ ...current, max_output_tokens: value }))}
          />
          <BudgetField
            id="memory-budget-cost"
            label={tr("maxTotalCost", "费用预算（可选）")}
            step={0.01}
            value={budgets.max_total_cost}
            onChange={(value) => setBudgets((current) => ({ ...current, max_total_cost: value }))}
          />
          <BudgetField
            id="memory-budget-trial-timeout"
            label={tr("perTrialTimeout", "单次试验超时（秒）")}
            min={1}
            max={900}
            value={budgets.per_trial_timeout_s}
            onChange={(value) => setBudgets((current) => ({ ...current, per_trial_timeout_s: value }))}
          />
          <BudgetField
            id="memory-budget-group-timeout"
            label={tr("groupTimeout", "分组超时（秒）")}
            min={1}
            value={budgets.group_timeout_s}
            onChange={(value) => setBudgets((current) => ({ ...current, group_timeout_s: value }))}
          />
          <BudgetField
            id="memory-budget-concurrency"
            label={tr("concurrency", "并发")}
            min={1}
            value={budgets.concurrency}
            onChange={(value) => setBudgets((current) => ({ ...current, concurrency: value }))}
          />
        </div>
        <div className="space-y-0.5 text-[12px] text-muted-foreground">
          <p>
            {tr("planBatches", "每候选 {{batches}} 批（每批 {{batchSize}} 个场景），共 {{runs}} 个子 run", {
              batches: batchPlan.batchesPerCandidate,
              batchSize: batchPlan.batchSize,
              runs: batchPlan.childRuns,
            })}
          </p>
        </div>
        {overBudget ? (
          <p role="alert" className="text-[12px] text-destructive">
            {tr("overBudget", "计划 {{trials}} 次试验超过总预算 {{budget}}，请显式调高预算或减少候选/重复次数。", {
              trials: plannedTrials,
              budget: maxTrials,
            })}
          </p>
        ) : null}
      </fieldset>
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <div className="space-y-2 rounded-panel border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-[12px] text-amber-700 dark:text-amber-300">
          <p className="font-medium">{tr("warnings", "提交前提示")}</p>
          <ul className="space-y-0.5">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
          <label className="flex items-center gap-2 text-foreground">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
            {tr("acknowledge", "我已了解以上评测限制")}
          </label>
        </div>
      ) : null}

      {blocking.length > 0 ? (
        <div role="alert" className="space-y-0.5 rounded-panel border border-destructive/20 bg-destructive/5 px-4 py-3 text-[12px] text-destructive">
          {blocking.map((item) => (
            <p key={item}>{item}</p>
          ))}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-[13px] text-destructive">
          {error}
        </p>
      ) : null}
      {localError ? (
        <p role="alert" className="text-[13px] text-destructive">
          {localError}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border/60 pt-4">
        {onCancel ? (
          <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onCancel}>
            {tr("cancel", "取消")}
          </Button>
        ) : null}
        <Button type="button" size="sm" disabled={!canSubmit || !onSubmit} onClick={() => void submit()}>
          {busy ? tr("submitting", "提交中…") : tr("submit", "开始测试")}
        </Button>
      </div>
    </section>
  );
}

function BudgetField({
  id,
  label,
  value,
  onChange,
  min,
  max,
  step,
}: {
  id: string;
  label: string;
  value: number | null;
  onChange: (value: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="space-y-1.5" htmlFor={id}>
      <span className="text-[12px] font-medium text-muted-foreground">{label}</span>
      <Input
        id={id}
        type="number"
        min={min}
        max={max}
        step={step}
        value={value ?? ""}
        onChange={(event) => onChange(toNumberOrNull(event.target.value))}
      />
    </label>
  );
}

const SELECT_CLASS =
  "h-9 w-full rounded-control border border-border/70 bg-background px-2.5 text-[13px] text-foreground";
