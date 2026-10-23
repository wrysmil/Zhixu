import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Compass,
  Feather,
  FlaskConical,
  Heart,
  Languages,
  Lightbulb,
  Loader2,
  MicVocal,
  PencilLine,
  Save,
  ShieldCheck,
  Sparkles,
  Sprout,
  UserRound,
  Wand2,
  type LucideIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { formControlFocusClassName } from "@/components/ui/form-control";
import { PageHeading } from "@/components/workspace/PageHeading";
import {
  fetchIdentityFile,
  getActivePersona,
  listIdentityPresets,
  reloadIdentity,
  saveIdentityFile,
  setActivePersona,
  type IdentityPreset,
} from "@/lib/api";
import { notify } from "@/lib/toast-events";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";
import {
  DEFAULT_FORM_STATE,
  chooseLanguage,
  composeForm,
  parseForm,
  type IdentityFormState,
  type LanguageKey,
  type ToneKey,
} from "./identityMarkdown";

// -----------------------------------------------------------------------------
// Persona gallery data. Lives in this file (not i18n) because the curated
// line-up is part of the product design — the i18n only carries the
// human-readable label/description overrides below.
// -----------------------------------------------------------------------------

interface PersonaCardSpec {
  presetKey: string;
  icon: LucideIcon;
  /** Marker color used for the persona card swatch. Kept to soft blue/neutral
   *  tints with at most one warm accent so the page stays a calm workbench. */
  toneClass: string;
  fallbackLabel: string;
  fallbackTagline: string;
  fallbackTraits: string[];
  fallbackSample: string;
}

const PERSONA_CARDS: PersonaCardSpec[] = [
  {
    presetKey: "balanced",
    icon: Compass,
    toneClass: "from-primary/[0.1] via-primary/[0.04] to-secondary text-foreground",
    fallbackLabel: "Balanced",
    fallbackTagline: "Act → explain briefly → ask only when needed.",
    fallbackTraits: ["先做再说", "短而准", "不装懂"],
    fallbackSample: "我已经把表格整理好了。要不要我也把第二段落的脚注加上？",
  },
  {
    presetKey: "mentor",
    icon: ShieldCheck,
    toneClass: "from-amber-50/70 via-secondary to-card text-foreground",
    fallbackLabel: "Mentor",
    fallbackTagline: "Ask how you think first; correct mistakes directly.",
    fallbackTraits: ["先问你思路", "结论带依据", "错了直说"],
    fallbackSample: "你这个判断的依据是什么？我们先把它拆开看看，再决定要不要重写。",
  },
  {
    presetKey: "creative",
    icon: Feather,
    toneClass: "from-rose-50/60 via-secondary to-card text-foreground",
    fallbackLabel: "Creative partner",
    fallbackTagline: "Three directions first; keep your voice in the final cut.",
    fallbackTraits: ["先给三个方向", "保留你的原味", "鼓励试错"],
    fallbackSample: "我想到三种切法：先讲痛点、先讲故事、先抛数据。你最想击中哪一类读者？",
  },
  {
    presetKey: "companion",
    icon: Heart,
    toneClass: "from-rose-50/50 via-secondary to-card text-foreground",
    fallbackLabel: "Companion",
    fallbackTagline: "Friend, not support desk. Empathise before offering solutions.",
    fallbackTraits: ["像朋友", "先共情", "再给方案"],
    fallbackSample: "听起来挺累的。先喝口水，我们慢慢把今天最难的那件聊清楚。",
  },
  {
    presetKey: "tech_expert",
    icon: FlaskConical,
    toneClass: "from-slate-100/70 via-secondary to-card text-foreground",
    fallbackLabel: "Tech expert",
    fallbackTagline: "No diluted jargon. Version-scoped. Evidence over vibes.",
    fallbackTraits: ["术语不稀释", "带版本前提", "以测试为准"],
    fallbackSample: "在 React 19 下，use() 的调用位置已经放宽到条件分支；我贴个最小复现你确认下。",
  },
];

