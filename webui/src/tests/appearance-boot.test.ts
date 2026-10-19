import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { APPEARANCE_STORAGE_KEY, DEFAULT_APPEARANCE, normalizeAppearance, resolveAppearance, serializeAppearance } from "@/lib/appearance";

const html = readFileSync(resolve(process.cwd(), "index.html"), "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((match) => match[1]);
function boot(dark = false) {
  vi.stubGlobal("matchMedia", () => ({ matches: dark }));
  for (const script of scripts.slice(0, 1)) window.eval(script);
}
beforeEach(() => {
  window.history.replaceState(null, "", "/");
  localStorage.clear();
  document.documentElement.removeAttribute("style");
  document.documentElement.removeAttribute("class");
  delete document.documentElement.dataset.appearanceTheme;
  document.head.innerHTML = '<meta name="theme-color" content="#ffffff" />';
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

describe("appearance before React startup", () => {
  it.each(["light", "dark"] as const)("restores all validated %s colors and fonts consistently with runtime", (theme) => {
    const preferences = normalizeAppearance({ ...DEFAULT_APPEARANCE, presetId: "custom",
      custom: { basePreset: "ocean", light: { background: "#AABBCC" }, dark: { background: "#112233" } },
      uiFont: { id: "custom", family: "本机字体" }, codeFont: { id: "consolas", family: "" },
    });
    localStorage.setItem(APPEARANCE_STORAGE_KEY, serializeAppearance(preferences));
    boot(theme === "dark");
    for (const [key, value] of Object.entries(resolveAppearance(preferences, theme))) {
      expect(document.documentElement.style.getPropertyValue(key), key).toBe(value);
    }
    expect(document.documentElement.classList.contains("dark")).toBe(theme === "dark");
    expect(document.documentElement.dataset.appearanceTheme).toBe("custom");
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute("content")).toBe(theme === "dark" ? "#112233" : "#AABBCC");
  });

  it("ignores stale signatures and unsafe snapshot properties", () => {
    const stored = JSON.parse(serializeAppearance(DEFAULT_APPEARANCE));
    stored.snapshot.light["--background"] = "0 0% 0%";
    stored.snapshot.dark["--font-ui"] = 'serif; background: url(https://invalid)';
    localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify(stored));
    boot();
    expect(document.documentElement.style.getPropertyValue("--background")).toBe("42 32% 96%");
    stored.snapshot.dark["--font-ui"] = stored.snapshot.light["--font-ui"];
    stored.mode = "dark";
    localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify(stored));
    boot();
    expect(document.documentElement.style.getPropertyValue("--background")).toBe("153 13% 10%");
  });

  it("does not apply arbitrary properties from a snapshot", () => {
    const stored = JSON.parse(serializeAppearance(DEFAULT_APPEARANCE));
    stored.snapshot.light["background-image"] = "url(https://invalid)";
    localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify(stored));
    boot();
    expect(document.documentElement.style.getPropertyValue("background-image")).toBe("");
  });

  it("accepts a saved palette regardless of the order colors were edited", () => {
    const preferences = { ...DEFAULT_APPEARANCE, presetId: "custom" as const,
      custom: { basePreset: "ocean" as const, light: { foreground: "#112233", background: "#DDEEFF", accent: "#445566" }, dark: {} },
    };
    localStorage.setItem(APPEARANCE_STORAGE_KEY, serializeAppearance(preferences));
    boot();
    expect(document.documentElement.style.getPropertyValue("--background")).toBe(resolveAppearance(preferences, "light")["--background"]);
  });

  it("does not read or migrate real preferences in the offline preview", () => {
    window.history.replaceState(null, "", "/?preview=1");
    const get = vi.spyOn(localStorage, "getItem");
    const set = vi.spyOn(localStorage, "setItem");
    boot();
    expect(get).not.toHaveBeenCalled();
    expect(set).not.toHaveBeenCalled();
    expect(document.documentElement.dataset.appearanceTheme).toBe("zhixu");
  });

  it("migrates the original brand theme key on a fresh page", () => {
    localStorage.setItem("nanobot-webui.theme", "dark");
    boot();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("zhixu-webui.theme")).toBe("dark");
    expect(localStorage.getItem("nanobot-webui.theme")).toBeNull();
    expect(document.documentElement.style.getPropertyValue("--background")).toBe("153 13% 10%");
  });

  it("uses defaults for unknown versions and malformed JSON without overwriting them", () => {
    for (const value of ['{"schemaVersion":99,"mode":"dark"}', "not-json"]) {
      localStorage.setItem(APPEARANCE_STORAGE_KEY, value);
      boot();
      expect(document.documentElement.classList.contains("dark")).toBe(false);
      expect(localStorage.getItem(APPEARANCE_STORAGE_KEY)).toBe(value);
    }
  });
});
