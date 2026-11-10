import { useTranslation } from "react-i18next";
import { Download, X } from "lucide-react";

import { Button } from "@/components/ui/button";

interface KnowledgeEvalManualProps {
  open: boolean;
  onClose: () => void;
  onDownloadTemplate: () => void;
}

const STEPS = [1, 2, 3, 4, 5] as const;

/**
 * 评测台使用指南（操作手册）。
 *
 * 只讲「怎么跑出第一份报告」与两个关键概念（关键词标注 / 模型判定），
 * 公式口径不在这里重复——那是报告详情里由后端下发的。
 */
export function KnowledgeEvalManual({
  open,
  onClose,
  onDownloadTemplate,
}: KnowledgeEvalManualProps) {
  const { t } = useTranslation();
  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="knowledge-eval-manual-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
    >
      <div className="flex max-h-[86vh] w-full max-w-[560px] flex-col rounded-panel border border-border bg-background shadow-2xl">
        <div className="flex items-start gap-3 border-b border-border/60 px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 id="knowledge-eval-manual-title" className="text-[15px] font-semibold text-foreground">
              {t("knowledge.eval.manual.title")}
            </h2>
            <p className="mt-1 text-[12px] text-muted-foreground">
              {t("knowledge.eval.manual.subtitle")}
            </p>
          </div>
          <button
            type="button"
            aria-label={t("knowledge.close")}
            onClick={onClose}
            className="shrink-0 cursor-pointer rounded-control p-1 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 text-[12.5px] leading-relaxed text-foreground">
          <ol className="flex flex-col gap-3">
            {STEPS.map((step) => (
              <li key={step} className="flex gap-2.5">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold tabular-nums text-primary">
                  {step}
                </span>
                <span className="min-w-0">
                  <span className="block text-[12.5px] font-semibold text-foreground">
                    {t(`knowledge.eval.manual.steps.${step}.title`)}
                  </span>
                  <span className="mt-0.5 block text-[12px] text-muted-foreground">
                    {t(`knowledge.eval.manual.steps.${step}.body`)}
                  </span>
                </span>
              </li>
            ))}
          </ol>

          <div className="mt-4 rounded-control border border-border/60 bg-muted/20 p-3">
            <p className="text-[12px] font-semibold text-foreground">
              {t("knowledge.eval.manual.conceptsTitle")}
            </p>
            <ul className="mt-1.5 flex list-disc flex-col gap-1 pl-4 text-[11.5px] text-muted-foreground">
              <li>{t("knowledge.eval.manual.conceptExpect")}</li>
              <li>{t("knowledge.eval.manual.conceptJudge")}</li>
            </ul>
          </div>

          <div className="mt-3 rounded-control border border-border/60 bg-muted/20 p-3">
            <p className="text-[12px] font-semibold text-foreground">
              {t("knowledge.eval.manual.templateTitle")}
            </p>
            <p className="mt-1 text-[11.5px] text-muted-foreground">
              {t("knowledge.eval.manual.templateBody")}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onDownloadTemplate}
              className="mt-2 h-8 gap-1.5 rounded-full px-3 text-[11.5px]"
            >
              <Download className="h-3.5 w-3.5" aria-hidden />
              {t("knowledge.eval.manual.download")}
            </Button>
          </div>
        </div>

        <div className="flex justify-end border-t border-border/60 px-5 py-3">
          <Button
            type="button"
            size="sm"
            onClick={onClose}
            className="h-8 rounded-full px-3.5 text-[12px] font-semibold"
          >
            {t("knowledge.eval.manual.close")}
          </Button>
        </div>
      </div>
    </div>
  );
}
