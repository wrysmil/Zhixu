import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import type { Article } from "./types";
import type { ArticleConflict } from "@/lib/writing-api";
import { ARTICLE_DELETED_REVISION } from "./use-article-save-flow";

export interface ConflictDialogProps {
  /** 解析自契约 §6 的冲突信息；`currentRevision === -1` 表示服务端已无这篇文章。 */
  conflict: ArticleConflict | null;
  /** 服务器最新版本；读不到（或已被删除）时为 null。 */
  latest: Article | null;
  /** 本地未保存的正文，冲突期间始终保留（A-08 第 4 条）。 */
  localBody: string;
  /** 远端版本正在读取中，用于区分「读不到」与「还没读完」。 */
  latestLoading?: boolean;
  onReloadLatest: () => void;
  onSaveCopy: () => Promise<void>;
  onDiscardLocal: () => Promise<void>;
  onCancel: () => void;
}

/**
 * A-08 的三条冲突处理路径：另存副本 / 以最新版本为基线手动合并 / 确认放弃本地修改后重新载入。
 * 刻意不提供「用本地稿强制覆盖服务端」——A-P0 不允许一键无条件覆盖。
 */
export function ConflictDialog({
  conflict,
  latest,
  localBody,
  latestLoading = false,
  onReloadLatest,
  onSaveCopy,
  onDiscardLocal,
  onCancel,
}: ConflictDialogProps) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState<"copy" | "discard" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDiscard, setConfirmingDiscard] = useState(false);

  const deleted = conflict?.currentRevision === ARTICLE_DELETED_REVISION;
  const revisionLabel = conflict && !deleted ? String(conflict.currentRevision) : "";

  const report = (reason: unknown) => {
    setError(reason instanceof Error ? reason.message : String(reason));
  };

  const handleReload = () => {
    setError(null);
    // 计划里的 `onReloadLatest: () => void`：调用方若返回 Promise，失败仍要能被看见。
    const outcome: unknown = onReloadLatest();
    if (outcome instanceof Promise) void outcome.catch(report);
  };

  const handleSaveCopy = async () => {
    setBusy("copy");
    setError(null);
    try {
      await onSaveCopy();
    } catch (reason) {
      report(reason);
    } finally {
      setBusy(null);
    }
  };

  const handleDiscard = async () => {
    setBusy("discard");
    setError(null);
    try {
      await onDiscardLocal();
    } catch (reason) {
      report(reason);
    } finally {
      setBusy(null);
    }
  };

  return (
    <AlertDialog open onOpenChange={(open) => (!open ? onCancel() : undefined)}>
      <AlertDialogContent
        data-testid="conflict-dialog"
        className="w-[min(calc(100vw-2rem),44rem)] gap-0 p-5"
      >
        <AlertDialogHeader>
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" aria-hidden />
            <div className="min-w-0">
              <AlertDialogTitle className="text-left text-[15px] font-semibold leading-snug text-foreground">
                {t("creative.conflict.title", { defaultValue: "其它位置已更新这篇文章" })}
              </AlertDialogTitle>
              <AlertDialogDescription className="text-left text-[12px] leading-relaxed">
                {deleted ? (
                  <span data-testid="conflict-deleted">
                    {t("creative.conflict.deleted", {
                      defaultValue: "这篇文章在服务端已不存在，可以把本地稿另存为一篇新文章。",
                    })}
                  </span>
                ) : (
                  t("creative.conflict.description", {
                    defaultValue: "服务器上的版本是 {{revision}}，本地未保存的内容已保留，自动保存已暂停。",
                    revision: revisionLabel,
                  })
                )}
              </AlertDialogDescription>
            </div>
          </div>
        </AlertDialogHeader>

        <div className={deleted ? "mt-4" : "mt-4 grid min-h-0 gap-3 sm:grid-cols-2"}>
          {!deleted ? (
            <section className="min-w-0">
              <h3 className="mb-1 text-[11px] font-medium text-muted-foreground">
                {t("creative.conflict.remote", { defaultValue: "服务器最新版本" })}
                {latest ? ` · v${latest.revision}` : ""}
              </h3>
              {latest ? (
                <pre
                  data-testid="conflict-remote"
                  className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-control border border-border bg-settings-surface p-2 text-[11px] leading-relaxed text-foreground"
                >
                  {latest.body}
                </pre>
              ) : (
                <p
                  data-testid="conflict-remote-unavailable"
                  className="rounded-control border border-dashed border-border p-2 text-[11px] text-muted-foreground"
                >
                  {latestLoading
                    ? t("creative.conflict.remoteLoading", { defaultValue: "正在读取服务器最新版本…" })
                    : t("creative.conflict.remoteUnavailable", { defaultValue: "暂时读取不到服务器最新版本。" })}
                </p>
              )}
            </section>
          ) : null}

          <section className="min-w-0">
            <h3 className="mb-1 text-[11px] font-medium text-muted-foreground">
              {t("creative.conflict.local", { defaultValue: "本地未保存内容" })}
            </h3>
            <pre
              data-testid="conflict-local"
              className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-control border border-border bg-background p-2 text-[11px] leading-relaxed text-foreground"
            >
              {localBody}
            </pre>
          </section>
        </div>

        {error ? (
          <p
            role="alert"
            data-testid="conflict-error"
            className="mt-3 break-words text-[11px] text-destructive"
          >
            {error}
          </p>
        ) : null}

        {confirmingDiscard ? (
          <p
            data-testid="conflict-discard-warning"
            className="mt-3 rounded-control border border-destructive/40 bg-destructive/5 px-2.5 py-2 text-[11px] leading-relaxed text-foreground"
          >
            {t("creative.conflict.discardWarning", {
              defaultValue: "本地草稿会被删除且无法找回，确认要重新载入服务器版本吗？",
            })}
          </p>
        ) : null}

        <AlertDialogFooter className="mt-5 grid grid-cols-1 gap-2 sm:flex sm:flex-row sm:justify-end">
          {confirmingDiscard ? (
            <>
              <Button
                variant="ghost"
                size="sm"
                data-testid="conflict-discard-back"
                onClick={() => setConfirmingDiscard(false)}
                disabled={busy !== null}
                className="h-7 text-[11px]"
              >
                {t("creative.conflict.discardBack", { defaultValue: "继续编辑" })}
              </Button>
              <Button
                size="sm"
                data-testid="conflict-discard-confirm"
                onClick={() => void handleDiscard()}
                disabled={busy !== null}
                className="h-7 bg-destructive text-[11px] text-destructive-foreground hover:bg-destructive/90"
              >
                {t("creative.conflict.discardConfirm", { defaultValue: "确认放弃本地修改" })}
              </Button>
            </>
          ) : (
            <>
              <AlertDialogCancel
                onClick={onCancel}
                data-testid="conflict-cancel"
                className="mt-0 h-7 w-auto min-w-0 text-[11px]"
              >
                {t("creative.conflict.cancel", { defaultValue: "稍后处理" })}
              </AlertDialogCancel>
              {!deleted ? (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    data-testid="conflict-reload"
                    onClick={handleReload}
                    className="h-7 cursor-pointer text-[11px]"
                  >
                    {t("creative.conflict.reload", { defaultValue: "查看最新版本并手动合并" })}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    data-testid="conflict-discard"
                    onClick={() => {
                      setError(null);
                      setConfirmingDiscard(true);
                    }}
                    className="h-7 cursor-pointer text-[11px]"
                  >
                    {t("creative.conflict.discard", { defaultValue: "放弃本地修改并重新载入" })}
                  </Button>
                </>
              ) : null}
              <Button
                size="sm"
                data-testid="conflict-save-copy"
                onClick={() => void handleSaveCopy()}
                disabled={busy !== null}
                className="h-7 text-[11px]"
              >
                {busy === "copy"
                  ? t("creative.conflict.savingCopy", { defaultValue: "正在另存副本…" })
                  : t("creative.conflict.saveCopy", { defaultValue: "另存副本" })}
              </Button>
            </>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
