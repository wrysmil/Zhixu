import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ChevronRight,
  CircleAlert,
  FileText,
  FolderTree,
  Info,
  Loader2,
  RefreshCcw,
  ShieldCheck,
  SquareCheckBig,
  Users,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formControlFocusClassName } from "@/components/ui/form-control";
import {
  fetchIdentityFile,
  getActivePersona,
  listIdentityFiles,
  listIdentityPresets,
  reloadIdentity,
  saveIdentityFile,
  setActivePersona,
  type IdentityCompileStatus,
  type IdentityPreset,
} from "@/lib/api";
import type { IdentityFileEntry } from "@/lib/types";
import { fmtDateTime } from "@/lib/format";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";
import { useMediaQuery } from "@/hooks/useMediaQuery";

// -----------------------------------------------------------------------------
// Visual primitives — match ChannelsSettings's split-pane catalog pattern.
// -----------------------------------------------------------------------------

type BadgeTone = "amber" | "sage" | "clay";

const BADGE_CLASS: Record<BadgeTone, string> = {
  amber: "border-amber-300/70 bg-amber-50 text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-200",
  sage: "border-emerald-300/70 bg-emerald-50 text-emerald-900 dark:border-emerald-700/40 dark:bg-emerald-950/30 dark:text-emerald-200",
  clay: "border-rose-300/70 bg-rose-50 text-rose-900 dark:border-rose-700/40 dark:bg-rose-950/30 dark:text-rose-200",
};

/** The backend badge tone is an open string; clamp it to a known class. */
function badgeTone(tone: string): BadgeTone {
  return tone === "amber" || tone === "sage" || tone === "clay" ? tone : "sage";
}

/**
 * Fallback copy per badge key, for locales that have not translated the key yet.
 * Keys come from the backend catalog (`BADGE_*`); unknown keys fall through to "".
 */
const BADGE_DEFAULTS: Record<string, string> = {
  "settings.identity.badgeNeedsCompile": "需编译",
  "settings.identity.badgeAutoRegen": "自动重生成",
  "settings.identity.badgeSystemSection": "系统段落",
  "settings.identity.badgeNotWired": "尚未生效",
};

function FileBadge({ tone, fallback }: { tone: BadgeTone; fallback: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        BADGE_CLASS[tone],
      )}
    >
      {tone === "clay" && <AlertTriangle className="h-3 w-3" />}
      {tone === "amber" && <RefreshCcw className="h-3 w-3" />}
      {tone === "sage" && (
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />
      )}
      {fallback}
    </span>
  );
}

function IdentityFileRow({
  file,
  selected,
  onSelect,
}: {
  file: IdentityFileEntry;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  const display = file.logicalPath ?? file.name;
  const badgeFallback = file.badge
    ? t(file.badge.labelKey, {
        defaultValue: BADGE_DEFAULTS[file.badge.labelKey] ?? "",
      })
    : null;
  const subtitle = file.badge
    ? badgeFallback
    : file.restricted
      ? t("settings.identity.systemManaged", "系统管理")
      : t("settings.identity.editable", "可编辑");

  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        "group flex w-full min-w-0 items-center gap-3 rounded-control px-3 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border/80",
        selected ? "bg-background" : "hover:bg-muted",
        !file.exists && "opacity-50",
      )}
    >
      <div
        className={cn(
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-md",
          selected ? "bg-foreground/8 text-foreground" : "bg-muted text-muted-foreground",
        )}
      >
        <FileText className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <h3 className="truncate font-mono text-[13.5px] font-semibold leading-5 text-foreground">
          {display}
        </h3>
        <p className="mt-0.5 truncate text-[12px] leading-5 text-muted-foreground">
          {subtitle}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <span className="shrink-0 font-mono text-[11px] leading-5 text-muted-foreground/80">
          ≈{file.tokens ?? 0} tokens
        </span>
        {file.badge && badgeFallback && (
          <FileBadge tone={badgeTone(file.badge.tone)} fallback={badgeFallback} />
        )}
        {file.restricted && !file.badge && (
          <CircleAlert
            className="h-4 w-4 shrink-0 text-amber-500"
            aria-label={t("settings.identity.restrictedHint", "System-managed")}
          />
        )}
        <ChevronRight
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
            selected && "translate-x-0.5 text-foreground",
          )}
          aria-hidden
        />
      </div>
    </button>
  );
}

