import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

/** gold 样本人工确认状态；只有 confirmed 才是人工确认过的 gold。 */
export type CalibrationGoldStatus = "proposed" | "confirmed" | "rejected";

export type CalibrationSample = {
  id: string;
  case_id: string;
  case_name?: string | null;
  gold_status: CalibrationGoldStatus;
  agreement?: number | null;
  note?: string | null;
};

export type CalibrationState = {
  status: "not_started" | "running" | "ready" | "stale";
  fingerprint?: string | null;
  required_confirmed: number;
  confirmed_count: number;
  positive_confirmed: number;
  negative_confirmed: number;
  agreement_rate: number | null;
  agreement_threshold: number;
  samples: CalibrationSample[];
};

export type EvaluationCalibrationProps = {
  calibration: CalibrationState | null;
  busy?: boolean;
  /** 触发一次校准作业；未确认 gold 时按钮保持禁用。 */
  onRunCalibration?: () => void;
  /** 人工确认单个 gold 样例；组件不提供任何自动代签入口。 */
  onConfirmGold?: (sampleId: string) => void;
  className?: string;
};

const STATUS_KEY: Record<CalibrationState["status"], string> = {
  not_started: "settings.evaluation.reliability.calibration.statusNotStarted",
  running: "settings.evaluation.reliability.calibration.statusRunning",
  ready: "settings.evaluation.reliability.calibration.statusReady",
  stale: "settings.evaluation.reliability.calibration.statusStale",
};

const GOLD_KEY: Record<CalibrationGoldStatus, string> = {
  proposed: "settings.evaluation.reliability.calibration.goldProposed",
  confirmed: "settings.evaluation.reliability.calibration.goldConfirmed",
  rejected: "settings.evaluation.reliability.calibration.goldRejected",
};

export function EvaluationCalibration({
  calibration,
  busy = false,
  onRunCalibration,
  onConfirmGold,
  className,
}: EvaluationCalibrationProps) {
  const { t } = useTranslation();
  const base = "settings.evaluation.reliability.calibration.";

  const required = calibration?.required_confirmed ?? 0;
  const confirmed = calibration?.confirmed_count ?? 0;
  const pending = required > 0 && confirmed < required;
  const agreement = calibration?.agreement_rate ?? null;
  const threshold = calibration?.agreement_threshold ?? 0.8;
  const agreementLow = agreement != null && agreement < threshold;

  return (
    <section
      aria-label={t(base + "title")}
      className={cn("space-y-3 rounded-floating border border-border/60 bg-settings-surface p-3", className)}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-[13px] font-medium text-foreground">{t(base + "title")}</h3>
        <span
          data-testid="calibration-status"
          className="rounded-full border border-border/60 bg-muted/30 px-2 py-0.5 text-[11px] text-foreground"
        >
          {t(base + "status") + ": " + t(STATUS_KEY[calibration?.status ?? "not_started"])}
        </span>
      </header>

      <p className="text-[12px] text-muted-foreground">
        {t(base + "fingerprint") + ": " + (calibration?.fingerprint || t(base + "unknown"))}
      </p>

      <section aria-label={t(base + "goldTitle")} className="space-y-1.5">
        <h4 className="text-[13px] font-medium text-foreground">{t(base + "goldTitle")}</h4>
        <p data-testid="gold-progress" className="text-[12px] text-foreground">
          {t(base + "goldProgress", { confirmed, required })}
        </p>
        {pending ? (
          <p data-testid="gold-pending" className="text-[12px] font-medium text-amber-700 dark:text-amber-300">
            {t(base + "goldPending")}
          </p>
        ) : null}
        {required > 0 ? (
          <p className="text-[11px] text-muted-foreground">{t(base + "goldRequired", { required })}</p>
        ) : null}
        <p className="text-[11px] text-muted-foreground">
          {t(base + "positiveCount") + ": " + (calibration?.positive_confirmed ?? 0)}
          {" · "}
          {t(base + "negativeCount") + ": " + (calibration?.negative_confirmed ?? 0)}
        </p>

        {calibration?.samples.length ? (
          <ul className="space-y-1">
            {calibration.samples.map((sample) => (
              <li
                key={sample.id}
                className="flex flex-wrap items-center gap-2 rounded-floating border border-border/60 px-2 py-1 text-[12px]"
              >
                <span className="font-medium text-foreground">
                  {sample.case_name || sample.case_id}
                </span>
                <span data-testid={"gold-status-" + sample.id} className="text-muted-foreground">
                  {t(GOLD_KEY[sample.gold_status])}
                </span>
                {sample.gold_status === "proposed" && onConfirmGold ? (
                  <button
                    type="button"
                    className="ml-auto rounded border border-border px-2 py-0.5 text-[11px] text-foreground hover:bg-muted/40"
                    onClick={() => onConfirmGold(sample.id)}
                  >
                    {t(base + "confirmGold")}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p data-testid="no-samples" className="text-[12px] text-muted-foreground">
            {t(base + "noSamples")}
          </p>
        )}
      </section>

      <section aria-label={t(base + "agreement")} className="space-y-1">
        <h4 className="text-[13px] font-medium text-foreground">{t(base + "agreement")}</h4>
        <p data-testid="agreement-rate" className="text-[12px] text-foreground">
          {agreement == null ? t(base + "unknown") : String(Math.round(agreement * 100)) + "%"}
        </p>
        {agreementLow ? (
          <p data-testid="agreement-warning" className="text-[12px] text-amber-700 dark:text-amber-300">
            {t(base + "agreementThreshold", { threshold: Math.round(threshold * 100) })}
          </p>
        ) : null}
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy || pending || !onRunCalibration}
          className="rounded border border-border px-2 py-1 text-[12px] text-foreground disabled:opacity-50"
          onClick={() => onRunCalibration?.()}
        >
          {t(base + "runCalibration")}
        </button>
        {pending ? (
          <p data-testid="calibration-blocked" className="text-[11px] text-muted-foreground">
            {t(base + "runCalibrationHint")}
          </p>
        ) : null}
      </div>
    </section>
  );
}
