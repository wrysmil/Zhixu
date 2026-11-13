import { Bookmark, History } from "lucide-react";
import { useTranslation } from "react-i18next";

/** A shared explanation of the two existing memory stores, without new capabilities. */
export function MemoryGuide(props: { preview?: boolean }) {
  // Preview and live surfaces now share the same localized explanation.
  void props.preview;
  const { t } = useTranslation();

  return (
    <div className="assistant-panel assistant-panel-subtle flex items-start gap-5 px-5 py-5 sm:px-6 sm:py-6">
      <div className="min-w-0 max-w-[650px] flex-1">
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-primary">
          {t("settings.memory.guide.eyebrow")}
        </p>
        <h2 className="text-[18px] font-semibold leading-snug tracking-[-0.01em] text-foreground">
          {t("settings.memory.pageEyebrow")}
        </h2>
        <p className="mt-2 max-w-[50ch] text-[13px] leading-6 text-muted-foreground">
          {t("settings.memory.pageSubtitle")}
        </p>
        <dl className="mt-4 grid gap-4 text-[12px] leading-5 lg:grid-cols-2">
          <div className="flex items-start gap-2.5">
            <Bookmark className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
            <div>
              <dt className="font-medium text-foreground">{t("settings.memory.tabSemantic")}</dt>
              <dd className="mt-0.5 text-muted-foreground">{t("settings.memory.guide.semanticDescription")}</dd>
            </div>
          </div>
          <div className="flex items-start gap-2.5">
            <History className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
            <div>
              <dt className="font-medium text-foreground">{t("settings.memory.tabEpisode")}</dt>
              <dd className="mt-0.5 text-muted-foreground">{t("settings.memory.guide.episodeDescription")}</dd>
            </div>
          </div>
        </dl>
      </div>
    </div>
  );
}
