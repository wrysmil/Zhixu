import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ChevronRight,
  Info,
  Loader2,
  RefreshCcw,
  Save,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { PageHeading } from "@/components/workspace/PageHeading";
import {
  fetchIdentityFile,
  getActivePersona,
  listIdentityFiles,
  reloadIdentity,
  saveIdentityFile,
  setActivePersona,
} from "@/lib/api";
import type { IdentityFileEntry } from "@/lib/types";
import { notify } from "@/lib/toast-events";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";

/**
 * 身份配置页：布局与交互照抄 openakita 的 setup-center 身份视图
 * （左侧源文件清单 + 右侧工具栏/警告条/编辑器/状态栏）。
 *
 * 差异只在两处：文件清单按知序实际存在的身份文件渲染（见
 * `nanobot/identity/catalog.py`），高风险确认在前端本地完成——
 * 知序没有 openakita 那套 validate 端点。
 */

const BANNER_STORAGE_KEY = "zhixu.identity.banner-dismissed";

/** 改动后直接影响每次对话的文件：保存前要二次确认。 */
const HIGH_RISK_FILES = new Set(["SOUL.md", "AGENT.md", "AGENTS.md"]);

/** 每个文件的提示条文案键（openakita 的 WARNING_KEYS 同款口径）。 */
const WARNING_KEYS: Record<string, string> = {
  "SOUL.md": "soulWarning",
  "AGENT.md": "agentWarning",
  "USER.md": "userWarning",
  "MEMORY.md": "memoryWarning",
  "prompts/policies.md": "policiesMdWarning",
};

/** 粗估 token 数：CJK 按 1.5 字/token，其余按 4 字符/token。 */
function estimateTokens(text: string): number {
  if (!text) return 0;
  let cjk = 0;
  for (const ch of text) {
    if (ch >= "一" && ch <= "鿿") cjk += 1;
  }
  return Math.round(cjk / 1.5 + (text.length - cjk) / 4);
}

function diffLines(original: string, modified: string): { added: number; removed: number } {
  const before = new Set(original.split("\n"));
  const after = new Set(modified.split("\n"));
  let added = 0;
  let removed = 0;
  for (const line of modified.split("\n")) if (!before.has(line)) added += 1;
  for (const line of original.split("\n")) if (!after.has(line)) removed += 1;
  return { added, removed };
}

