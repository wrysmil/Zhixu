export type Theme = "light" | "dark";
export type ThemeMode = Theme | "system";
export type PresetId = "zhixu" | "graphite" | "ocean" | "sand";
export type ColorField = "accent" | "background" | "foreground";
export type Palette = Record<ColorField, string>;
export type FontChoice = { id: string; family: string };
export interface AppearancePreferences {
  schemaVersion: 1;
  mode: ThemeMode;
  presetId: PresetId | "custom";
  custom: { basePreset: PresetId; light: Partial<Palette>; dark: Partial<Palette> } | null;
  uiFont: FontChoice;
  codeFont: FontChoice;
}
export interface AppearanceController {
  preferences: AppearancePreferences;
  theme: Theme;
  storageError: boolean;
  setMode(mode: ThemeMode): void;
  setPreset(id: PresetId | "custom"): void;
  setColor(field: ColorField, hex: string): void;
  resetColor(field: ColorField): void;
  setFont(kind: "ui" | "code", choice: FontChoice): void;
  resetAppearance(): void;
}

export const APPEARANCE_STORAGE_KEY = "zhixu-webui.appearance";
export const APPEARANCE_APPLIED_EVENT = "zhixu-webui.appearance-applied";
export const DEFAULT_APPEARANCE: AppearancePreferences = {
  schemaVersion: 1, mode: "light", presetId: "zhixu", custom: null,
  uiFont: { id: "system", family: "" }, codeFont: { id: "default", family: "" },
};

export const THEME_PRESETS: Array<{ id: PresetId; name: string; light: Palette; dark: Palette }> = [
  { id: "zhixu", name: "Zhixu", light: { accent: "#3C64B4", background: "#FFFFFF", foreground: "#202B3C" }, dark: { accent: "#8FB0E8", background: "#111621", foreground: "#EDF1F7" } },
  { id: "graphite", name: "Graphite", light: { accent: "#52525B", background: "#FAFAFA", foreground: "#242426" }, dark: { accent: "#B3B3BC", background: "#18181B", foreground: "#E4E4E7" } },
  { id: "ocean", name: "Ocean", light: { accent: "#0169CC", background: "#F4F8FC", foreground: "#152A3E" }, dark: { accent: "#76B8F5", background: "#101C29", foreground: "#E1EBF5" } },
  { id: "sand", name: "Sand", light: { accent: "#976337", background: "#FAF5EB", foreground: "#3A3026" }, dark: { accent: "#D7AD79", background: "#241F19", foreground: "#EDE3D6" } },
];

const UI_SYSTEM = '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC", "PingFang SC", "Hiragino Sans GB", system-ui, Roboto, "Helvetica Neue", Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji"';
const CODE_DEFAULT = '"JetBrains Mono", "SFMono-Regular", "SF Mono", "Fira Code", "Cascadia Code", "Source Code Pro", Menlo, Consolas, "Noto Sans Mono CJK SC", "Noto Sans SC", "Microsoft YaHei", monospace';
const UI_FONTS: Record<string, string> = {
  system: UI_SYSTEM,
  sans: 'Arial, "Noto Sans", "Noto Sans SC", "Microsoft YaHei", sans-serif',
  serif: 'Georgia, "Noto Serif SC", "Songti SC", "SimSun", serif',
};
const CODE_FONTS: Record<string, string> = {
  default: CODE_DEFAULT,
  consolas: `Consolas, ${CODE_DEFAULT}`,
  cascadia: `"Cascadia Code", ${CODE_DEFAULT}`,
  jetbrains: `"JetBrains Mono", ${CODE_DEFAULT}`,
};

