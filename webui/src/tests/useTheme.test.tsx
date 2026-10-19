import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTheme } from "@/hooks/useTheme";
import { APPEARANCE_STORAGE_KEY, DEFAULT_APPEARANCE, serializeAppearance } from "@/lib/appearance";

let media: EventTarget & { matches: boolean; addEventListener: EventTarget["addEventListener"]; removeEventListener: EventTarget["removeEventListener"] };
beforeEach(() => {
  localStorage.clear();
  media = Object.assign(new EventTarget(), { matches: false });
  vi.stubGlobal("matchMedia", vi.fn(() => media));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("appearance controller", () => {
  it("keeps browser chrome aligned with the actual selected background", () => {
    const meta = document.createElement("meta");
    meta.name = "theme-color";
    document.head.append(meta);
    const { result } = renderHook(() => useTheme({ persist: false }));
    expect(meta.content).toBe("#F8F6F2");
    act(() => result.current.setColor("background", "#123456"));
    expect(meta.content).toBe("#123456");
    act(() => result.current.setTheme("dark"));
    expect(meta.content).toBe("#161D1A");
    meta.remove();
  });

  it("retains the final pending edit on pagehide and restores it on remount", () => {
    const first = renderHook(() => useTheme());
    act(() => first.result.current.setPreset("ocean"));
    act(() => first.result.current.setColor("accent", "#123456"));
    act(() => window.dispatchEvent(new Event("pagehide")));
    first.unmount();
    const second = renderHook(() => useTheme());
    expect(second.result.current.preferences.custom?.light.accent).toBe("#123456");
    expect(second.result.current.preferences.custom?.basePreset).toBe("ocean");
  });

  it("keeps the last valid color and font when input is incomplete or unsafe", () => {
    const { result } = renderHook(() => useTheme({ persist: false }));
    act(() => result.current.setColor("accent", "#123456"));
    act(() => result.current.setColor("accent", "#12"));
    act(() => result.current.setFont("code", { id: "custom", family: "Local Mono" }));
    act(() => result.current.setFont("code", { id: "custom", family: "a; color:red" }));
    expect(result.current.preferences.custom?.light.accent).toBe("#123456");
    expect(result.current.preferences.codeFont.family).toBe("Local Mono");
  });

  it("toggle exits system mode with an explicit choice and removes its listener", () => {
    const remove = vi.spyOn(media, "removeEventListener");
    const { result } = renderHook(() => useTheme({ persist: false }));
    act(() => result.current.toggle());
    expect(result.current.theme).toBe("dark");
    expect(result.current.preferences.mode).toBe("dark");
    expect(remove).toHaveBeenCalledWith("change", expect.any(Function));
    act(() => result.current.setTheme("light"));
    expect(result.current.theme).toBe("light");
  });

  it("tracks system changes only in system mode", () => {
    const { result, unmount } = renderHook(() => useTheme());
    expect(result.current.preferences.mode).toBe("system");
    act(() => { media.matches = true; media.dispatchEvent(new Event("change")); });
    expect(result.current.theme).toBe("dark");
    act(() => result.current.setMode("light"));
    act(() => { media.matches = true; media.dispatchEvent(new Event("change")); });
    expect(result.current.theme).toBe("light");
    unmount();
  });

  it("migrates explicit legacy mode and saves full preferences", async () => {
    localStorage.setItem("zhixu-webui.theme", "dark");
    const { result } = renderHook(() => useTheme());
    expect(result.current.preferences.mode).toBe("dark");
    act(() => result.current.setColor("accent", "abc"));
    expect(result.current.preferences.custom?.dark.accent).toBe("#AABBCC");
    await act(() => new Promise((resolve) => setTimeout(resolve, 180)));
    expect(JSON.parse(localStorage.getItem(APPEARANCE_STORAGE_KEY)!).custom.dark.accent).toBe("#AABBCC");
  });

  it("keeps custom colors across modes and preset switches and supports field/default reset", () => {
    const { result } = renderHook(() => useTheme({ persist: false }));
    act(() => result.current.setMode("light"));
    act(() => result.current.setPreset("ocean"));
    act(() => result.current.setColor("accent", "#123456"));
    act(() => result.current.setMode("dark"));
    act(() => result.current.setColor("background", "#223344"));
    act(() => result.current.setPreset("sand"));
    act(() => result.current.setPreset("custom"));
    expect(result.current.preferences.custom).toEqual({ basePreset: "ocean", light: { accent: "#123456" }, dark: { background: "#223344" } });
    act(() => result.current.resetColor("background"));
    expect(result.current.preferences.custom?.dark).toEqual({});
    act(() => result.current.setFont("ui", { id: "custom", family: "Local Font" }));
    expect(document.documentElement.style.getPropertyValue("--font-ui")).toContain('"Local Font"');
    act(() => result.current.resetAppearance());
    expect(result.current.preferences).toEqual(DEFAULT_APPEARANCE);
  });

  it("isolates preview from all real storage access and storage events", () => {
    const get = vi.spyOn(localStorage, "getItem");
    const set = vi.spyOn(localStorage, "setItem");
    const { result } = renderHook(() => useTheme({ persist: false }));
    act(() => result.current.setMode("dark"));
    act(() => window.dispatchEvent(new StorageEvent("storage", { key: APPEARANCE_STORAGE_KEY,
      newValue: serializeAppearance({ ...DEFAULT_APPEARANCE, mode: "light" }) })));
    expect(result.current.theme).toBe("dark");
    expect(get).not.toHaveBeenCalled();
    expect(set).not.toHaveBeenCalled();
  });

  it("does not overwrite future schemas until a user makes an edit", async () => {
    const future = JSON.stringify({ schemaVersion: 42, mode: "dark" });
    localStorage.setItem(APPEARANCE_STORAGE_KEY, future);
    const { result } = renderHook(() => useTheme());
    await act(() => new Promise((resolve) => setTimeout(resolve, 180)));
    expect(localStorage.getItem(APPEARANCE_STORAGE_KEY)).toBe(future);
    act(() => result.current.setPreset("graphite"));
    await act(() => new Promise((resolve) => setTimeout(resolve, 180)));
    expect(JSON.parse(localStorage.getItem(APPEARANCE_STORAGE_KEY)!).schemaVersion).toBe(1);
  });

  it("syncs storage changes without a write loop or stale pending write", async () => {
    const { result } = renderHook(() => useTheme());
    const set = vi.spyOn(localStorage, "setItem");
    act(() => result.current.setColor("accent", "#123456"));
    act(() => window.dispatchEvent(new StorageEvent("storage", { key: APPEARANCE_STORAGE_KEY,
      newValue: serializeAppearance({ ...DEFAULT_APPEARANCE, mode: "dark", presetId: "sand" }) })));
    expect(result.current.preferences.presetId).toBe("sand");
    await act(() => new Promise((resolve) => setTimeout(resolve, 180)));
    expect(set).not.toHaveBeenCalled();
  });

  it("reports storage failures but still applies user changes", async () => {
    vi.spyOn(localStorage, "setItem").mockImplementation(() => { throw new Error("quota"); });
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setMode("dark"));
    await act(() => new Promise((resolve) => setTimeout(resolve, 180)));
    expect(result.current.theme).toBe("dark");
    expect(result.current.storageError).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
