import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** 契约 §1：事件判别闭集。 */
export type MemoryScenarioEventType =
  | "session"
  | "settle"
  | "checkpoint"
  | "restore"
  | "restart"
  | "forget"
  | "rebuild";

export type MemoryScenarioMessageView = {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  at: string;
  text: string;
};

export type MemoryScenarioEventView = {
  type: MemoryScenarioEventType;
  /** session 事件必填；其余事件可空。 */
  id?: string | null;
  at?: string | null;
  messages?: MemoryScenarioMessageView[];
  /** 事件摘要，例如 settle.after_session / forget.target。 */
  summary?: string | null;
};

export type MemoryScenarioFactView = {
  key: string;
  value: string;
  subject?: string | null;
  validFrom?: string | null;
  validTo?: string | null;
  evidenceMessageIds?: string[];
};

export type MemoryScenarioProbeView = {
  id: string;
  question: string;
  at: string;
  answerability: "answerable" | "unanswerable";
  expectedAbsence?: boolean;
  requiredFacts: MemoryScenarioFactView[];
  forbiddenCurrentValues?: MemoryScenarioFactView[];
  allowedEvidenceMessageIds?: string[];
};

export type MemoryScenarioGoldView = {
  annotationScope: "closed_world" | "targeted";
  extractionFactCount?: number;
  stateExpectationCount?: number;
};

export type MemoryScenarioView = {
  scenarioId: string;
  revision: number;
  split: "development" | "calibration" | "regression";
  clusterId?: string | null;
  tags?: string[];
  events: MemoryScenarioEventView[];
  probes: MemoryScenarioProbeView[];
  gold: MemoryScenarioGoldView;
  /** 场景含受控脚本时为只读；编辑器不提供脚本编辑。 */
  readOnly?: boolean;
};

/**
 * 契约 §1/§8：只允许这些字段编辑。事件、消息、system 角色、脚本与任意代码
 * 不在白名单内，界面也不提供对应输入。
 */
export const MEMORY_SCENARIO_EDITABLE_FIELDS = [
  "tags",
  "probes[].question",
  "probes[].answerability",
  "probes[].expected_absence",
  "probes[].required_facts[].value",
  "probes[].allowed_evidence_message_ids",
] as const;

export type MemoryScenarioProbeEdit = {
  id: string;
  question?: string;
  answerability?: "answerable" | "unanswerable";
  expected_absence?: boolean;
  required_facts?: Array<{ key: string; value: string }>;
  allowed_evidence_message_ids?: string[];
};

export type MemoryScenarioEdit = {
  scenario_id: string;
  base_revision: number;
  tags?: string[];
  probes?: MemoryScenarioProbeEdit[];
};

export type MemoryScenarioRevisionResult = {
  revision: number;
  datasetRevisionId?: string;
};

export type MemoryScenarioEditorProps = {
  scenario: MemoryScenarioView;
  evidenceOptions?: string[];
  saving?: boolean;
  error?: string | null;
  onSaveRevision?: (edit: MemoryScenarioEdit) => Promise<MemoryScenarioRevisionResult | void>;
  onCancel?: () => void;
};

type ProbeDraft = {
  question: string;
  answerability: "answerable" | "unanswerable";
  expectedAbsence: boolean;
  factValues: Record<string, string>;
  evidence: string[];
};

function buildDrafts(scenario: MemoryScenarioView): Record<string, ProbeDraft> {
  const drafts: Record<string, ProbeDraft> = {};
  for (const probe of scenario.probes) {
    drafts[probe.id] = {
      question: probe.question,
      answerability: probe.answerability,
      expectedAbsence: Boolean(probe.expectedAbsence),
      factValues: Object.fromEntries(probe.requiredFacts.map((fact) => [fact.key, fact.value])),
      evidence: [...(probe.allowedEvidenceMessageIds ?? [])].sort(),
    };
  }
  return drafts;
}

function sameIds(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const sortedLeft = [...left].sort();
  const sortedRight = [...right].sort();
  return sortedLeft.every((value, index) => value === sortedRight[index]);
}

