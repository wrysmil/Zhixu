import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { evaluationErrorText } from "@/components/settings/evaluation/evaluation-utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  evaluationApi,
  type EvaluationCase,
  type Profile,
  type Run,
  type Suite,
  type TargetKind,
} from "@/lib/evaluation-api";
import { cn } from "@/lib/utils";

const TARGET_KINDS: TargetKind[] = ["agent", "team", "skill", "dataset"];

export function NewRunDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (run: Run) => void;
}) {
  const { t } = useTranslation();
  const [suites, setSuites] = useState<Suite[]>([]);
  const [cases, setCases] = useState<EvaluationCase[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [suiteId, setSuiteId] = useState("");
  const [selectedCaseIds, setSelectedCaseIds] = useState<string[]>([]);
  const [targetKind, setTargetKind] = useState<TargetKind>("agent");
  const [targetId, setTargetId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [judgeId, setJudgeId] = useState("");
  const [noLimit, setNoLimit] = useState(false);
  const [timeoutS, setTimeoutS] = useState(600);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setSubmitError(null);
    setTargetKind("agent");
    setTargetId("");
    setJudgeId("");
    setNoLimit(false);
    setTimeoutS(600);
    void (async () => {
      try {
        const [suitePage, profilePage] = await Promise.all([
          evaluationApi.listSuites(),
          evaluationApi.listProfiles(),
        ]);
        if (cancelled) return;
        setSuites(suitePage.data);
        setProfiles(profilePage.data);
        setSuiteId(suitePage.data[0]?.id ?? "");
        setProfileId(profilePage.data.find((profile) => profile.kind !== "judge")?.id ?? "");
      } catch (error) {
        if (!cancelled) setSubmitError(evaluationErrorText(error, t));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, t]);

  useEffect(() => {
    if (!open || !suiteId) return;
    let cancelled = false;
    void (async () => {
      try {
        const casePage = await evaluationApi.listCases(suiteId);
        if (cancelled) return;
        setCases(casePage.data);
        setSelectedCaseIds(casePage.data.filter((item) => item.enabled).map((item) => item.id));
      } catch (error) {
        if (!cancelled) setSubmitError(evaluationErrorText(error, t));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, suiteId, t]);

  const modelProfiles = profiles.filter((profile) => profile.kind !== "judge");
  const judgeProfiles = profiles.filter((profile) => profile.kind !== "agent");
  const canSubmit = Boolean(
    suiteId && selectedCaseIds.length > 0 && profileId && (targetKind === "dataset" || targetId.trim()),
  );

  const submit = async () => {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const run = await evaluationApi.createRun({
        suite_id: suiteId,
        case_ids: selectedCaseIds,
        agent_profile_id: profileId,
        judge_profile_id: judgeId === "" ? null : judgeId,
        target: {
          kind: targetKind,
          id: targetKind === "dataset" ? suiteId : targetId.trim(),
        },
        run_timeout_s: noLimit ? null : timeoutS,
      });
      onCreated?.(run);
      onOpenChange(false);
    } catch (error) {
      setSubmitError(evaluationErrorText(error, t));
    } finally {
      setSubmitting(false);
    }
  };

  const targetLabels: Record<TargetKind, string> = {
    agent: t("settings.evaluation.newRun.targetAgent"),
    team: t("settings.evaluation.newRun.targetTeam"),
    skill: t("settings.evaluation.newRun.targetSkill"),
    dataset: t("settings.evaluation.newRun.targetDataset"),
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("settings.evaluation.newRun.title")}</DialogTitle>
          <DialogDescription>{t("settings.evaluation.subtitle")}</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <div className="grid grid-cols-2 gap-3">
            <Field label={t("settings.evaluation.newRun.target")} id="eval-newrun-target-kind">
              <select
                id="eval-newrun-target-kind"
                value={targetKind}
                onChange={(event) => setTargetKind(event.target.value as TargetKind)}
                className={SELECT_CLASS}
              >
                {TARGET_KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {targetLabels[kind]}
                  </option>
                ))}
              </select>
            </Field>
            {targetKind === "dataset" ? (
              <Field label={t("settings.evaluation.newRun.dataset")} id="eval-newrun-dataset-target">
                <select
                  id="eval-newrun-dataset-target"
                  value={suiteId}
                  onChange={(event) => setSuiteId(event.target.value)}
                  className={SELECT_CLASS}
                >
                  {suites.map((suite) => (
                    <option key={suite.id} value={suite.id}>
                      {suite.name}
                    </option>
                  ))}
                </select>
              </Field>
            ) : (
              <Field label={t("settings.evaluation.newRun.targetId")} id="eval-newrun-target-id">
                <Input
                  id="eval-newrun-target-id"
                  value={targetId}
                  onChange={(event) => setTargetId(event.target.value)}
                  placeholder="my-agent"
                />
              </Field>
            )}
          </div>

          <Field label={t("settings.evaluation.newRun.dataset")} id="eval-newrun-suite">
            <select
              id="eval-newrun-suite"
              value={suiteId}
              onChange={(event) => setSuiteId(event.target.value)}
              className={SELECT_CLASS}
            >
              {suites.map((suite) => (
                <option key={suite.id} value={suite.id}>
                  {suite.name}
                </option>
              ))}
            </select>
          </Field>

          {cases.length > 0 ? (
            <fieldset className="space-y-1.5">
              <legend className="text-[12px] font-medium text-muted-foreground">
                {t("settings.evaluation.newRun.cases")}
              </legend>
              <div className="max-h-40 space-y-1 overflow-y-auto rounded-control border border-border/60 p-2">
                {cases.map((item) => (
                  <label
                    key={item.id}
                    className="flex items-center gap-2 rounded-control px-1 py-0.5 text-[13px] text-foreground"
                  >
                    <input
                      type="checkbox"
                      checked={selectedCaseIds.includes(item.id)}
                      onChange={(event) => {
                        setSelectedCaseIds((ids) =>
                          event.target.checked
                            ? [...ids, item.id]
                            : ids.filter((id) => id !== item.id),
                        );
                      }}
                      className="h-4 w-4"
                    />
                    <span className={cn(!item.enabled && "text-muted-foreground")}>{item.name}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          ) : null}

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("settings.evaluation.newRun.model")} id="eval-newrun-model">
              <select
                id="eval-newrun-model"
                value={profileId}
                onChange={(event) => setProfileId(event.target.value)}
                className={SELECT_CLASS}
              >
                {modelProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.label} ({profile.model})
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("settings.evaluation.newRun.judge")} id="eval-newrun-judge">
              <select
                id="eval-newrun-judge"
                value={judgeId}
                onChange={(event) => setJudgeId(event.target.value)}
                className={SELECT_CLASS}
              >
                <option value="">{t("settings.evaluation.newRun.judgeNone")}</option>
                {judgeProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.label} ({profile.model})
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <div className="flex items-end gap-4">
            <label className="flex items-center gap-2 pb-2 text-[13px] text-foreground">
              <input
                type="checkbox"
                checked={noLimit}
                onChange={(event) => setNoLimit(event.target.checked)}
                className="h-4 w-4"
              />
              {t("settings.evaluation.newRun.noLimit")}
            </label>
            <Field
              label={t("settings.evaluation.newRun.timeLimitSeconds")}
              id="eval-newrun-timeout"
            >
              <Input
                id="eval-newrun-timeout"
                type="number"
                min={5}
                disabled={noLimit}
                value={timeoutS}
                onChange={(event) => {
                  const parsed = Number.parseInt(event.target.value, 10);
                  setTimeoutS(Number.isFinite(parsed) ? parsed : 0);
                }}
                className="w-28"
              />
            </Field>
          </div>

          <DialogFooter>
            {submitError ? (
              <p role="alert" className="mr-auto text-[13px] text-destructive">
                {submitError}
              </p>
            ) : null}
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              {t("settings.evaluation.newRun.cancel")}
            </Button>
            <Button type="submit" disabled={!canSubmit || submitting}>
              {t("settings.evaluation.newRun.submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

const SELECT_CLASS =
  "h-9 w-full rounded-control border border-border/70 bg-background px-2.5 text-[13px] text-foreground";

function Field({
  label,
  id,
  children,
}: {
  label: string;
  id: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-[12px] font-medium text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}
