import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Check,
  ChevronDown,
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
import {
  fetchIdentityFile,
  getActivePersona,
  listIdentityPresets,
  reloadIdentity,
  saveIdentityFile,
  setActivePersona,
  type IdentityPreset,
} from "@/lib/api";
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
  /** Marker color used for the persona card swatch. Avoid purple — use the
   *  project's sage/forest/amber/terracotta palette only. */
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
    toneClass: "from-emerald-100/80 via-emerald-50 to-amber-50/70 text-emerald-900",
    fallbackLabel: "Balanced",
    fallbackTagline: "Act → explain briefly → ask only when needed.",
    fallbackTraits: ["先做再说", "短而准", "不装懂"],
    fallbackSample: "我已经把表格整理好了。要不要我也把第二段落的脚注加上？",
  },
  {
    presetKey: "mentor",
    icon: ShieldCheck,
    toneClass: "from-amber-100/80 via-amber-50 to-stone-100/70 text-amber-900",
    fallbackLabel: "Mentor",
    fallbackTagline: "Ask how you think first; correct mistakes directly.",
    fallbackTraits: ["先问你思路", "结论带依据", "错了直说"],
    fallbackSample: "你这个判断的依据是什么？我们先把它拆开看看，再决定要不要重写。",
  },
  {
    presetKey: "creative",
    icon: Feather,
    toneClass: "from-rose-100/70 via-amber-50 to-emerald-50/70 text-rose-900",
    fallbackLabel: "Creative partner",
    fallbackTagline: "Three directions first; keep your voice in the final cut.",
    fallbackTraits: ["先给三个方向", "保留你的原味", "鼓励试错"],
    fallbackSample: "我想到三种切法：先讲痛点、先讲故事、先抛数据。你最想击中哪一类读者？",
  },
  {
    presetKey: "companion",
    icon: Heart,
    toneClass: "from-amber-50 via-rose-50 to-emerald-50/60 text-rose-900",
    fallbackLabel: "Companion",
    fallbackTagline: "Friend, not support desk. Empathise before offering solutions.",
    fallbackTraits: ["像朋友", "先共情", "再给方案"],
    fallbackSample: "听起来挺累的。先喝口水，我们慢慢把今天最难的那件聊清楚。",
  },
  {
    presetKey: "tech_expert",
    icon: FlaskConical,
    toneClass: "from-stone-100/80 via-emerald-50 to-stone-50/70 text-stone-900",
    fallbackLabel: "Tech expert",
    fallbackTagline: "No diluted jargon. Version-scoped. Evidence over vibes.",
    fallbackTraits: ["术语不稀释", "带版本前提", "以测试为准"],
    fallbackSample: "在 React 19 下，use() 的调用位置已经放宽到条件分支；我贴个最小复现你确认下。",
  },
];

const TONE_OPTIONS: Array<{ value: ToneKey; label: string; description: string; icon: LucideIcon }> = [
  { value: "friendly", label: "友好", description: "亲切、鼓励、共情", icon: Heart },
  { value: "professional", label: "专业", description: "正式、克制、有理有据", icon: ShieldCheck },
  { value: "concise", label: "简短", description: "先给结论再展开", icon: Sparkles },
  { value: "detailed", label: "详细", description: "完整推理过程与边界", icon: Lightbulb },
];