const TONE_OPTIONS: Array<{
  value: ToneKey;
  labelKey: string;
  descriptionKey: string;
  icon: LucideIcon;
}> = [
  {
    value: "friendly",
    labelKey: "settings.identity.page.tone.friendly.label",
    descriptionKey: "settings.identity.page.tone.friendly.description",
    icon: Heart,
  },
  {
    value: "professional",
    labelKey: "settings.identity.page.tone.professional.label",
    descriptionKey: "settings.identity.page.tone.professional.description",
    icon: ShieldCheck,
  },
  {
    value: "concise",
    labelKey: "settings.identity.page.tone.concise.label",
    descriptionKey: "settings.identity.page.tone.concise.description",
    icon: Sparkles,
  },
  {
    value: "detailed",
    labelKey: "settings.identity.page.tone.detailed.label",
    descriptionKey: "settings.identity.page.tone.detailed.description",
    icon: Lightbulb,
  },
];

const LANGUAGE_OPTIONS: Array<{
  value: LanguageKey;
  labelKey: string;
  descriptionKey: string;
}> = [
  {
    value: "auto",
    labelKey: "settings.identity.page.replyLang.auto.label",
    descriptionKey: "settings.identity.page.replyLang.auto.description",
  },
  {
    value: "zh",
    labelKey: "settings.identity.page.replyLang.zh.label",
    descriptionKey: "settings.identity.page.replyLang.zh.description",
  },
  {
    value: "en",
    labelKey: "settings.identity.page.replyLang.en.label",
    descriptionKey: "settings.identity.page.replyLang.en.description",
  },
];

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

interface IdentityStep {
  key: string;
  label: string;
  hint: string;
}

/**
 * The editor is split into paged steps instead of one long scroll: every step
 * fits the desktop viewport, so picking a vibe and filling the form never
 * pushes the save actions below the fold.
 */
function identitySteps(t: TFunction): IdentityStep[] {
  return [
    {
      key: "vibe",
      label: t("settings.identity.page.step.vibe.label"),
      hint: t("settings.identity.page.step.vibe.hint"),
    },
    {
      key: "voice",
      label: t("settings.identity.page.step.voice.label"),
      hint: t("settings.identity.page.step.voice.hint"),
    },
    {
      key: "rules",
      label: t("settings.identity.page.step.rules.label"),
      hint: t("settings.identity.page.step.rules.hint"),
    },
    {
      key: "advanced",
      label: t("settings.identity.page.step.advanced.label"),
      hint: t("settings.identity.page.step.advanced.hint"),
    },
  ];
}



// -----------------------------------------------------------------------------
// Sub-components
// -----------------------------------------------------------------------------