function GroupHeader({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-1.5 px-3 pb-1.5 pt-4 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80 first:pt-2">
      {icon}
      <span>{label}</span>
    </div>
  );
}

// -----------------------------------------------------------------------------
// Main component — same split-pane pattern as ChannelsSettings:
// left = scrollable file list, right = detail/editor card. On narrow screens,
// the detail takes over the whole row. Data comes from the gateway identity
// endpoints (reads over HTTP, mutations over the WebUI socket).
// -----------------------------------------------------------------------------

export function IdentityView() {
  const { t, i18n } = useTranslation();
  const { client, token } = useClient();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const splitLayout = useMediaQuery("(min-width: 1280px)");
  const containerRef = useRef<HTMLDivElement>(null);

  const [files, setFiles] = useState<IdentityFileEntry[]>([]);
  const [charLimit, setCharLimit] = useState(1500);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [savingState, setSavingState] = useState<"idle" | "saving" | "saved">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [compactDetailOpen, setCompactDetailOpen] = useState(false);
  const [fromTemplate, setFromTemplate] = useState(false);
  const [presetLoaded, setPresetLoaded] = useState(false);
  const [presets, setPresets] = useState<IdentityPreset[]>([]);
  const [activePersona, setActivePersonaState] = useState("");
  const [personaOptions, setPersonaOptions] = useState<string[]>([]);
  const [compileStatus, setCompileStatus] = useState<IdentityCompileStatus | null>(null);

  const refreshCompileStatus = () => {
    listIdentityFiles(token)
      .then((payload) => setCompileStatus(payload.compile ?? null))
      .catch(() => {
        /* status display is optional; ignore */
      });
  };

  // Load the catalog once per token. `cancelled` makes the effect safe against
  // unmount and against a token change that races the in-flight request.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    listIdentityFiles(token)
      .then((payload) => {
        if (cancelled) return;
        setFiles(payload.files);
        setCharLimit(payload.charLimit);
        setCompileStatus(payload.compile ?? null);
        setSelectedName((prev) =>
          prev && payload.files.some((file) => file.name === prev)
            ? prev
            : payload.files[0]?.name ?? null,
        );
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setLoadError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Persona presets (SOUL editor toolbar). Loaded once; failure is non-fatal —
  // the dropdown just stays empty.
  useEffect(() => {
    let cancelled = false;
    listIdentityPresets(token)
      .then((payload) => {
        if (!cancelled) setPresets(payload.presets);
      })
      .catch(() => {
        /* presets are optional UX; ignore */
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Which persona file is injected into the system prompt. Also optional: a
  // failure leaves the picker on "None" rather than breaking the whole page.
  useEffect(() => {
    let cancelled = false;
    getActivePersona(token)
      .then((payload) => {
        if (cancelled) return;
        setPersonaOptions(payload.options ?? []);
        // The backend normalizes an unknown stem to "", but a stale gateway
        // could still answer with a value absent from options — fall back to
        // "None" so the select can never hold a value it cannot render.
        setActivePersonaState(
          payload.active && payload.options?.includes(payload.active) ? payload.active : "",
        );
      })
      .catch(() => {
        /* persona switching is optional UX; ignore */
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const selectedMissing = useMemo(() => {
    const entry = files.find((file) => file.name === selectedName);
    return entry !== undefined && !entry.exists;
  }, [files, selectedName]);

  // Always fetch: the backend returns the factory template for missing files
  // (fromTemplate) instead of 404, so the editor prefills rather than erroring.
  // A network/5xx failure on a known-missing file stays silent (empty draft).
  useEffect(() => {
    setFromTemplate(false);
    setPresetLoaded(false);
    if (!selectedName) {
      setDraft("");
      return;
    }
    let cancelled = false;
    setDraft("");
    setLoadError(null);
    fetchIdentityFile(token, selectedName)
      .then((payload) => {
        if (cancelled) return;
        setDraft(payload.content);
        setFromTemplate(payload.fromTemplate === true);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        // Missing file with no template payload → keep the create-on-save
        // placeholder; only surface unexpected errors as a banner.
        const entry = files.find((file) => file.name === selectedName);
        if (entry !== undefined && !entry.exists) {
          setDraft("");
          setFromTemplate(false);
          return;
        }
        setLoadError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- files only used for missing fallback
  }, [token, selectedName]);

  const core = useMemo(() => files.filter((file) => file.group === "core"), [files]);
  const personas = useMemo(() => files.filter((file) => file.group === "personas"), [files]);
  const selected = useMemo(
    () => files.find((file) => file.name === selectedName) ?? null,
    [files, selectedName],
  );

  const charCount = draft.length;
  const charMax = charLimit;
  const overLimit = charCount > charMax;

  function handleSelect(file: IdentityFileEntry) {
    setSelectedName(file.name);
    setSavingState("idle");
    setSaveError(null);
    if (!splitLayout) setCompactDetailOpen(true);
  }

  function handleApplyPreset(preset: IdentityPreset) {
    if (draft.trim() && draft !== preset.content) {
      const ok = window.confirm(
        tx(
          "settings.identity.personaOverwriteConfirm",
          "将覆盖当前 SOUL 草稿，未保存的修改会丢失。继续？",
        ),
      );
      if (!ok) return;
    }
    setDraft(preset.content);
    setPresetLoaded(true);
    setFromTemplate(false);
    setSavingState("idle");
    setSaveError(null);
  }

  function handleBack() {
    setCompactDetailOpen(false);
  }

  async function handlePersonaChange(next: string) {
    const previous = activePersona;
    if (next === previous) return;
    setActivePersonaState(next);
    setSaveError(null);
    try {
      await setActivePersona(client, next);
    } catch (reason) {
      setActivePersonaState(previous);
      setSaveError(
        `${tx("settings.identity.personaSwitchFailed", "切换人格失败")}: ${
          reason instanceof Error ? reason.message : String(reason)
        }`,
      );
    }
  }

  async function handleSave() {
    if (!selected) return;
    setSavingState("saving");
    setSaveError(null);
    try {
      await saveIdentityFile(client, { name: selected.name, content: draft });
      setSavingState("saved");
      setFromTemplate(false);
      setPresetLoaded(false);
      // The save changes the source mtime; refresh the "uncompiled changes" hint.
      refreshCompileStatus();
      // A create-on-save flips the catalog entry to exists, so the load effect
      // refetches the canonical body instead of keeping the stale skip.
      if (!selected.exists) {
        setFiles((prev) =>
          prev.map((file) =>
            file.name === selected.name ? { ...file, exists: true } : file,
          ),
        );
      }
    } catch (reason) {
      setSaveError(
        `${tx("settings.identity.saveFailed", "保存失败")}: ${
          reason instanceof Error ? reason.message : String(reason)
        }`,
      );
      setSavingState("idle");
    }
  }

  async function handleReload() {
    if (!selected) return;
    setSaveError(null);
    try {
      await reloadIdentity(client);
      const payload = await fetchIdentityFile(token, selected.name);
      setDraft(payload.content);
      setFromTemplate(payload.fromTemplate === true);
      setPresetLoaded(false);
      setSavingState("idle");
      refreshCompileStatus();
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  const showingCompactDetail = !splitLayout && compactDetailOpen;

  return (
    <div
      ref={containerRef}
      className="flex min-h-full flex-1 flex-col xl:min-h-0 xl:overflow-hidden"
    >
      {!showingCompactDetail ? (
        <section className="shrink-0">
          <div className="flex items-start gap-3 rounded-control border border-amber-300/70 bg-amber-50/70 px-3.5 py-3 text-[12.5px] text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-200">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
            <p className="leading-relaxed">
              {t(
                "settings.identity.warningBanner",
                "身份文件是 Agent 的核心人格和行为基础。不当修改可能导致 Agent 行为异常、丧失关键能力或产生不可预期的回复。如果你不确定某项修改的影响，建议先备份原文件。",
              )}
            </p>
          </div>
        </section>
      ) : null}

      {compileStatus ? (
        <section className="mt-3 shrink-0">
          <div
            className={cn(
              "flex items-center gap-2 rounded-control border px-3.5 py-2 text-[12px] leading-5",
              compileStatus.compiled && !compileStatus.fresh
                ? "border-amber-300/70 bg-amber-50/70 text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-200"
                : "border-border/60 bg-background/55 text-muted-foreground",
            )}
          >
            <Info className="h-3.5 w-3.5 shrink-0" aria-hidden />
            <span>
              {compileStatus.compiled
                ? `${tx("settings.identity.compile.last", "上次编译")}：${
                    compileStatus.compiled_at
                      ? fmtDateTime(Date.parse(compileStatus.compiled_at), i18n.resolvedLanguage || i18n.language)
                      : "—"
                  }`
                : tx("settings.identity.compile.never", "尚未编译")}
              {" · "}
              {compileStatus.fresh
                ? tx("settings.identity.compile.fresh", "产物已是最新")
                : tx(
                    "settings.identity.compile.stale",
                    "有未编译改动，编译后下一轮生效",
                  )}
            </span>
          </div>
        </section>
      ) : null}

      {loadError ? (
        <section className="mt-3 shrink-0">
          <div
            role="alert"
            className="flex items-start gap-3 rounded-control border border-rose-300/70 bg-rose-50/70 px-3.5 py-3 text-[12.5px] text-rose-900 dark:border-rose-700/40 dark:bg-rose-950/30 dark:text-rose-200"
          >
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-rose-600 dark:text-rose-400" />
            <div className="min-w-0">
              <p className="font-medium leading-relaxed">
                {tx("settings.identity.loadFailed", "加载身份文件失败")}
              </p>
              <p className="mt-0.5 leading-relaxed opacity-90">{loadError}</p>
            </div>
          </div>
        </section>
      ) : null}

      <section
        className={cn(
          "flex flex-1 flex-col",
          showingCompactDetail ? "mt-1" : "mt-5",
          splitLayout && "min-h-0 overflow-hidden",
        )}
      >
        {loading && files.length === 0 ? (
          <div className="flex h-36 items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
            {tx("settings.identity.loading", "加载身份文件…")}
          </div>
        ) : files.length === 0 ? (
          <div
            role="status"
            className="flex h-36 items-center justify-center text-sm text-muted-foreground"
          >
            {tx("settings.identity.empty", "未发现身份文件")}
          </div>
        ) : (
          <div
            className={cn(
              "grid min-h-0 flex-1",
              splitLayout
                ? "grid-cols-[minmax(0,1fr)_minmax(420px,520px)] gap-6 overflow-hidden"
                : "gap-3",
            )}
          >
            {/* Left column: file list */}
            {!showingCompactDetail && (
              <div className="min-h-0 space-y-1 overflow-y-auto overscroll-contain pr-1">
                <GroupHeader
                  icon={<FolderTree className="h-3 w-3" />}
                  label={tx("settings.identity.groupCore", "核心文件")}
                />
                {core.map((file) => (
                  <IdentityFileRow
                    key={file.name}
                    file={file}
                    selected={selected?.name === file.name}
                    onSelect={() => handleSelect(file)}
                  />
                ))}
                <div className="px-3 pb-1 pt-4">
                  <label
                    htmlFor="identity-active-persona"
                    className="mb-1 block text-[10.5px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80"
                  >
                    {tx("settings.identity.activePersona", "使用人格")}
                  </label>
                  <select
                    id="identity-active-persona"
                    value={activePersona}
                    onChange={(event) => {
                      void handlePersonaChange(event.target.value);
                    }}
                    className={cn(
                      "h-8 w-full rounded-control border border-input bg-settings-surface px-2 text-[12.5px] text-foreground transition-colors",
                      formControlFocusClassName,
                    )}
                  >
                    <option value="">
                      {tx("settings.identity.noPersona", "不使用")}
                    </option>
                    {personaOptions.map((stem) => (
                      <option key={stem} value={stem}>
                        {tx(`settings.identity.preset.${stem}.label`, stem)}
                      </option>
                    ))}
                  </select>
                </div>
                <GroupHeader
                  icon={<FolderTree className="h-3 w-3" />}
                  label={tx("settings.identity.groupPersonas", "人格模板")}
                />
                {personas.map((file) => (
                  <IdentityFileRow
                    key={file.name}
                    file={file}
                    selected={selected?.name === file.name}
                    onSelect={() => handleSelect(file)}
                  />
                ))}
              </div>
            )}

            {/* Right column: editor card */}
            <div className="flex min-h-0 flex-col">
              {/* Compact back button on narrow screens */}
              {showingCompactDetail && (
                <button
                  type="button"
                  onClick={handleBack}
                  className="touch-target mb-3 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
                >
                  <span aria-hidden>←</span>
                  {t("settings.identity.backToList", "返回文件列表")}
                </button>
              )}

              {selected ? (
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-control bg-settings-surface">
                  {/* Header row: title + toolbar */}
                  <div className="flex flex-col gap-3 border-b border-border/45 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                    <div className="flex items-center gap-3">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-background text-foreground">
                        <FileText className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <div className="truncate font-mono text-[14px] font-medium leading-5 text-foreground">
                          {selected.name}
                        </div>
                        <div className="truncate text-[12px] leading-5 text-muted-foreground">
                          {selected.logicalPath
                            ? selected.logicalPath
                            : tx("settings.identity.editorSubtitle", "工作区 · 可编辑身份文件")}
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      {selected.name === "SOUL.md" && presets.length > 0 && (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              className="h-8 rounded-full px-3 text-[12px] font-semibold"
                              title={t("settings.identity.personaPresets", "人格预设")}
                            >
                              <Users className="h-3.5 w-3.5" />
                              <span>
                                {t("settings.identity.personaPresets", "人格预设")}
                              </span>
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-64">
                            <DropdownMenuLabel>
                              {t("settings.identity.personaPresets", "人格预设")}
                            </DropdownMenuLabel>
                            {presets.map((preset) => (
                              <DropdownMenuItem
                                key={preset.name}
                                onSelect={() => handleApplyPreset(preset)}
                                className="flex flex-col items-start gap-0.5 py-2"
                              >
                                <span className="font-medium">
                                  {t(preset.labelKey, preset.name)}
                                </span>
                                <span className="text-[11px] leading-4 text-muted-foreground">
                                  {t(preset.descriptionKey, "")}
                                </span>
                              </DropdownMenuItem>
                            ))}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      )}
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-8 rounded-full px-3 text-[12px] font-semibold"
                        title={t("settings.identity.reload", "重载")}
                        onClick={() => {
                          void handleReload();
                        }}
                      >
                        <RefreshCcw className="h-3.5 w-3.5" />
                        <span>{t("settings.identity.reload", "重载")}</span>
                      </Button>
                    </div>
                  </div>

                  {/* Factory-template / preset-loaded hint (not an error) */}
                  {fromTemplate || presetLoaded ? (
                    <div className="flex items-start gap-2 border-b border-border/45 bg-amber-50/60 px-4 py-2.5 text-[12px] text-amber-900 dark:bg-amber-950/30 dark:text-amber-200 sm:px-5">
                      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span className="min-w-0">
                        {presetLoaded
                          ? tx(
                              "settings.identity.personaLoadedHint",
                              "已载入人格预设 · 确认后点保存",
                            )
                          : tx(
                              "settings.identity.fromTemplateHint",
                              "出厂模板 · 尚未保存到工作区，编辑后点保存创建",
                            )}
                      </span>
                    </div>
                  ) : null}

                  {/* Per-file warning row */}
                  {selected.name === "MEMORY.md" && (
                    <div className="flex items-start gap-3 border-b border-border/45 px-4 py-3 sm:px-5">
                      <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
                      <div className="min-w-0">
                        <p className="text-[13px] font-medium leading-5 text-foreground">
                          {t("settings.identity.memoryMdHintTitle", "自动覆盖提示")}
                        </p>
                        <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground">
                          {t(
                            "settings.identity.memoryMdHint",
                            "MEMORY.md 由系统从 SQLite 自动生成；手动编辑的内容会在下次自动刷新时被覆盖。如需持久化规则，请使用「记忆」页签的「语义记忆」+ 按钮。",
                          )}
                        </p>
                      </div>
                    </div>
                  )}
                  {/* Editor body */}
                  <div className="flex min-h-0 flex-1 border-b border-border/45 bg-background">
                    <textarea
                      value={draft}
                      onChange={(e) => {
                        setDraft(e.target.value);
                        setSavingState("idle");
                      }}
                      spellCheck={false}
                      className="no-resize font-mono block h-full min-h-[24rem] w-full resize-none bg-transparent px-5 py-4 text-[13px] leading-6 text-foreground outline-none placeholder:text-muted-foreground/50"
                      placeholder={
                        selectedMissing && !fromTemplate && !presetLoaded
                          ? tx(
                              "settings.identity.fileMissingPlaceholder",
                              "文件不存在，输入内容并保存即可创建",
                            )
                          : t(
                              "settings.identity.editorPlaceholder",
                              "请从左侧选择一个文件进行编辑",
                            )
                      }
                      aria-label={selected.name}
                    />
                  </div>

                  {/* Save / action error row */}
                  {saveError ? (
                    <div
                      role="alert"
                      className="flex items-start gap-2 border-b border-rose-300/60 bg-rose-50/70 px-4 py-2.5 text-[12px] text-rose-900 dark:border-rose-700/40 dark:bg-rose-950/30 dark:text-rose-200 sm:px-5"
                    >
                      <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span className="min-w-0 break-words">{saveError}</span>
                    </div>
                  ) : null}

                  {/* Footer status row */}
                  <div className="flex flex-col gap-3 px-4 py-3 text-[12px] text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-5">
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                      <span>
                        {t("settings.identity.editorFooterFile", "文件")}{" "}
                        <span className="font-mono text-foreground/80">{selected.name}</span>
                      </span>
                      <span>
                        {t("settings.identity.editorFooterLines", "行数")}{" "}
                        <span className="font-mono text-foreground/80">
                          {draft.split("\n").length}
                        </span>
                      </span>
                      <span>
                        <span
                          className={cn(
                            "font-mono",
                            overLimit ? "font-semibold text-rose-600" : "text-foreground/80",
                          )}
                        >
                          {charCount.toLocaleString()}
                        </span>
                        <span className="text-muted-foreground/70">
                          {" "}
                          / {charMax.toLocaleString()} chars
                          {overLimit
                            ? ` — ${t("settings.identity.charCountExceeded", "超出字符限制")}`
                            : ""}
                        </span>
                      </span>
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-3">
                      {selected.restricted && (
                        <span className="flex items-center gap-1">
                          <CircleAlert className="h-3 w-3 text-amber-500" />
                          {t("settings.identity.systemManaged", "系统管理")}
                        </span>
                      )}
                      <span className="hidden text-muted-foreground/70 sm:inline">
                        {t("settings.identity.editorFooterHint", "Ctrl+S 保存")}
                      </span>
                      <Button
                        type="button"
                        size="sm"
                        className="h-8 rounded-full px-3.5 text-[12px] font-semibold"
                        onClick={() => {
                          void handleSave();
                        }}
                        disabled={savingState === "saving"}
                      >
                        {savingState === "saving" ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <SquareCheckBig className="h-3.5 w-3.5" />
                        )}
                        <span>
                          {savingState === "saved"
                            ? t("settings.identity.saved", "已保存")
                            : t("settings.identity.save", "保存")}
                        </span>
                      </Button>
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
