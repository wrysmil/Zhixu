import {
  createContext, createElement, useCallback, useContext, useEffect, useLayoutEffect,
  useRef, useState, type ReactNode,
} from "react";
import {
  APPEARANCE_APPLIED_EVENT, APPEARANCE_STORAGE_KEY, DEFAULT_APPEARANCE, THEME_PRESETS,
  getPalette, normalizeAppearance, normalizeFontChoice, normalizeHex, resolveAppearance,
  serializeAppearance, type AppearanceController, type AppearancePreferences, type Theme,
} from "@/lib/appearance";

const LEGACY_THEME_KEY = "zhixu-webui.theme";
const ThemeContext = createContext<Theme>("light");
const AppearanceContext = createContext<AppearanceController | null>(null);

function systemTheme(): Theme {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function readInitial(persist: boolean): { preferences: AppearancePreferences; writable: boolean; error: boolean } {
  const initial = { preferences: structuredClone(DEFAULT_APPEARANCE), writable: true, error: false };
  if (!persist) return initial;
  try {
    const stored = localStorage.getItem(APPEARANCE_STORAGE_KEY);
    if (stored !== null) {
      try {
        const value: unknown = JSON.parse(stored);
        return { ...initial, preferences: normalizeAppearance(value),
          writable: !(value && typeof value === "object" && "schemaVersion" in value && typeof value.schemaVersion === "number" && value.schemaVersion > 1) };
      } catch { return initial; }
    }
    const legacy = localStorage.getItem(LEGACY_THEME_KEY) ?? localStorage.getItem("nanobot-webui.theme");
    if (legacy === "light" || legacy === "dark") initial.preferences.mode = legacy;
    return initial;
  } catch { return { ...initial, error: true }; }
}

function applyAppearance(preferences: AppearancePreferences, theme: Theme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.style.colorScheme = theme;
  for (const [property, value] of Object.entries(resolveAppearance(preferences, theme))) root.style.setProperty(property, value);
  root.dataset.appearanceTheme = preferences.presetId;
  root.dataset.appearanceFont = preferences.uiFont.id;
  const themeColor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (themeColor) themeColor.content = getPalette(preferences, theme).background;
  window.dispatchEvent(new CustomEvent(APPEARANCE_APPLIED_EVENT));
}

export function useTheme(options: { persist?: boolean } = {}): AppearanceController & { toggle(): void; setTheme(t: Theme): void } {
  const persist = options.persist !== false;
  const [initial] = useState(() => readInitial(persist));
  const [preferences, setPreferences] = useState(initial.preferences);
  const [storageError, setStorageError] = useState(initial.error);
  const [system, setSystem] = useState(systemTheme);
  const preferencesRef = useRef(preferences);
  const writable = useRef(initial.writable);
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);
  const theme = preferences.mode === "system" ? system : preferences.mode;

  const save = useCallback((next: AppearancePreferences) => {
    if (!persist || !writable.current) return;
    try {
      localStorage.setItem(APPEARANCE_STORAGE_KEY, serializeAppearance(next));
      localStorage.setItem(LEGACY_THEME_KEY, next.mode === "system" ? systemTheme() : next.mode);
      setStorageError(false);
    } catch { setStorageError(true); }
  }, [persist]);

  const update = useCallback((transform: (previous: AppearancePreferences) => AppearancePreferences) => {
    const next = transform(preferencesRef.current);
    if (next === preferencesRef.current) return;
    writable.current = true;
    preferencesRef.current = next;
    setPreferences(next);
    if (!persist) return;
    if (pending.current !== null) clearTimeout(pending.current);
    pending.current = setTimeout(() => { pending.current = null; save(next); }, 120);
  }, [persist, save]);

  useLayoutEffect(() => { applyAppearance(preferences, theme); }, [preferences, theme]);

  // https://developer.mozilla.org/en-US/docs/Web/API/MediaQueryList/change_event
  useEffect(() => {
    if (preferences.mode !== "system" || !window.matchMedia) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const change = () => setSystem(media.matches ? "dark" : "light");
    change();
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, [preferences.mode]);

  useEffect(() => {
    if (!persist) return;
    // Repair old/malformed cache once on mount; preserve unsupported future data.
    save(initial.preferences);
    const flush = () => {
      if (pending.current === null) return;
      clearTimeout(pending.current);
      pending.current = null;
      save(preferencesRef.current);
    };
    const storage = (event: StorageEvent) => {
      if (event.key !== APPEARANCE_STORAGE_KEY && event.key !== null) return;
      if (event.storageArea && event.storageArea !== localStorage) return;
      if (pending.current !== null) clearTimeout(pending.current);
      pending.current = null;
      let value: unknown = null;
      try { value = event.newValue ? JSON.parse(event.newValue) : null; } catch { /* Corrupt remote values use safe defaults. */ }
      writable.current = !(value && typeof value === "object" && "schemaVersion" in value && typeof value.schemaVersion === "number" && value.schemaVersion > 1);
      const next = normalizeAppearance(value);
      preferencesRef.current = next;
      setPreferences(next);
      setStorageError(false);
    };
    window.addEventListener("storage", storage);
    window.addEventListener("pagehide", flush);
    return () => {
      flush();
      window.removeEventListener("storage", storage);
      window.removeEventListener("pagehide", flush);
    };
  }, [initial, persist, save]);

  const setMode = useCallback<AppearanceController["setMode"]>((mode) => {
    if (mode === "light" || mode === "dark" || mode === "system") update((previous) => ({ ...previous, mode }));
  }, [update]);
  const setPreset = useCallback<AppearanceController["setPreset"]>((presetId) => {
    update((previous) => {
      if (presetId === "custom" ? !previous.custom : !THEME_PRESETS.some((item) => item.id === presetId)) return previous;
      return { ...previous, presetId };
    });
  }, [update]);
  const setColor = useCallback<AppearanceController["setColor"]>((field, value) => {
    const hex = normalizeHex(value);
    if (!hex || !["accent", "background", "foreground"].includes(field)) return;
    update((previous) => {
      const custom = previous.presetId === "custom" && previous.custom ? previous.custom : {
        basePreset: previous.presetId === "custom" ? "zhixu" as const : previous.presetId, light: {}, dark: {},
      };
      const mode = previous.mode === "system" ? systemTheme() : previous.mode;
      return { ...previous, presetId: "custom", custom: { ...custom, [mode]: { ...custom[mode], [field]: hex } } };
    });
  }, [update]);
  const resetColor = useCallback<AppearanceController["resetColor"]>((field) => {
    update((previous) => {
      if (previous.presetId !== "custom" || !previous.custom) return previous;
      const mode = previous.mode === "system" ? systemTheme() : previous.mode;
      const overrides = { ...previous.custom[mode] };
      delete overrides[field];
      return { ...previous, custom: { ...previous.custom, [mode]: overrides } };
    });
  }, [update]);
  const setFont = useCallback<AppearanceController["setFont"]>((kind, choice) => {
    if (kind !== "ui" && kind !== "code") return;
    const normalized = normalizeFontChoice(choice, kind);
    if (choice.id === "custom" && normalized.id !== "custom") return;
    update((previous) => ({ ...previous, [kind === "ui" ? "uiFont" : "codeFont"]: normalized }));
  }, [update]);
  const resetAppearance = useCallback(() => update(() => structuredClone(DEFAULT_APPEARANCE)), [update]);
  const toggle = useCallback(() => setMode(theme === "dark" ? "light" : "dark"), [setMode, theme]);
  return { preferences, theme, storageError, setMode, setPreset, setColor, resetColor, setFont, resetAppearance, toggle, setTheme: setMode };
}

export function ThemeProvider({ theme, appearance, children }: { theme: Theme; appearance?: AppearanceController; children: ReactNode }) {
  return createElement(ThemeContext.Provider, { value: theme },
    createElement(AppearanceContext.Provider, { value: appearance ?? null }, children));
}
export function useAppearance(): AppearanceController | null { return useContext(AppearanceContext); }
export function useThemeValue(): Theme { return useContext(ThemeContext); }