export function IdentityPage() {
  const { t } = useTranslation();
  const { client, token } = useClient();

  const [files, setFiles] = useState<IdentityFileEntry[]>([]);
  const [charLimit, setCharLimit] = useState(1500);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activePersona, setActivePersonaState] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [bannerDismissed, setBannerDismissed] = useState(() => {
    try {
      return window.localStorage.getItem(BANNER_STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });

  const refreshList = useCallback(async () => {
    const payload = await listIdentityFiles(token);
    setFiles(payload.files);
    setCharLimit(payload.charLimit);
    return payload.files;
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    refreshList()
      .then((list) => {
        if (cancelled) return;
        setSelectedName((prev) =>
          prev && list.some((file) => file.name === prev) ? prev : list[0]?.name ?? null,
        );
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [refreshList]);

  useEffect(() => {
    let cancelled = false;
    getActivePersona(token)
      .then((payload) => {
        if (cancelled) return;
        setActivePersonaState(payload.active ?? "");
      })
      .catch(() => {
        /* 人格切换是可选能力，取不到就不显示当前标记 */
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    if (!selectedName) {
      setContent("");
      setOriginal("");
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchIdentityFile(token, selectedName)
      .then((payload) => {
        if (cancelled) return;
        setContent(payload.content ?? "");
        setOriginal(payload.content ?? "");
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, selectedName]);

  const core = useMemo(() => files.filter((file) => file.group === "core"), [files]);
  const personas = useMemo(() => files.filter((file) => file.group === "personas"), [files]);
  const selected = useMemo(
    () => files.find((file) => file.name === selectedName) ?? null,
    [files, selectedName],
  );

  const hasChanges = content !== original;
  const warningKey = selectedName ? WARNING_KEYS[selectedName] : undefined;
  const tokenCount = estimateTokens(content);
  // 每个文件的字符上限由后端按文件下发（SOUL.md / AGENT.md 的出厂基线比
  // MEMORY.md 之类的派生文件长得多），取不到时回退到全局默认值。
  const effectiveCharLimit = selected?.charLimit ?? charLimit;
  const overCharLimit = content.length > effectiveCharLimit;
  const selectedPersonaStem =
    selected?.group === "personas" ? selected.name.replace(/\.md$/, "") : "";

  const doSave = useCallback(async () => {
    if (!selectedName) return;
    setSaving(true);
    setError(null);
    try {
      await saveIdentityFile(client, { name: selectedName, content });
      setOriginal(content);
      notify({ message: t("settings.identity.files.saved"), durationMs: 2600 });
      await refreshList();
    } catch (reason: unknown) {
      setError(
        `${t("settings.identity.files.saveError")}: ${
          reason instanceof Error ? reason.message : String(reason)
        }`,
      );
    } finally {
      setSaving(false);
      setConfirming(false);
    }
  }, [client, content, refreshList, selectedName, t]);

  const handleSave = useCallback(() => {
    if (!selectedName || !hasChanges) return;
    if (HIGH_RISK_FILES.has(selectedName)) {
      setConfirming(true);
      return;
    }
    void doSave();
  }, [doSave, hasChanges, selectedName]);

  const handleReload = useCallback(async () => {
    setError(null);
    try {
      await reloadIdentity(client);
      await refreshList();
      if (selectedName) {
        const payload = await fetchIdentityFile(token, selectedName);
        setContent(payload.content ?? "");
        setOriginal(payload.content ?? "");
      }
      notify({ message: t("settings.identity.files.reloadSuccess"), durationMs: 2600 });
    } catch (reason: unknown) {
      setError(
        `${t("settings.identity.files.reloadError")}: ${
          reason instanceof Error ? reason.message : String(reason)
        }`,
      );
    }
  }, [client, refreshList, selectedName, t, token]);

  const handleActivatePersona = useCallback(async () => {
    if (!selectedPersonaStem) return;
    try {
      await setActivePersona(client, selectedPersonaStem);
      setActivePersonaState(selectedPersonaStem);
      notify({
        message: t("settings.identity.files.personaActivated", { name: selectedPersonaStem }),
        durationMs: 2600,
      });
    } catch (reason: unknown) {
      setError(
        `${t("settings.identity.files.saveError")}: ${
          reason instanceof Error ? reason.message : String(reason)
        }`,
      );
    }
  }, [client, selectedPersonaStem, t]);

  const diff = confirming && selectedName ? diffLines(original, content) : null;

  return (
    <div className="assistant-page flex h-full flex-col">
      <PageHeading
        kicker={t("settings.identity.files.kicker")}
        title={t("settings.identity.files.title")}
        description={t("settings.identity.files.subtitle")}
        illustration={{ src: "/illustrations/zhixu-mascot.webp", size: "compact" }}
      />

      {!bannerDismissed ? (
        <div className="mt-4 flex items-start gap-2 rounded-control border border-amber-300/70 bg-amber-50/80 px-3 py-2 text-[12px] leading-relaxed text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-300" aria-hidden />
          <p className="flex-1">{t("settings.identity.files.pageBanner")}</p>
          <button
            type="button"
            aria-label={t("settings.identity.files.dismissBanner")}
            onClick={() => {
              setBannerDismissed(true);
              try {
                window.localStorage.setItem(BANNER_STORAGE_KEY, "1");
              } catch {
                /* 隐私模式下写不进去，忽略 */
              }
            }}
            className="shrink-0 rounded-control p-0.5 text-amber-700/70 transition-colors hover:text-amber-900 dark:text-amber-200/70"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
      ) : null}

      <div className="mt-4 flex min-h-0 flex-1 flex-col gap-4 lg:flex-row">
        {/* 左：源文件清单 */}
        <aside className="w-full shrink-0 overflow-y-auto border-border/60 lg:w-56 lg:border-r lg:pr-3">
          <p className="px-1 pb-1.5 pt-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            {t("settings.identity.files.sourceFiles")}
          </p>
          {core.map((file) => (
            <FileItem
              key={file.name}
              file={file}
              selected={file.name === selectedName}
              onSelect={() => setSelectedName(file.name)}
            />
          ))}
          {personas.length > 0 ? (
            <>
              <p className="px-1 pb-1.5 pt-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                {t("settings.identity.files.personasGroup")}
              </p>
              {personas.map((file) => (
                <FileItem
                  key={file.name}
                  file={file}
                  selected={file.name === selectedName}
                  active={file.name.replace(/\.md$/, "") === activePersona}
                  onSelect={() => setSelectedName(file.name)}
                />
              ))}
            </>
          ) : null}
        </aside>

        {/* 右：编辑器 */}
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          {!selectedName ? (
            <div className="flex flex-1 items-center justify-center rounded-panel border border-border/60 bg-muted/20 text-[13px] text-muted-foreground">
              {t("settings.identity.files.noFileSelected")}
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 pb-2">
                <span className="flex-1 text-[14px] font-semibold text-foreground">{selectedName}</span>
                {selectedName === "SOUL.md" ? (
                  <span className="rounded-full border border-emerald-300/70 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-900 dark:border-emerald-700/40 dark:bg-emerald-950/30 dark:text-emerald-200">
                    {t("settings.identity.files.fullTextInject")}
                  </span>
                ) : null}
                {selectedName === "AGENT.md" ? (
                  <span className="rounded-full border border-amber-300/70 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-200">
                    {t("settings.identity.files.autoCompile")}
                  </span>
                ) : null}
                {selectedPersonaStem ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => void handleActivatePersona()}
                    className="h-7 rounded-full px-3 text-[11.5px] font-medium"
                  >
                    {t("settings.identity.files.setActivePersona")}
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => void handleReload()}
                  className="h-7 gap-1.5 rounded-full px-3 text-[11.5px] font-medium"
                >
                  <RefreshCcw className="h-3.5 w-3.5" aria-hidden />
                  {t("settings.identity.files.reload")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  onClick={handleSave}
                  disabled={saving || !hasChanges}
                  className="h-7 gap-1.5 rounded-full px-3.5 text-[11.5px] font-semibold"
                >
                  {saving ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <Save className="h-3.5 w-3.5" aria-hidden />
                  )}
                  {t("settings.identity.files.save")}
                </Button>
              </div>

              {warningKey ? (
                <div className="mb-2 flex items-start gap-2 rounded-control bg-amber-50/80 px-3 py-2 text-[12px] leading-relaxed text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-300" aria-hidden />
                  <span>{t(`settings.identity.files.${warningKey}`)}</span>
                </div>
              ) : null}

              {error ? (
                <div className="mb-2 flex items-start gap-2 rounded-control bg-destructive/10 px-3 py-2 text-[12px] text-destructive">
                  <button
                    type="button"
                    aria-label={t("settings.identity.files.dismissError")}
                    onClick={() => setError(null)}
                    className="shrink-0 rounded-control p-0.5"
                  >
                    <X className="h-3.5 w-3.5" aria-hidden />
                  </button>
                  <p className="min-w-0 whitespace-pre-wrap break-words">{error}</p>
                </div>
              ) : null}

              <div className="relative flex min-h-[320px] flex-1 flex-col">
                <textarea
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  spellCheck={false}
                  aria-label={t("settings.identity.files.editorAria")}
                  className="block min-h-[320px] w-full flex-1 resize-y rounded-panel border border-border bg-background px-3 py-2 font-mono text-[12.5px] leading-relaxed text-foreground"
                />
                {loading ? (
                  <div className="absolute inset-0 flex items-center justify-center rounded-panel bg-background/60">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
                  </div>
                ) : null}
              </div>

              <div className="flex items-center justify-between gap-3 pt-2 text-[11px] text-muted-foreground">
                <span>
                  <span className={cn(overCharLimit && "font-semibold text-destructive")}>
                    {t("settings.identity.files.charCount", {
                      count: content.length,
                      max: effectiveCharLimit,
                    })}
                    {overCharLimit ? ` — ${t("settings.identity.files.charCountExceeded")}` : ""}
                  </span>
                </span>
                <span>{t("settings.identity.files.tokenCount", { count: tokenCount })}</span>
              </div>
            </>
          )}
        </section>
      </div>

      {confirming && selectedName ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="identity-confirm-title"
        >
          <div className="w-full max-w-[480px] rounded-panel border border-border bg-background p-6 shadow-2xl">
            <h2 id="identity-confirm-title" className="text-[15px] font-bold text-destructive">
              {t("settings.identity.files.confirmTitle")}
            </h2>
            <div className="mt-3 space-y-2 text-[13px] leading-relaxed text-muted-foreground">
              <p>
                {t("settings.identity.files.confirmChangesSummary", {
                  added: diff?.added ?? 0,
                  removed: diff?.removed ?? 0,
                })}
              </p>
              <p className="text-destructive">{t("settings.identity.files.confirmRisk")}</p>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setConfirming(false)}
                className="h-8 rounded-full px-3.5 text-[12px] font-medium"
              >
                {t("settings.identity.files.confirmCancel")}
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={() => void doSave()}
                className="h-8 rounded-full bg-destructive px-4 text-[12px] font-semibold text-destructive-foreground hover:bg-destructive/90"
              >
                {t("settings.identity.files.confirmSave")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function FileItem({
  file,
  selected,
  active,
  onSelect,
}: {
  file: IdentityFileEntry;
  selected: boolean;
  active?: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  const display = file.logicalPath?.replace(/^personas\//, "") ?? file.name;

  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-1.5 rounded-control px-2 py-1.5 text-left text-[12.5px] transition-colors",
        selected
          ? "bg-primary/10 font-semibold text-primary"
          : "text-foreground/80 hover:bg-muted",
        !file.exists && "opacity-50",
      )}
    >
      <ChevronRight className="h-2.5 w-2.5 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 truncate">{display}</span>
      {active ? (
        <span className="shrink-0 rounded-full bg-primary/15 px-1.5 py-0.5 text-[11px] font-medium text-primary">
          {t("settings.identity.files.activePersona")}
        </span>
      ) : null}
      {file.restricted ? (
        <span
          title={t("settings.identity.files.restrictedTitle")}
          className="inline-flex shrink-0 text-amber-600 dark:text-amber-400"
        >
          <AlertTriangle className="h-3 w-3" aria-hidden />
        </span>
      ) : null}
    </button>
  );
}