const LANGUAGE_OPTIONS: Array<{ value: LanguageKey; label: string; description: string }> = [
  { value: "auto", label: "跟随你", description: "用你提问的语言回答" },
  { value: "zh", label: "中文", description: "所有回复都使用中文" },
  { value: "en", label: "English", description: "All replies in English" },
];

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------



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
  const { t, i18n } = useTranslation();
  const zh = i18n.resolvedLanguage?.startsWith("zh");

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5">
      {PERSONA_CARDS.map((spec, index) => {
        const Icon = spec.icon;
        const isActive = spec.presetKey === activePresetKey;
        const isBusy = busyKey === spec.presetKey;
        const preset = presets.find((row) => row.name === spec.presetKey);
        const label = preset
          ? t(preset.labelKey, { defaultValue: spec.fallbackLabel })
          : (zh ? spec.fallbackLabel : spec.fallbackLabel);
        const tagline = preset
          ? t(preset.descriptionKey, { defaultValue: spec.fallbackTagline })
          : spec.fallbackTagline;
        // Asymmetric grid: the first card spans wider, last card sits a bit
        // higher — avoids the perfectly-aligned "three equal cards" feel.
        const offsetClass = index === 0
          ? "xl:col-span-2 xl:row-span-2"
          : index === PERSONA_CARDS.length - 1
            ? "xl:mt-6"
            : "";

        return (
          <button
            key={spec.presetKey}
            type="button"
            disabled={!preset || isBusy}
            onClick={() => preset && onSelect(preset)}
            className={cn(
              "group relative flex flex-col items-start gap-3 overflow-hidden rounded-2xl border bg-gradient-to-br p-4 text-left transition-all duration-200 ease-out",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              "disabled:cursor-default disabled:opacity-90",
              spec.toneClass,
              isActive
                ? "border-primary/60 shadow-[0_18px_45px_-25px_rgba(71,119,91,0.55)]"
                : "border-border/70 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-[0_18px_45px_-25px_rgba(71,119,91,0.35)]",
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
                  {zh ? "已选用" : "Active"}
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
                ? (zh ? "重新载入" : "Reload preset")
                : (zh ? "使用这个性格" : "Use this vibe")}
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
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
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
                ? "border-primary/55 bg-primary/[0.08] text-foreground shadow-[0_12px_28px_-18px_rgba(71,119,91,0.45)]"
                : "border-border/70 bg-background/60 text-foreground/80 hover:border-primary/35 hover:bg-primary/[0.04]",
            )}
          >
            <span className="flex items-center gap-1.5 text-[13px] font-semibold">
              <Icon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
              {option.label}
            </span>
            <span className="text-[11.5px] leading-relaxed text-muted-foreground">
              {option.description}
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
  return (
    <div role="radiogroup" aria-label="回复语言" className="flex flex-wrap gap-2">
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
              <span className="text-[12.5px] font-semibold leading-tight">{option.label}</span>
              <span className={cn("text-[11px] leading-tight", active ? "text-primary-foreground/80" : "text-muted-foreground")}>
                {option.description}
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
  const zh = i18n.resolvedLanguage?.startsWith("zh");
  const lang = chooseLanguage(state, zh ? "zh" : "en");
  const toneMeta = TONE_OPTIONS.find((option) => option.value === state.tone);
  const langMeta = LANGUAGE_OPTIONS.find((option) => option.value === state.language);
  const card = PERSONA_CARDS.find((card) => card.fallbackLabel === personaLabel) ?? PERSONA_CARDS[0];
  const Icon = card.icon;

  const name = state.name.trim() || (zh ? "你的助手" : "Your Agent");
  const greeting = lang === "zh"
    ? `你好，我是${name}。${state.role.trim() ? `我会${state.role.trim().replace(/[。.!?]+$/, "")}。` : ""}有什么我能帮上的？`
    : `Hi, I'm ${name}.${state.role.trim() ? ` I help with ${state.role.trim().replace(/[.!?]+$/, "")}.` : ""} What can I do for you today?`;
  const sampleLine = card.fallbackSample;

  return (
    <div className="relative overflow-hidden rounded-3xl border border-border/60 bg-background/70 p-5 shadow-[0_30px_80px_-50px_rgba(29,42,35,0.35)]">
      <span
        className="absolute inset-0 -z-10 opacity-70"
        style={{
          backgroundImage:
            "radial-gradient(circle at 18% 18%, hsl(154 30% 80% / 0.55), transparent 55%), radial-gradient(circle at 82% 78%, hsl(40 50% 85% / 0.45), transparent 60%)",
        }}
        aria-hidden
      />
      <span
        className="absolute inset-0 -z-10 opacity-[0.18] mix-blend-multiply"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.10 0 0 0 0 0.14 0 0 0 0 0.12 0 0 0 0.40 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")",
        }}
        aria-hidden
      />
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className={cn("flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br text-foreground shadow-sm", card.toneClass)}>
            <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="truncate text-[14px] font-semibold text-foreground">{name}</p>
            <p className="truncate text-[12px] text-muted-foreground">
              {personaLabel} · {toneMeta?.label} · {langMeta?.label}
            </p>
          </div>
        </div>
        <span className="hidden items-center gap-1 rounded-full border border-primary/30 bg-primary/[0.08] px-2 py-0.5 text-[11px] font-medium text-primary sm:inline-flex">
          <MicVocal className="h-3 w-3" aria-hidden />
          {zh ? "实时预览" : "Live preview"}
        </span>
      </div>
      <div className="mt-5 space-y-3">
        <div className="flex justify-end">
          <div className="max-w-[80%] rounded-2xl rounded-br-md bg-foreground/[0.06] px-3 py-2 text-[13px] leading-relaxed text-foreground/90">
            {zh ? "帮我把这个月的工作周报写成三段。" : "Draft my monthly recap as three short sections."}
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
            {zh ? "已应用规则：" : "Active rule: "}
            <span className="font-medium">{state.rules.split("\n")[0]}</span>
          </p>
        ) : null}
      </div>
      <p className="mt-4 text-[11px] leading-relaxed text-muted-foreground/85">
        {t("identity.previewHint", {
          defaultValue: zh
            ? "以上是当前设定下 Agent 的开场白预览，保存后立即生效。"
            : "How the agent opens after your changes. Saving makes it live.",
        })}
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
  const { i18n } = useTranslation();
  const zh = i18n.resolvedLanguage?.startsWith("zh");
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
          {zh ? "高级模式：直接编辑原始 Markdown" : "Advanced: edit raw Markdown"}
        </span>
        <ChevronDown
          className={cn("h-4 w-4 text-muted-foreground transition-transform duration-200 ease-out", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open ? (
        <div className="space-y-3 border-t border-border/60 px-4 pb-4 pt-3">
          <p className="text-[11.5px] leading-relaxed text-muted-foreground">
            {zh
              ? "只有当预设和表单都不够用时才需要来这里。手动修改会立即生效，但被表单再次保存时会按上面的结构重写。"
              : "Only reach for this when the presets and form cannot express what you want. Edits here apply immediately but the form view will rewrite the file on next save."}
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
            aria-label={zh ? "原始 Markdown" : "Raw Markdown"}
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
              {zh ? "从磁盘重载" : "Reload from disk"}
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
  const { i18n } = useTranslation();
  const zh = i18n.resolvedLanguage?.startsWith("zh");
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
    if (!activePersona) return zh ? "未启用预设" : "Default";
    return personaLabelMap[activePersona] ?? activePersona;
  }, [activePersona, personaLabelMap, zh]);

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
      const lang = chooseLanguage(form, zh ? "zh" : "en");
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
      window.setTimeout(() => setSaveState("idle"), 1800);
    } catch (reason) {
      setSaveState("idle");
      setSaveError(
        `${zh ? "保存失败" : "Save failed"}: ${reason instanceof Error ? reason.message : String(reason)}`,
      );
    }
  }, [client, form, rawDirty, rawDraft, zh]);

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
    <div className="mx-auto w-full max-w-[1180px] px-4 py-8 sm:px-8 lg:px-10">
      <header className="grid grid-cols-1 items-end gap-6 lg:grid-cols-[minmax(0,1fr)_auto]">
        <div className="space-y-2">
          <p className="text-[12px] font-semibold uppercase tracking-[0.22em] text-primary/85">
            {zh ? "身份 · Identity" : "Identity"}
          </p>
          <h1 className="text-[28px] font-semibold leading-tight text-foreground sm:text-[32px]">
            {zh ? "让 Agent 用你想要的方式说话" : "Make the agent sound like you want"}
          </h1>
          <p className="max-w-[560px] text-[13.5px] leading-relaxed text-muted-foreground">
            {zh
              ? "从一张性格卡片开始，再用几个简单问题补充细节。下面看到的就是 Agent 之后会怎么开口。"
              : "Pick a vibe, answer a few quick questions, and see exactly how the agent will greet you next time."}
          </p>
        </div>
        <div className="hidden justify-self-end lg:block">
          <img
            src="/illustrations/identity-hero.svg"
            alt=""
            aria-hidden
            className="h-[148px] w-auto select-none"
            draggable={false}
          />
        </div>
      </header>

      {loadError ? (
        <div
          role="alert"
          className="mt-5 flex items-start gap-3 rounded-2xl border border-amber-300/70 bg-amber-50/80 px-4 py-3 text-[12.5px] text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-300" aria-hidden />
          <div className="min-w-0">
            <p className="font-medium">{zh ? "部分内容没能加载" : "Some content could not load"}</p>
            <p className="mt-0.5 break-words opacity-90">{loadError}</p>
          </div>
        </div>
      ) : null}

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_360px]">
        {/* Left column: persona + form */}
        <div className="space-y-8">
          <section className="space-y-4">
            <header className="flex items-baseline justify-between gap-3">
              <h2 className="text-[16px] font-semibold text-foreground">
                {zh ? "1 · 选一张性格卡片" : "1 · Pick a vibe"}
              </h2>
              <span className="text-[11.5px] text-muted-foreground">
                {zh ? "选中后会自动填好下面的表单" : "Selecting one fills the form for you"}
              </span>
            </header>
            <PersonaGallery
              presets={presets}
              activePresetKey={activePersona}
              busyKey={personaBusy}
              onSelect={handleSelectPreset}
            />
          </section>

          <section className="space-y-4">
            <header className="flex items-baseline justify-between gap-3">
              <h2 className="text-[16px] font-semibold text-foreground">
                {zh ? "2 · 说说你是谁" : "2 · Tell the agent about you"}
              </h2>
              <span className="text-[11.5px] text-muted-foreground">
                {zh ? "空白处会自动跳过" : "Empty entries are skipped"}
              </span>
            </header>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <label className="flex flex-col gap-1.5 md:col-span-2">
                <FieldLabel
                  icon={UserRound}
                  title={zh ? "Agent 怎么称呼你" : "How should the agent call you?"}
                />
                <input
                  type="text"
                  value={form.name}
                  onChange={(event) => updateField("name", event.target.value)}
                  maxLength={48}
                  placeholder={zh ? "例如：小明、运营组" : "e.g. Marketing team, Alex"}
                  className={cn(
                    "h-10 w-full rounded-xl border border-input bg-background px-3 text-[14px] text-foreground placeholder:text-muted-foreground/70",
                    formControlFocusClassName,
                  )}
                />
              </label>

              <label className="flex flex-col gap-1.5 md:col-span-2">
                <FieldLabel
                  icon={Sparkles}
                  title={zh ? "Agent 主要帮你做什么" : "What does the agent mainly help you with?"}
                />
                <textarea
                  value={form.role}
                  onChange={(event) => updateField("role", event.target.value)}
                  rows={2}
                  maxLength={240}
                  placeholder={zh
                    ? "例如：把一周的会议整理成可读周报；梳理客户反馈里的高频问题"
                    : "e.g. turn weekly meetings into a readable recap; group customer feedback into themes"}
                  className={cn(
                    "block w-full resize-y rounded-xl border border-input bg-background px-3 py-2 text-[14px] leading-relaxed text-foreground placeholder:text-muted-foreground/70",
                    formControlFocusClassName,
                  )}
                />
              </label>

              <div className="flex flex-col gap-1.5">
                <FieldLabel
                  icon={MicVocal}
                  title={zh ? "说话的调子" : "Tone of voice"}
                  hint={zh ? "选最贴近的那个" : "Pick the closest"}
                />
                <TonePicker value={form.tone} onChange={(next) => updateField("tone", next)} />
              </div>

              <div className="flex flex-col gap-1.5">
                <FieldLabel
                  icon={Languages}
                  title={zh ? "默认用什么语言回复" : "Default reply language"}
                />
                <LanguagePicker value={form.language} onChange={(next) => updateField("language", next)} />
              </div>

              <label className="flex flex-col gap-1.5 md:col-span-2">
                <FieldLabel
                  icon={ShieldCheck}
                  title={zh ? "希望它遵守的底线" : "Lines the agent should not cross"}
                  hint={zh ? "一行一条，最多 6 条" : "One per line, up to 6"}
                />
                <textarea
                  value={form.rules}
                  onChange={(event) => updateField("rules", event.target.value)}
                  rows={3}
                  maxLength={400}
                  placeholder={zh
                    ? "不在合同里直接生成金额\n不向未登录的用户提及后台路径\n没把握就明说 '我不确定'，不装懂"
                    : "Never invent contract numbers\nDon't quote internal paths to anonymous users\nSay 'I don't know' rather than guess"}
                  className={cn(
                    "block w-full resize-y rounded-xl border border-input bg-background px-3 py-2 text-[14px] leading-relaxed text-foreground placeholder:text-muted-foreground/70",
                    formControlFocusClassName,
                  )}
                />
              </label>

              <label className="flex flex-col gap-1.5 md:col-span-2">
                <FieldLabel
                  icon={Lightbulb}
                  title={zh ? "还想告诉它的事（可选）" : "Anything else worth remembering?"}
                />
                <textarea
                  value={form.notes}
                  onChange={(event) => updateField("notes", event.target.value)}
                  rows={2}
                  maxLength={240}
                  placeholder={zh
                    ? "公司名、行话缩写、忌讳词等都可以写在这里"
                    : "Company name, jargon, words to avoid…"}
                  className={cn(
                    "block w-full resize-y rounded-xl border border-input bg-background px-3 py-2 text-[14px] leading-relaxed text-foreground placeholder:text-muted-foreground/70",
                    formControlFocusClassName,
                  )}
                />
              </label>
            </div>
          </section>

          <AdvancedPanel
            draft={rawDirty ? rawDraft : composeForm(form, { language: chooseLanguage(form, zh ? "zh" : "en") })}
            onDraftChange={(next) => {
              setRawDraft(next);
              setRawDirty(true);
              const parsed = parseForm(next);
              setForm(parsed);
            }}
            onReload={() => void handleReload()}
            reloading={reloading}
          />

          <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-5">
            <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
              {saveError ? (
                <span className="inline-flex items-center gap-1 text-destructive">
                  <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                  {saveError}
                </span>
              ) : dirty ? (
                <span>{zh ? "有未保存的改动" : "Unsaved changes"}</span>
              ) : (
                <span className="inline-flex items-center gap-1">
                  <Check className="h-3.5 w-3.5 text-primary" aria-hidden />
                  {zh ? "已是最新" : "Up to date"}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setForm(initial);
                  setRawDirty(false);
                  setRawDraft(
                    composeForm(initial, { language: chooseLanguage(initial, zh ? "zh" : "en") }),
                  );
                }}
                disabled={!dirty || saveState === "saving"}
                className="h-9 rounded-full px-3.5 text-[12.5px] font-medium"
              >
                {zh ? "撤销改动" : "Discard"}
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
                  ? (zh ? "已保存" : "Saved")
                  : (zh ? "保存设定" : "Save")}
              </Button>
            </div>
          </footer>
        </div>

        {/* Right column: live preview */}
        <aside className="space-y-4 lg:sticky lg:top-6 lg:self-start">
          <LivePreview state={form} personaLabel={activePersonaLabel} />
          {personaOptions.length > 0 ? (
            <div className="rounded-2xl border border-border/60 bg-background/55 p-4">
              <p className="text-[12px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                {zh ? "Agent 在用的预设" : "Active persona"}
              </p>
              <p className="mt-2 text-[13px] text-foreground">
                {activePersona || (zh ? "未启用，保留默认 SOUL" : "Default SOUL, no preset enabled")}
              </p>
              <p className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">
                {zh
                  ? "点击上方任意性格卡片都会同步切换到这个预设。"
                  : "Clicking a vibe card above also switches this preset."}
              </p>
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
