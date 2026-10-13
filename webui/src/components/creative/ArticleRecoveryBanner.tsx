import { useTranslation } from "react-i18next";
import { History } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Article } from "./types";
import type { LocalArticleDraft } from "@/lib/writing-draft-cache";

export interface ArticleRecoveryBannerProps {
  /** Server-side article we just opened. Used to compute the version delta. */
  article: Article;
  /** Cached recovery draft, or null if the local cache had nothing for this article. */
  recovery: LocalArticleDraft | null;
  /**
   * When true, the static "recovery unavailable" notice is rendered regardless
   * of whether a draft exists. The banner itself can still appear above it.
   */
  storageUnhealthy: boolean;
  onRecover: () => void;
  onDiscard: () => void;
}

/**
 * A-07 重开恢复版（spec §A-07；plan A3.6）。
 *
 * 显示的三个不变量：
 * - 默认不自动覆盖编辑器（spec A-06 末条：本地草稿只是恢复用，不能当保存成功的依据）；
 * - 服务端版本已变时给一条次级提示，避免用户误以为恢复就是落盘；
 * - 本地缓存不可用时显示静态提示，导出与保存动作不受影响。
 */
export function ArticleRecoveryBanner({
  article,
  recovery,
  storageUnhealthy,
  onRecover,
  onDiscard,
}: ArticleRecoveryBannerProps) {
  const { t } = useTranslation();

  if (!recovery) {
    if (!storageUnhealthy) return null;
    return (
      <div
        data-testid="recovery-storage-hint"
        role="status"
        className="flex items-center justify-between gap-2 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-[11px] text-amber-800 dark:text-amber-200"
      >
        <span className="min-w-0 truncate">
          {t("creative.recovery.storageUnavailable", {
            defaultValue: "本地恢复保障不可用，保存与导出仍可继续。",
          })}
        </span>
      </div>
    );
  }

  const serverRevision = article.revision;
  const baseRevision = recovery.baseRevision;
  const serverMovedOn = serverRevision > baseRevision;

  return (
    <div
      data-testid="article-recovery-banner"
      role="status"
      className="flex flex-wrap items-start justify-between gap-3 border-b border-amber-500/40 bg-amber-500/10 px-4 py-3 text-[12px] text-amber-900 dark:text-amber-100"
    >
      <div className="flex min-w-0 items-start gap-2">
        <History className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-300" aria-hidden />
        <div className="min-w-0">
          <p className="font-medium leading-snug">
            {t("creative.recovery.bannerTitle", {
              defaultValue: "本地有未保存的草稿",
            })}
          </p>
          <p
            data-testid="recovery-server-warning"
            className="mt-1 break-words text-[11px] leading-relaxed text-amber-800 dark:text-amber-200"
          >
            {serverMovedOn
              ? t("creative.recovery.bannerServerNewer", {
                      defaultValue: "草稿基于版本 {{baseRevision}}；服务器已是 {{serverRevision}}。",
                      baseRevision,
                      serverRevision,
                    })
              : t("creative.recovery.bannerServerSame", {
                      defaultValue: "草稿基于版本 {{baseRevision}}，与服务端一致。",
                      baseRevision,
                    })}
          </p>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          data-testid="recovery-discard"
          onClick={onDiscard}
          className="h-7 cursor-pointer text-[11px]"
        >
          {t("creative.recovery.discard", {
            defaultValue: "放弃恢复稿",
          })}
        </Button>
        <Button
          size="sm"
          data-testid="recovery-recover"
          onClick={onRecover}
          className="h-7 text-[11px]"
        >
          {t("creative.recovery.recover", {
            defaultValue: "恢复草稿继续",
          })}
        </Button>
      </div>
    </div>
  );
}