function PersonaGallery({
  presets,
  activePresetKey,
  busyKey,
  onSelect,
}: {
  presets: IdentityPreset[];
  activePresetKey: string | null;
  busyKey: string | null;
  onSelect: (preset: IdentityPreset) => void;
}) {
  const { t } = useTranslation();

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {PERSONA_CARDS.map((spec, index) => {
        const Icon = spec.icon;
        const isActive = spec.presetKey === activePresetKey;
        const isBusy = busyKey === spec.presetKey;
        const preset = presets.find((row) => row.name === spec.presetKey);
        const label = preset
          ? t(preset.labelKey, { defaultValue: spec.fallbackLabel })
          : spec.fallbackLabel;
        const tagline = preset
          ? t(preset.descriptionKey, { defaultValue: spec.fallbackTagline })
          : spec.fallbackTagline;
        // The default vibe leads the gallery: it spans both columns wherever
        // the grid has room, so the five cards fill whole rows instead of
        // leaving a hole and no card gets squeezed below a readable width.
        const offsetClass = index === 0
          ? "sm:col-span-2"
          : "";

        return (
          <button
            key={spec.presetKey}
            type="button"
            disabled={!preset || isBusy}
            onClick={() => preset && onSelect(preset)}
            className={cn(
              "group relative flex flex-col items-start gap-3 overflow-hidden rounded-2xl border bg-gradient-to-br p-3.5 text-left transition-all duration-200 ease-out",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              "disabled:cursor-default disabled:opacity-90",
              spec.toneClass,
              isActive
                ? "border-primary/45 shadow-[0_14px_36px_-28px_hsl(var(--primary)/0.5)]"
                : "border-border hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-[0_14px_36px_-30px_hsl(var(--primary)/0.35)]",
              offsetClass,
            )}
          >
            <span
              className={cn(
                "absolute -right-10 -top-10 h-32 w-32 rounded-full bg-white/35 blur-2xl",
                "transition-opacity duration-300 ease-out",
                isActive ? "opacity-90" : "opacity-0 group-hover:opacity-60",
              )}
              aria-hidden
            />
            <span className="flex items-center gap-2">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/70 text-foreground shadow-sm">
                <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden />
              </span>
              {isActive ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-2 py-0.5 text-[10.5px] font-medium text-primary">
                  <Check className="h-3 w-3" aria-hidden />
                  {t("settings.identity.page.gallery.active")}
                </span>
              ) : null}
            </span>
            <div className="min-w-0 space-y-1.5">
              <h3 className="text-[15px] font-semibold leading-tight">{label}</h3>
              <p className="text-[12.5px] leading-relaxed opacity-90">{tagline}</p>
            </div>
            <ul className="mt-auto flex flex-wrap gap-1.5 text-[11.5px]">
              {spec.fallbackTraits.map((trait) => (
                <li
                  key={trait}
                  className="rounded-full bg-white/55 px-2 py-0.5 font-medium text-foreground/75"
                >
                  {trait}
                </li>
              ))}
            </ul>
            <span className="mt-2 inline-flex items-center gap-1.5 text-[12px] font-medium text-foreground/85 transition-transform duration-200 ease-out group-hover:translate-x-0.5">
              {isBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Wand2 className="h-3.5 w-3.5" aria-hidden />
              )}
              {isActive
                ? t("settings.identity.page.gallery.reload")
                : t("settings.identity.page.gallery.use")}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function TonePicker({
  value,
  onChange,
}: {
  value: ToneKey;
  onChange: (next: ToneKey) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-2 gap-2">
      {TONE_OPTIONS.map((option) => {
        const Icon = option.icon;
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={cn(
              "flex flex-col items-start gap-1 rounded-xl border px-3 py-2.5 text-left transition-all duration-200 ease-out",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              active
                ? "border-primary/45 bg-primary/[0.07] text-foreground shadow-[0_10px_24px_-20px_hsl(var(--primary)/0.45)]"
                : "border-border bg-card text-foreground/80 hover:border-primary/30 hover:bg-primary/[0.04]",
            )}
          >
            <span className="flex items-center gap-1.5 text-[13px] font-semibold">
              <Icon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
              {t(option.labelKey)}
            </span>
            <span className="text-[11.5px] leading-relaxed text-muted-foreground">
              {t(option.descriptionKey)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function LanguagePicker({
  value,
  onChange,
}: {
  value: LanguageKey;
  onChange: (next: LanguageKey) => void;
}) {
  const { t } = useTranslation();
  return (
    <div role="radiogroup" aria-label={t("settings.identity.page.replyLang.groupAria")} className="flex flex-wrap gap-2">
      {LANGUAGE_OPTIONS.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(option.value)}
            className={cn(
              "flex items-start gap-2 rounded-full border px-3 py-1.5 text-left transition-colors duration-150 ease-out",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              active
                ? "border-primary/55 bg-primary text-primary-foreground shadow-sm"
                : "border-border/70 bg-background/60 text-foreground/80 hover:border-primary/35 hover:bg-primary/[0.05]",
            )}
          >
            <Languages className="mt-0.5 h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
            <span className="flex flex-col">
              <span className="text-[12.5px] font-semibold leading-tight">{t(option.labelKey)}</span>
              <span className={cn("text-[11px] leading-tight", active ? "text-primary-foreground/80" : "text-muted-foreground")}>
                {t(option.descriptionKey)}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function FieldLabel({
  icon: Icon,
  title,
  hint,
}: {
  icon: LucideIcon;
  title: string;
  hint?: string;
}) {
  return (
    <span className="flex items-center gap-2 text-[13px] font-semibold text-foreground/85">
      <Icon className="h-3.5 w-3.5 text-primary" strokeWidth={1.75} aria-hidden />
      <span>{title}</span>
      {hint ? (
        <span className="text-[11.5px] font-normal text-muted-foreground/85">{hint}</span>
      ) : null}
    </span>
  );
}

function LivePreview({
  state,
  personaLabel,
}: {
  state: IdentityFormState;
  personaLabel: string;
}) {
  const { t, i18n } = useTranslation();
  const uiLanguage: LanguageKey = i18n.resolvedLanguage?.startsWith("zh") ? "zh" : "en";
  const lang = chooseLanguage(state, uiLanguage);
  const toneMeta = TONE_OPTIONS.find((option) => option.value === state.tone);
  const langMeta = LANGUAGE_OPTIONS.find((option) => option.value === state.language);
  const card = PERSONA_CARDS.find((card) => card.fallbackLabel === personaLabel) ?? PERSONA_CARDS[0];
  const Icon = card.icon;

  const name = state.name.trim() || t("settings.identity.page.preview.yourAgent");
  const greeting = lang === "zh"
    ? `你好，我是${name}。${state.role.trim() ? `我会${state.role.trim().replace(/[。.!?]+$/, "")}。` : ""}有什么我能帮上的？`
    : `Hi, I'm ${name}.${state.role.trim() ? ` I help with ${state.role.trim().replace(/[.!?]+$/, "")}.` : ""} What can I do for you today?`;
  const sampleLine = card.fallbackSample;

  return (
    <div className="relative overflow-hidden rounded-panel border border-border bg-card p-5 shadow-[0_10px_30px_-26px_hsl(var(--foreground)/0.4)]">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className={cn("flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br text-foreground shadow-sm", card.toneClass)}>
            <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="truncate text-[14px] font-semibold text-foreground">{name}</p>
            <p className="truncate text-[12px] text-muted-foreground">
              {personaLabel} · {toneMeta ? t(toneMeta.labelKey) : null} · {langMeta ? t(langMeta.labelKey) : null}
            </p>
          </div>
        </div>
        <span className="hidden items-center gap-1 rounded-full border border-primary/30 bg-primary/[0.08] px-2 py-0.5 text-[11px] font-medium text-primary sm:inline-flex">
          <MicVocal className="h-3 w-3" aria-hidden />
          {t("settings.identity.page.preview.live")}
        </span>
      </div>
      <div className="mt-5 space-y-3">
        <div className="flex justify-end">
          <div className="max-w-[80%] rounded-2xl rounded-br-md bg-foreground/[0.06] px-3 py-2 text-[13px] leading-relaxed text-foreground/90">
            {t("settings.identity.page.preview.samplePrompt")}
          </div>
        </div>
        <div className="flex items-start gap-2">
          <span className={cn("mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br", card.toneClass)}>
            <Icon className="h-3.5 w-3.5" aria-hidden />
          </span>
          <div className="max-w-[82%] rounded-2xl rounded-tl-md bg-primary/[0.08] px-3 py-2 text-[13px] leading-relaxed text-foreground">
            <p className="font-medium">{greeting}</p>
            <p className="mt-1 text-foreground/85">{sampleLine}</p>
          </div>
        </div>
        {state.rules.trim() ? (
          <p className="rounded-xl border border-dashed border-amber-300/70 bg-amber-50/60 px-3 py-2 text-[11.5px] text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/20 dark:text-amber-100">
            <AlertTriangle className="mr-1 inline h-3 w-3 align-[-2px]" aria-hidden />
            {t("settings.identity.page.preview.activeRule")}
            <span className="font-medium">{state.rules.split("\n")[0]}</span>
          </p>
        ) : null}
      </div>
      <p className="mt-4 text-[11px] leading-relaxed text-muted-foreground/85">
        {t("settings.identity.page.preview.hint")}
      </p>
    </div>
  );
}

function AdvancedPanel({
  draft,
  onDraftChange,
  onReload,
  reloading,
}: {
  draft: string;
  onDraftChange: (next: string) => void;
  onReload: () => void;
  reloading: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-2xl border border-dashed border-border/70 bg-background/40">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left text-[12.5px] font-medium text-foreground/80 transition-colors hover:bg-foreground/[0.025]"
      >
        <span className="flex items-center gap-2">
          <PencilLine className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={1.75} aria-hidden />
          {t("settings.identity.page.advanced.toggle")}
        </span>
        <ChevronDown
          className={cn("h-4 w-4 text-muted-foreground transition-transform duration-200 ease-out", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open ? (
        <div className="space-y-3 border-t border-border/60 px-4 pb-4 pt-3">
          <p className="text-[11.5px] leading-relaxed text-muted-foreground">
            {t("settings.identity.page.advanced.note")}
          </p>
          <textarea
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            spellCheck={false}
            rows={10}
            className={cn(
              "block w-full resize-y rounded-xl border border-border bg-background px-3 py-2 font-mono text-[12.5px] leading-relaxed text-foreground",
              formControlFocusClassName,
            )}
            aria-label={t("settings.identity.page.advanced.rawAria")}
          />
          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={onReload}
              disabled={reloading}
              className="h-8 rounded-full px-3 text-[12px] font-medium"
            >
              {reloading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Sprout className="h-3.5 w-3.5" aria-hidden />
              )}
              {t("settings.identity.page.advanced.reload")}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// -----------------------------------------------------------------------------
// Main page
// -----------------------------------------------------------------------------

export function IdentityPage() {
  const { t, i18n } = useTranslation();
  const uiLanguage: LanguageKey = i18n.resolvedLanguage?.startsWith("zh") ? "zh" : "en";
  const { client, token } = useClient();

  const [form, setForm] = useState<IdentityFormState>(DEFAULT_FORM_STATE);
  const [rawDraft, setRawDraft] = useState<string>("");
  const [rawDirty, setRawDirty] = useState(false);
  const [presets, setPresets] = useState<IdentityPreset[]>([]);
  const [personaOptions, setPersonaOptions] = useState<string[]>([]);
  const [activePersona, setActivePersonaState] = useState<string>("");
  const [personaLabelMap, setPersonaLabelMap] = useState<Record<string, string>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [personaBusy, setPersonaBusy] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [initial, setInitial] = useState<IdentityFormState>(DEFAULT_FORM_STATE);
  const initialRef = useRef<IdentityFormState>(DEFAULT_FORM_STATE);
  const [step, setStep] = useState(0);
  const steps = useMemo(() => identitySteps(t), [t]);

  // Load presets, persona options, and SOUL.md once on mount.
  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    void Promise.allSettled([
      listIdentityPresets(token),
      getActivePersona(token),
      fetchIdentityFile(token, "SOUL.md").catch(() => ({ name: "SOUL.md", content: "" })),
    ]).then(([presetResult, personaResult, soulResult]) => {
      if (cancelled) return;
      const nextPresets = presetResult.status === "fulfilled" ? presetResult.value.presets : [];
      setPresets(nextPresets);
      setPersonaLabelMap(
        Object.fromEntries(nextPresets.map((row) => [row.name, row.name])),
      );
      if (personaResult.status === "fulfilled") {
        const payload = personaResult.value;
        setPersonaOptions(payload.options ?? []);
        setActivePersonaState(
          payload.active && payload.options?.includes(payload.active) ? payload.active : "",
        );
      }
      if (soulResult.status === "fulfilled") {
        const content = soulResult.value.content ?? "";
        const parsed = parseForm(content);
        setForm(parsed);
        setRawDraft(content);
        setInitial(parsed);
        initialRef.current = parsed;
      }
      const firstError =
        (presetResult.status === "rejected" && (presetResult.reason as Error)?.message) ||
        (personaResult.status === "rejected" && (personaResult.reason as Error)?.message) ||
        (soulResult.status === "rejected" && (soulResult.reason as Error)?.message) ||
        null;
      if (firstError) setLoadError(firstError);
    });
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Track dirty state without re-rendering on every keystroke.
  useEffect(() => {
    setDirty(
      form.name !== initial.name ||
      form.role !== initial.role ||
      form.tone !== initial.tone ||
      form.language !== initial.language ||
      form.rules !== initial.rules ||
      form.notes !== initial.notes,
    );
  }, [form, initial]);

  const activePersonaLabel = useMemo(() => {
    if (!activePersona) return t("settings.identity.page.persona.default");
    return personaLabelMap[activePersona] ?? activePersona;
  }, [activePersona, personaLabelMap, t]);

  const updateField = useCallback(<K extends keyof IdentityFormState>(key: K, value: IdentityFormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleSelectPreset = useCallback(async (preset: IdentityPreset) => {
    setPersonaBusy(preset.name);
    setSaveError(null);
    try {
      const parsed = parseForm(preset.content);
      setForm(parsed);
      setRawDraft(preset.content);
      setRawDirty(true);
      setInitial(parsed);
      initialRef.current = parsed;
      // Best-effort: also flip the persona switch so this preset is active.
      try {
        await setActivePersona(client, preset.name);
        setActivePersonaState(preset.name);
      } catch {
        /* preset switching is optional; ignore */
      }
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPersonaBusy(null);
    }
  }, [client]);

  const handleSave = useCallback(async () => {
    setSaveState("saving");
    setSaveError(null);
    try {
      const lang = chooseLanguage(form, uiLanguage);
      const composed = composeForm(form, { language: lang });
      await saveIdentityFile(client, { name: "SOUL.md", content: composed });
      // If the user wrote raw Markdown in the advanced panel, honour it on save.
      if (rawDirty && rawDraft !== composed.trim()) {
        await saveIdentityFile(client, { name: "SOUL.md", content: rawDraft });
      }
      try {
        await reloadIdentity(client);
      } catch {
        /* reload is a hint; failure is non-fatal */
      }
      setRawDraft(composed);
      setRawDirty(false);
      setInitial(form);
      initialRef.current = form;
      setSaveState("saved");
      notify({
        message: t("settings.identity.page.toast.saved"),
        durationMs: 2600,
      });
      window.setTimeout(() => setSaveState("idle"), 2600);
    } catch (reason) {
      setSaveState("idle");
      setSaveError(
        `${t("settings.identity.page.error.saveFailed")}: ${reason instanceof Error ? reason.message : String(reason)}`,
      );
    }
  }, [client, form, rawDirty, rawDraft, uiLanguage, t]);

  const handleReload = useCallback(async () => {
    setReloading(true);
    setSaveError(null);
    try {
      await reloadIdentity(client);
      const payload = await fetchIdentityFile(token, "SOUL.md");
      const parsed = parseForm(payload.content);
      setForm(parsed);
      setRawDraft(payload.content);
      setRawDirty(false);
      setInitial(parsed);
      initialRef.current = parsed;
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setReloading(false);
    }
  }, [client, token]);

  return (
    <div className="assistant-page">
      <PageHeading
        kicker={t("settings.identity.page.hero.eyebrow")}
        title={t("settings.identity.page.hero.title")}
        description={t("settings.identity.page.hero.subtitle")}
        illustration={{ src: "/illustrations/zhixu-mascot.webp", size: "compact" }}
      />

      {loadError ? (
        <div
          role="alert"
          className="mt-5 flex items-start gap-3 rounded-2xl border border-amber-300/70 bg-amber-50/80 px-4 py-3 text-[12.5px] text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-300" aria-hidden />
          <div className="min-w-0">
            <p className="font-medium">{t("settings.identity.page.error.loadFailed")}</p>
            <p className="mt-0.5 break-words opacity-90">{loadError}</p>
          </div>
        </div>
      ) : null}

      <div className="mt-4 grid grid-cols-1 gap-8 xl:grid-cols-[minmax(0,1fr)_360px]">
        {/* Left column: paged identity editor (one step at a time) */}
        <div className="min-w-0 space-y-3">
          <div
            role="tablist"
            aria-label={t("settings.identity.page.stepsAria")}
            className="flex flex-wrap items-center gap-2"
          >
            {steps.map((entry, index) => {
              const active = index === step;
              return (
                <button
                  key={entry.key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  aria-controls={`identity-step-${entry.key}`}
                  onClick={() => setStep(index)}
                  className={cn(
                    "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[12.5px] font-medium transition-colors duration-150 ease-out",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                    active
                      ? "border-primary/55 bg-primary text-primary-foreground shadow-sm"
                      : "border-border/70 bg-background/60 text-foreground/75 hover:border-primary/35 hover:bg-primary/[0.05]",
                  )}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "flex h-[18px] w-[18px] items-center justify-center rounded-full text-[10.5px] font-semibold",
                      active ? "bg-white/25" : "bg-foreground/[0.07] text-foreground/70",
                    )}
                  >
                    {index + 1}
                  </span>
                  {entry.label}
                </button>
              );
            })}
          </div>

          <section
            id={`identity-step-${steps[step].key}`}
            role="tabpanel"
            aria-label={steps[step].label}
            className="space-y-4"
          >
            <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <h2 className="text-[16px] font-semibold text-foreground">{steps[step].label}</h2>
              <span className="text-[11.5px] text-muted-foreground">{steps[step].hint}</span>
            </header>

            {step === 0 ? (
              <PersonaGallery
                presets={presets}
                activePresetKey={activePersona}
                busyKey={personaBusy}
                onSelect={handleSelectPreset}
              />
            ) : null}

            {step === 1 ? (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <label className="flex flex-col gap-1.5 md:col-span-2">
                <FieldLabel
                  icon={UserRound}
                  title={t("settings.identity.page.form.nameLabel")}
                />
                <input
                  type="text"
                  value={form.name}
                  onChange={(event) => updateField("name", event.target.value)}
                  maxLength={48}
                  placeholder={t("settings.identity.page.form.namePlaceholder")}
                  className={cn(
                    "h-10 w-full rounded-xl border border-input bg-background px-3 text-[14px] text-foreground placeholder:text-muted-foreground/70",
                    formControlFocusClassName,
                  )}
                />
              </label>

              <label className="flex flex-col gap-1.5 md:col-span-2">
                <FieldLabel
                  icon={Sparkles}
                  title={t("settings.identity.page.form.roleLabel")}
                />
                <textarea
                  value={form.role}
                  onChange={(event) => updateField("role", event.target.value)}
                  rows={2}
                  maxLength={240}
                  placeholder={t("settings.identity.page.form.rolePlaceholder")}
                  className={cn(
                    "block w-full resize-y rounded-xl border border-input bg-background px-3 py-2 text-[14px] leading-relaxed text-foreground placeholder:text-muted-foreground/70",
                    formControlFocusClassName,
                  )}
                />
              </label>

              <div className="flex flex-col gap-1.5">
                <FieldLabel
                  icon={MicVocal}
                  title={t("settings.identity.page.form.toneLabel")}
                  hint={t("settings.identity.page.form.toneHint")}
                />
                <TonePicker value={form.tone} onChange={(next) => updateField("tone", next)} />
              </div>

              <div className="flex flex-col gap-1.5">
                <FieldLabel
                  icon={Languages}
                  title={t("settings.identity.page.form.langLabel")}
                />
                <LanguagePicker value={form.language} onChange={(next) => updateField("language", next)} />
              </div>
            </div>
            ) : null}

            {step === 2 ? (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <label className="flex flex-col gap-1.5 md:col-span-2">
                <FieldLabel
                  icon={ShieldCheck}
                  title={t("settings.identity.page.form.rulesLabel")}
                  hint={t("settings.identity.page.form.rulesHint")}
                />
                <textarea
                  value={form.rules}
                  onChange={(event) => updateField("rules", event.target.value)}
                  rows={3}
                  maxLength={400}
                  placeholder={t("settings.identity.page.form.rulesPlaceholder")}
                  className={cn(
                    "block w-full resize-y rounded-xl border border-input bg-background px-3 py-2 text-[14px] leading-relaxed text-foreground placeholder:text-muted-foreground/70",
                    formControlFocusClassName,
                  )}
                />
              </label>

              <label className="flex flex-col gap-1.5 md:col-span-2">
                <FieldLabel
                  icon={Lightbulb}
                  title={t("settings.identity.page.form.notesLabel")}
                />
                <textarea
                  value={form.notes}
                  onChange={(event) => updateField("notes", event.target.value)}
                  rows={2}
                  maxLength={240}
                  placeholder={t("settings.identity.page.form.notesPlaceholder")}
                  className={cn(
                    "block w-full resize-y rounded-xl border border-input bg-background px-3 py-2 text-[14px] leading-relaxed text-foreground placeholder:text-muted-foreground/70",
                    formControlFocusClassName,
                  )}
                />
              </label>
              </div>
            ) : null}

            {step === 3 ? (
              <AdvancedPanel
                draft={rawDirty ? rawDraft : composeForm(form, { language: chooseLanguage(form, uiLanguage) })}
                onDraftChange={(next) => {
                  setRawDraft(next);
                  setRawDirty(true);
                  const parsed = parseForm(next);
                  setForm(parsed);
                }}
                onReload={() => void handleReload()}
                reloading={reloading}
              />
            ) : null}
          </section>

          <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-4">
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setStep((prev) => Math.max(0, prev - 1))}
                disabled={step === 0}
                className="h-9 gap-1.5 rounded-full px-3.5 text-[12.5px] font-medium"
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
                {t("settings.identity.page.footer.back")}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => setStep((prev) => Math.min(steps.length - 1, prev + 1))}
                disabled={step === steps.length - 1}
                className="h-9 gap-1.5 rounded-full px-3.5 text-[12.5px] font-medium"
              >
                {t("settings.identity.page.footer.next")}
                <ChevronRight className="h-3.5 w-3.5" aria-hidden />
              </Button>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-3">
              <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
                {saveError ? (
                  <span className="inline-flex items-center gap-1 text-destructive">
                    <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                    {saveError}
                  </span>
                ) : dirty ? (
                  <span>{t("settings.identity.page.footer.unsaved")}</span>
                ) : (
                  <span className="inline-flex items-center gap-1">
                    <Check className="h-3.5 w-3.5 text-primary" aria-hidden />
                    {t("settings.identity.page.footer.upToDate")}
                  </span>
                )}
              </div>
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setForm(initial);
                  setRawDirty(false);
                  setRawDraft(
                    composeForm(initial, { language: chooseLanguage(initial, uiLanguage) }),
                  );
                }}
                disabled={!dirty || saveState === "saving"}
                className="h-9 rounded-full px-3.5 text-[12.5px] font-medium"
              >
                {t("settings.identity.page.footer.discard")}
              </Button>
              <Button
                type="button"
                onClick={() => void handleSave()}
                disabled={saveState === "saving"}
                className={cn(
                  "h-9 gap-2 rounded-full px-4 text-[12.5px] font-semibold",
                  "transition-transform duration-200 ease-out active:scale-[0.97]",
                )}
              >
                {saveState === "saving" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : saveState === "saved" ? (
                  <Check className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <Save className="h-3.5 w-3.5" aria-hidden />
                )}
                {saveState === "saved"
                  ? t("settings.identity.page.footer.saved")
                  : t("settings.identity.page.footer.save")}
              </Button>
            </div>
          </footer>
        </div>

        {/* Right column: live preview */}
        <aside className="space-y-4 xl:sticky xl:top-6 xl:self-start">
          <LivePreview state={form} personaLabel={activePersonaLabel} />
          {personaOptions.length > 0 ? (
            <div className="rounded-2xl border border-border/60 bg-background/55 p-4">
              <p className="text-[12px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                {t("settings.identity.page.persona.cardTitle")}
              </p>
              <p className="mt-2 text-[13px] text-foreground">
                {activePersona || t("settings.identity.page.persona.cardNone")}
              </p>
              <p className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">
                {t("settings.identity.page.persona.cardHint")}
              </p>
            </div>
          ) : null}
        </aside>
      </div>

    </div>
  );
}