export function MemoryScenarioEditor({
  scenario,
  evidenceOptions,
  saving = false,
  error = null,
  onSaveRevision,
  onCancel,
}: MemoryScenarioEditorProps) {
  const { t } = useTranslation();
  const tr = (key: string, defaultValue: string, options?: Record<string, unknown>) =>
    t(`settings.evaluation.memory.scenario.${key}`, { defaultValue, ...options });

  const [tagsText, setTagsText] = useState((scenario.tags ?? []).join(", "));
  const [drafts, setDrafts] = useState<Record<string, ProbeDraft>>(() => buildDrafts(scenario));
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedRevision, setSavedRevision] = useState<number | null>(null);

  // 只在场景身份（id + revision）变化时重置草稿：父组件每次渲染重建对象时不能
  // 清掉用户正在编辑的内容。
  useEffect(() => {
    setTagsText((scenario.tags ?? []).join(", "));
    setDrafts(buildDrafts(scenario));
    setSaveError(null);
    setSavedRevision(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenario.scenarioId, scenario.revision]);

  const evidenceList = useMemo(() => {
    if (evidenceOptions) return evidenceOptions;
    const ids: string[] = [];
    for (const event of scenario.events) {
      for (const message of event.messages ?? []) ids.push(message.id);
    }
    return ids;
  }, [evidenceOptions, scenario.events]);

  const edit = useMemo<MemoryScenarioEdit | null>(() => {
    if (scenario.readOnly) return null;
    const originalTags = [...(scenario.tags ?? [])].sort().join(",");
    const nextTags = tagsText
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean);
    const payload: MemoryScenarioEdit = {
      scenario_id: scenario.scenarioId,
      base_revision: scenario.revision,
    };
    if (nextTags.sort().join(",") !== originalTags) payload.tags = nextTags;

    const probes: MemoryScenarioProbeEdit[] = [];
    for (const probe of scenario.probes) {
      const draft = drafts[probe.id];
      if (!draft) continue;
      const probeEdit: MemoryScenarioProbeEdit = { id: probe.id };
      let changed = false;
      if (draft.question !== probe.question) {
        probeEdit.question = draft.question;
        changed = true;
      }
      if (draft.answerability !== probe.answerability) {
        probeEdit.answerability = draft.answerability;
        changed = true;
      }
      if (draft.expectedAbsence !== Boolean(probe.expectedAbsence)) {
        probeEdit.expected_absence = draft.expectedAbsence;
        changed = true;
      }
      const nextFacts = probe.requiredFacts.map((fact) => ({
        key: fact.key,
        value: draft.factValues[fact.key] ?? fact.value,
      }));
      if (nextFacts.some((fact, index) => fact.value !== probe.requiredFacts[index]?.value)) {
        probeEdit.required_facts = nextFacts;
        changed = true;
      }
      if (!sameIds(draft.evidence, probe.allowedEvidenceMessageIds ?? [])) {
        probeEdit.allowed_evidence_message_ids = [...draft.evidence].sort();
        changed = true;
      }
      if (changed) probes.push(probeEdit);
    }
    if (probes.length > 0) payload.probes = probes;
    if (!payload.tags && !payload.probes) return null;
    return payload;
  }, [drafts, scenario, tagsText]);

  const changeCount =
    (edit?.tags ? 1 : 0) +
    (edit?.probes ?? []).reduce((total, probe) => {
      let count = 0;
      if (probe.question !== undefined) count += 1;
      if (probe.answerability !== undefined) count += 1;
      if (probe.expected_absence !== undefined) count += 1;
      if (probe.required_facts) count += probe.required_facts.length;
      if (probe.allowed_evidence_message_ids) count += 1;
      return total + count;
    }, 0);

  const save = async () => {
    if (!edit || !onSaveRevision || saving) return;
    setSaveError(null);
    try {
      const result = await onSaveRevision(edit);
      setSavedRevision(result && typeof result.revision === "number" ? result.revision : scenario.revision + 1);
    } catch (saveFailure) {
      setSaveError(
        saveFailure instanceof Error
          ? saveFailure.message
          : tr("saveFailed", "保存失败，请重试"),
      );
    }
  };

  const updateDraft = (probeId: string, patch: Partial<ProbeDraft>) => {
    setDrafts((current) => ({ ...current, [probeId]: { ...current[probeId], ...patch } }));
  };

  return (
    <section aria-label={tr("title", "场景编辑")} className="min-w-0 space-y-5">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold text-foreground">
          {scenario.scenarioId}
          <span className="ml-2 text-[12px] font-normal text-muted-foreground">
            {tr("revision", "版本 {{revision}}", { revision: scenario.revision })}
          </span>
        </h3>
        <span className="rounded-full border border-border/60 bg-muted/30 px-2 py-0.5 text-[11px] text-muted-foreground">
          {scenario.split}
        </span>
        {scenario.clusterId ? (
          <span className="text-[11px] text-muted-foreground">cluster: {scenario.clusterId}</span>
        ) : null}
        {scenario.readOnly ? (
          <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-300">
            {tr("readonly", "只读")}
          </span>
        ) : null}
      </header>

      <div className="space-y-1.5">
        <label htmlFor="memory-scenario-tags" className="text-[12px] font-medium text-muted-foreground">
          {tr("tags", "标签")}
        </label>
        <Input
          id="memory-scenario-tags"
          value={tagsText}
          disabled={scenario.readOnly}
          onChange={(event) => setTagsText(event.target.value)}
          placeholder={tr("tagsPlaceholder", "多个标签用逗号分隔")}
        />
      </div>

      <div className="space-y-2">
        <h4 className="text-sm font-semibold text-foreground">{tr("events", "发生了什么")}</h4>
        <p className="text-[12px] text-muted-foreground">
          {tr(
            "eventsReadonly",
            "会话事件与发生时间为只读；不提供脚本或任意代码编辑。",
          )}
        </p>
        <ol className="space-y-2">
          {scenario.events.map((event, index) => (
            <li
              key={`${event.type}-${event.id ?? index}`}
              className="space-y-1 rounded-control border border-border/60 bg-muted/20 px-3 py-2 text-[13px]"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full border border-border/60 bg-background px-2 py-0.5 text-[11px] font-medium text-foreground">
                  {tr(`event.${event.type}`, event.type)}
                </span>
                <span className="text-[12px] text-muted-foreground">{event.at ?? "—"}</span>
                {event.id ? (
                  <span className="text-[11px] text-muted-foreground">#{event.id}</span>
                ) : null}
                {event.summary ? (
                  <span className="text-[11px] text-muted-foreground">{event.summary}</span>
                ) : null}
              </div>
              {event.messages && event.messages.length > 0 ? (
                <ul className="space-y-0.5">
                  {event.messages.map((message) => (
                    <li key={message.id} className="text-[12px] text-foreground">
                      <span className="font-medium">{message.role}</span>
                      <span className="text-muted-foreground"> · {message.at} · {message.id}</span>
                      <p className="break-words text-muted-foreground">{message.text}</p>
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ol>
      </div>

      <div className="space-y-2">
        <h4 className="text-sm font-semibold text-foreground">{tr("probes", "应该怎么答")}</h4>
        <p className="text-[12px] text-muted-foreground">
          {tr("goldPrivate", "gold 只用于评分，不会进入被测 Agent 的执行输入。")}
        </p>
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
          <span className="rounded-full border border-border/60 bg-muted/30 px-2 py-0.5">
            {scenario.gold.annotationScope === "closed_world"
              ? tr("closedWorld", "完整抽取集合")
              : tr("targeted", "定向标注")}
          </span>
          {typeof scenario.gold.extractionFactCount === "number" ? (
            <span>{tr("extractionFacts", "抽取目标 {{count}} 条", { count: scenario.gold.extractionFactCount })}</span>
          ) : null}
          {typeof scenario.gold.stateExpectationCount === "number" ? (
            <span>{tr("stateExpectations", "状态检查 {{count}} 条", { count: scenario.gold.stateExpectationCount })}</span>
          ) : null}
        </div>

        <ul className="space-y-3">
          {scenario.probes.map((probe) => {
            const draft = drafts[probe.id];
            if (!draft) return null;
            return (
              <li key={probe.id} className="space-y-3 rounded-panel border border-border/60 bg-settings-surface p-3">
                <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  <span className="font-medium text-foreground">{probe.id}</span>
                  <span>{tr("probeAt", "提问时间 {{at}}", { at: probe.at })}</span>
                </div>
                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
                  <label className="space-y-1.5" htmlFor={`memory-probe-question-${probe.id}`}>
                    <span className="text-[12px] font-medium text-muted-foreground">
                      {tr("question", "测试问题")}
                    </span>
                    <Input
                      id={`memory-probe-question-${probe.id}`}
                      value={draft.question}
                      disabled={scenario.readOnly}
                      onChange={(event) => updateDraft(probe.id, { question: event.target.value })}
                    />
                  </label>
                  <label className="space-y-1.5" htmlFor={`memory-probe-answerability-${probe.id}`}>
                    <span className="text-[12px] font-medium text-muted-foreground">
                      {tr("answerability", "可回答性")}
                    </span>
                    <select
                      id={`memory-probe-answerability-${probe.id}`}
                      className={SELECT_CLASS}
                      value={draft.answerability}
                      disabled={scenario.readOnly}
                      onChange={(event) =>
                        updateDraft(probe.id, {
                          answerability: event.target.value as ProbeDraft["answerability"],
                        })
                      }
                    >
                      <option value="answerable">{tr("answerable", "可回答")}</option>
                      <option value="unanswerable">{tr("unanswerable", "信息不足")}</option>
                    </select>
                  </label>
                </div>
                <label className="flex items-center gap-2 text-[12px] text-foreground">
                  <input
                    type="checkbox"
                    className="h-4 w-4"
                    checked={draft.expectedAbsence}
                    disabled={scenario.readOnly}
                    onChange={(event) => updateDraft(probe.id, { expectedAbsence: event.target.checked })}
                  />
                  {tr("expectedAbsence", "预期不存在（应承认信息不足）")}
                </label>

                <fieldset className="space-y-1.5">
                  <legend className="text-[12px] font-medium text-muted-foreground">
                    {tr("requiredFacts", "应记住的事实（gold）")}
                  </legend>
                  {probe.requiredFacts.length === 0 ? (
                    <p className="text-[12px] text-muted-foreground">{tr("noFacts", "没有必需事实")}</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {probe.requiredFacts.map((fact) => (
                        <li key={fact.key} className="flex flex-wrap items-center gap-2">
                          <span className="min-w-28 text-[12px] text-muted-foreground">{fact.key}</span>
                          <Input
                            aria-label={tr("factValue", "事实值 {{key}}", { key: fact.key })}
                            className="max-w-sm"
                            value={draft.factValues[fact.key] ?? fact.value}
                            disabled={scenario.readOnly}
                            onChange={(event) =>
                              updateDraft(probe.id, {
                                factValues: { ...draft.factValues, [fact.key]: event.target.value },
                              })
                            }
                          />
                          {fact.evidenceMessageIds && fact.evidenceMessageIds.length > 0 ? (
                            <span className="text-[11px] text-muted-foreground">
                              {tr("factEvidence", "证据 {{ids}}", {
                                ids: fact.evidenceMessageIds.join(", "),
                              })}
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </fieldset>

                {probe.forbiddenCurrentValues && probe.forbiddenCurrentValues.length > 0 ? (
                  <div className="space-y-1">
                    <p className="text-[12px] font-medium text-muted-foreground">
                      {tr("forbidden", "禁止出现的当前值")}
                    </p>
                    <ul className="space-y-0.5 text-[12px] text-foreground">
                      {probe.forbiddenCurrentValues.map((fact) => (
                        <li key={fact.key}>
                          <span className="text-muted-foreground">{fact.key}</span>: {fact.value}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <fieldset className="space-y-1.5">
                  <legend className="text-[12px] font-medium text-muted-foreground">
                    {tr("evidence", "来源证据")}
                  </legend>
                  {evidenceList.length === 0 ? (
                    <p className="text-[12px] text-muted-foreground">{tr("noEvidence", "没有可选证据")}</p>
                  ) : (
                    <div className="flex flex-wrap gap-x-4 gap-y-1">
                      {evidenceList.map((messageId) => (
                        <label key={messageId} className="flex items-center gap-1.5 text-[12px] text-foreground">
                          <input
                            type="checkbox"
                            className="h-4 w-4"
                            checked={draft.evidence.includes(messageId)}
                            disabled={scenario.readOnly}
                            onChange={(event) =>
                              updateDraft(probe.id, {
                                evidence: event.target.checked
                                  ? [...draft.evidence, messageId]
                                  : draft.evidence.filter((id) => id !== messageId),
                              })
                            }
                          />
                          {messageId}
                        </label>
                      ))}
                    </div>
                  )}
                </fieldset>
              </li>
            );
          })}
        </ul>
      </div>

      {error ? (
        <p role="alert" className="text-[13px] text-destructive">
          {error}
        </p>
      ) : null}
      {saveError ? (
        <p role="alert" className="text-[13px] text-destructive">
          {saveError}
        </p>
      ) : null}
      {savedRevision !== null ? (
        <p role="status" className="text-[13px] text-emerald-700 dark:text-emerald-300">
          {tr("saved", "已生成 revision {{revision}}", { revision: savedRevision })}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-3 border-t border-border/60 pt-4">
        <p className="mr-auto text-[12px] text-muted-foreground">
          {changeCount > 0
            ? tr("dirty", "有 {{count}} 处修改", { count: changeCount })
            : tr("noChanges", "没有修改")}
          {" · "}
          {tr("newRevisionHint", "保存会生成新 revision，历史实验仍使用旧版本。")}
        </p>
        {onCancel ? (
          <Button type="button" variant="ghost" size="sm" disabled={saving} onClick={onCancel}>
            {tr("cancel", "取消")}
          </Button>
        ) : null}
        <Button
          type="button"
          size="sm"
          disabled={!edit || saving || scenario.readOnly || !onSaveRevision}
          onClick={() => void save()}
        >
          {saving ? tr("saving", "保存中…") : tr("save", "保存为新版本")}
        </Button>
      </div>
    </section>
  );
}

const SELECT_CLASS =
  "h-9 w-full rounded-control border border-border/70 bg-background px-2.5 text-[13px] text-foreground";