export function normalizeHex(value: string): string | null {
  if (typeof value !== "string") return null;
  const hex = value.trim().replace(/^#/, "");
  if (/^[0-9a-f]{3}$/i.test(hex)) return `#${hex.split("").map((c) => c + c).join("").toUpperCase()}`;
  return /^[0-9a-f]{6}$/i.test(hex) ? `#${hex.toUpperCase()}` : null;
}

export function normalizeFontFamily(value: string): string | null {
  if (typeof value !== "string" || Array.from(value).some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) return null;
  const family = value.trim();
  return family.length > 0 && family.length <= 80 && /^[\p{L}\p{N} _.-]+$/u.test(family) ? family : null;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function isPreset(value: unknown): value is PresetId {
  return THEME_PRESETS.some((preset) => preset.id === value);
}
export function normalizeFontChoice(value: unknown, kind: "ui" | "code"): FontChoice {
  const source = record(value);
  const choices = kind === "ui" ? UI_FONTS : CODE_FONTS;
  if (typeof source.id === "string" && Object.hasOwn(choices, source.id)) return { id: source.id, family: "" };
  const family = typeof source.family === "string" ? normalizeFontFamily(source.family) : null;
  if (source.id === "custom" && family) return { id: "custom", family };
  return { id: kind === "ui" ? "system" : "default", family: "" };
}

export function normalizeAppearance(value: unknown): AppearancePreferences {
  const source = record(value);
  if (source.schemaVersion !== 1) return structuredClone(DEFAULT_APPEARANCE);
  const custom = record(source.custom);
  const normalizeOverrides = (value: unknown): Partial<Palette> => {
    const source = record(value);
    const result: Partial<Palette> = {};
    for (const field of ["accent", "background", "foreground"] as const) {
      const hex = typeof source[field] === "string" ? normalizeHex(source[field]) : null;
      if (hex) result[field] = hex;
    }
    return result;
  };
  const normalizedCustom = isPreset(custom.basePreset) ? {
    basePreset: custom.basePreset, light: normalizeOverrides(custom.light), dark: normalizeOverrides(custom.dark),
  } : null;
  return {
    schemaVersion: 1,
    mode: source.mode === "light" || source.mode === "dark" || source.mode === "system" ? source.mode : "light",
    presetId: isPreset(source.presetId) ? source.presetId : source.presetId === "custom" && normalizedCustom ? "custom" : "zhixu",
    custom: normalizedCustom,
    uiFont: normalizeFontChoice(source.uiFont, "ui"),
    codeFont: normalizeFontChoice(source.codeFont, "code"),
  };
}

export function getPalette(preferences: AppearancePreferences, theme: Theme): Palette {
  const presetId = preferences.presetId === "custom" ? preferences.custom?.basePreset : preferences.presetId;
  const preset = THEME_PRESETS.find((item) => item.id === presetId) ?? THEME_PRESETS[0];
  return { ...preset[theme], ...(preferences.presetId === "custom" ? preferences.custom?.[theme] : {}) };
}

function rgb(hex: string): number[] {
  return [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16));
}
function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map((value) => {
    const n = value / 255;
    return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function contrastRatio(firstHex: string, secondHex: string): number {
  const first = normalizeHex(firstHex), second = normalizeHex(secondHex);
  if (!first || !second) return 1;
  const a = luminance(first), b = luminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
function readableOn(background: string): string {
  return contrastRatio(background, "#FFFFFF") > contrastRatio(background, "#0D0D0D") ? "#FFFFFF" : "#0D0D0D";
}
/**
 * Keep a chosen colour recognisable but never invisible: when the user picks an
 * accent that sits too close to the surface it is tinted towards the readable
 * foreground until the text/background pair clears the WCAG body-text ratio.
 * A white accent used to paint the active rail item white-on-white; this is the
 * guard that stops that class of bug for every custom palette.
 */
function ensureReadable(color: string, background: string, min: number, fallback: string): string {
  if (contrastRatio(color, background) >= min) return color;
  for (let step = 0.12; step < 1; step += 0.08) {
    const candidate = mix(color, fallback, step);
    if (contrastRatio(candidate, background) >= min) return candidate;
  }
  return fallback;
}
function mix(first: string, second: string, amount: number): string {
  const a = rgb(first), b = rgb(second);
  return `#${a.map((n, i) => Math.round(n * (1 - amount) + b[i] * amount).toString(16).padStart(2, "0")).join("").toUpperCase()}`;
}
function hsl(hex: string): string {
  const [r, g, b] = rgb(hex).map((n) => n / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b), delta = max - min;
  const l = (max + min) / 2;
  const s = delta === 0 ? 0 : delta / (1 - Math.abs(2 * l - 1));
  let h = 0;
  if (delta) h = max === r ? ((g - b) / delta + 6) % 6 : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
  return `${+(h * 60).toFixed(3)} ${+(s * 100).toFixed(3)}% ${+(l * 100).toFixed(3)}%`;
}

// Keep the default HSL tokens verbatim: rounding through HEX would alter the
// baseline. Light is a white canvas with near-white tiers (no grey-blue wash),
// dark stays a low saturation navy so the same blue accent reads in both themes.
const ZHIXU_LIGHT = {
  "--background": "0 0% 100%", "--foreground": "220 9% 11%", "--card": "0 0% 100%", "--popover": "0 0% 100%",
  "--primary": "220 9% 11%", "--primary-foreground": "0 0% 100%", "--secondary": "220 10% 96%", "--muted": "220 10% 96%",
  "--muted-foreground": "220 6% 44%", "--accent": "220 10% 96%", "--accent-foreground": "220 9% 11%", "--border": "220 10% 90%",
  "--surface-app": "220 10% 97%", "--surface-panel": "0 0% 100%", "--sidebar": "220 10% 94%", "--sidebar-accent": "220 10% 91%",
  "--nav-active-bg": "220 8% 89%", "--nav-active-fg": "220 9% 11%", "--nav-active-ring": "220 8% 85%",
  "--inline-token-highlight": "#1A1C1F", "--temporary-control-active": "#1A1C1F",
};
const ZHIXU_DARK = {
  "--background": "220 10% 8%", "--foreground": "220 8% 92%", "--card": "220 8% 11%", "--popover": "220 8% 12%",
  "--primary": "220 8% 92%", "--primary-foreground": "220 10% 8%", "--secondary": "220 8% 16%", "--muted": "220 8% 16%",
  "--muted-foreground": "220 6% 62%", "--accent": "220 8% 16%", "--accent-foreground": "220 8% 92%", "--border": "220 8% 18%",
  "--surface-app": "220 9% 9%", "--surface-panel": "220 8% 12%", "--sidebar": "220 9% 10%", "--sidebar-accent": "220 8% 16%",
  "--nav-active-bg": "220 8% 18%", "--nav-active-fg": "220 8% 92%", "--nav-active-ring": "220 8% 26%",
  "--inline-token-highlight": "#E9EAEC", "--temporary-control-active": "#E9EAEC",
};

export function resolveAppearance(preferences: AppearancePreferences, theme: Theme): Record<string, string> {
  const palette = getPalette(preferences, theme);
  const { accent, background, foreground } = palette;
  const safeForeground = readableOn(background);
  const surface = mix(background, safeForeground, theme === "dark" ? 0.035 : 0.025);
  const secondary = mix(background, safeForeground, 0.06);
  const accentSurface = mix(background, accent, theme === "dark" ? 0.22 : 0.15);
  // A near-white accent must still paint visible controls: 3:1 against the page
  // is the floor for a filled button or an icon stroke.
  const primary = ensureReadable(accent, background, 3, safeForeground);
  // Selected states (rail pill, popover rows) are text on a tinted surface: the
  // ink must stay readable even when the accent itself is white.
  const accentInk = ensureReadable(accent, accentSurface, 4.5, safeForeground);
  const navSurface = mix(background, accentInk, theme === "dark" ? 0.2 : 0.11);
  const navInk = ensureReadable(accentInk, navSurface, 4.5, safeForeground);
  const tokens: Record<string, string> = {
    "--background": hsl(background), "--foreground": hsl(foreground), "--card": hsl(surface), "--popover": hsl(surface),
    "--primary": hsl(primary), "--primary-foreground": hsl(readableOn(primary)), "--secondary": hsl(secondary), "--muted": hsl(secondary),
    "--muted-foreground": hsl(mix(background, foreground, 0.68)), "--accent": hsl(accentSurface),
    "--accent-foreground": hsl(accentInk), "--border": hsl(mix(background, safeForeground, 0.18)),
    "--surface-app": hsl(secondary), "--surface-panel": hsl(surface), "--sidebar": hsl(mix(background, safeForeground, 0.045)),
    "--sidebar-accent": hsl(mix(background, accent, 0.1)), "--inline-token-highlight": accent, "--temporary-control-active": accent,
    "--nav-active-bg": hsl(navSurface), "--nav-active-fg": hsl(navInk),
    "--nav-active-ring": hsl(mix(navSurface, navInk, 0.32)),
  };
  const hasOverrides = preferences.presetId === "custom" && Object.keys(preferences.custom?.[theme] ?? {}).length > 0;
  const base = preferences.presetId === "custom" ? preferences.custom?.basePreset : preferences.presetId;
  if (base === "zhixu" && !hasOverrides) Object.assign(tokens, theme === "dark" ? ZHIXU_DARK : ZHIXU_LIGHT);
  const aliases = {
    "--card-foreground": "--foreground", "--popover-foreground": "--foreground", "--secondary-foreground": "--foreground",
    "--input": "--border", "--ring": "--accent-foreground", "--border-soft": "--border",
    "--temporary-accent": "--accent-foreground", "--temporary-foreground": "--accent-foreground", "--temporary-border": "--accent-foreground",
    "--sidebar-foreground": "--foreground", "--sidebar-selected": "--accent", "--sidebar-accent-foreground": "--foreground",
    "--sidebar-border": "--border", "--settings-canvas": "--background", "--settings-surface": "--surface-panel",
  };
  for (const [key, source] of Object.entries(aliases)) tokens[key] = tokens[source];
  const ui = preferences.uiFont;
  const code = preferences.codeFont;
  tokens["--font-ui"] = ui.id === "custom" ? `"${normalizeFontFamily(ui.family) ?? "sans-serif"}", ${UI_SYSTEM}` : UI_FONTS[ui.id] ?? UI_SYSTEM;
  tokens["--font-code"] = code.id === "custom" ? `"${normalizeFontFamily(code.family) ?? "monospace"}", ${CODE_DEFAULT}` : CODE_FONTS[code.id] ?? CODE_DEFAULT;
  tokens["--font-display"] = tokens["--font-ui"];
  tokens["--appearance-safe-foreground"] = safeForeground;
  return tokens;
}

// A stable non-cryptographic signature detects stale cache/config pairs; values remain untrusted.
export function appearanceSignature(preferences: AppearancePreferences): string {
  const value = JSON.stringify(preferences);
  let hash = 2166136261;
  for (let i = 0; i < value.length; i++) hash = Math.imul(hash ^ value.charCodeAt(i), 16777619);
  return (hash >>> 0).toString(16);
}

export function serializeAppearance(preferences: AppearancePreferences): string {
  const canonical = normalizeAppearance(preferences);
  return JSON.stringify({ ...canonical, snapshot: {
    version: 1, signature: appearanceSignature(canonical),
    light: resolveAppearance(canonical, "light"), dark: resolveAppearance(canonical, "dark"),
  } });
}
