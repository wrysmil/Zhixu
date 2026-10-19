import { describe, expect, it } from "vitest";
import {
  DEFAULT_APPEARANCE, THEME_PRESETS, contrastRatio, getPalette,
  normalizeAppearance, normalizeFontFamily, normalizeHex, resolveAppearance,
  serializeAppearance,
} from "@/lib/appearance";

describe("appearance model", () => {
  it("accepts complete HEX colors and rejects CSS values", () => {
    expect(normalizeHex(" abc ")).toBe("#AABBCC");
    expect(normalizeHex("#0169cc")).toBe("#0169CC");
    for (const value of ["", "#12", "#1234", "red", "#123456; color:red", "var(--color)"]) {
      expect(normalizeHex(value)).toBeNull();
    }
  });

  it("validates a single local font family without accepting CSS injection", () => {
    expect(normalizeFontFamily(" 思源黑体 ")).toBe("思源黑体");
    expect(normalizeFontFamily("JetBrains Mono")).toBe("JetBrains Mono");
    for (const value of ["", "a; color:red", "font, serif", "a\nfont", "url(foo)", "a\"b", "a".repeat(81)]) {
      expect(normalizeFontFamily(value)).toBeNull();
    }
  });

  it("preserves default Zhixu tokens exactly and supplies complete preset palettes", () => {
    expect(resolveAppearance(DEFAULT_APPEARANCE, "light")["--background"]).toBe("42 32% 96%");
    expect(resolveAppearance(DEFAULT_APPEARANCE, "light")["--primary"]).toBe("154 29% 29%");
    expect(resolveAppearance(DEFAULT_APPEARANCE, "dark")["--background"]).toBe("153 13% 10%");
    expect(resolveAppearance(DEFAULT_APPEARANCE, "dark")["--primary"]).toBe("143 26% 66%");
    expect(THEME_PRESETS).toHaveLength(4);
    for (const preset of THEME_PRESETS) {
      for (const theme of ["light", "dark"] as const) {
        for (const color of Object.values(preset[theme])) expect(normalizeHex(color)).toBe(color);
      }
    }
  });

  it("applies only current-mode overrides and keeps backgrounds and foregrounds exact", () => {
    const preferences = normalizeAppearance({ ...DEFAULT_APPEARANCE, presetId: "custom",
      custom: { basePreset: "ocean", light: { background: "#ffffff", foreground: "#ffffff" }, dark: { accent: "#ff0000" } },
    });
    expect(getPalette(preferences, "light")).toMatchObject({ background: "#FFFFFF", foreground: "#FFFFFF" });
    expect(getPalette(preferences, "dark").accent).toBe("#FF0000");
    expect(resolveAppearance(preferences, "light")["--appearance-safe-foreground"]).toBe("#0D0D0D");
    expect(contrastRatio("#000000", "#FFFFFF")).toBe(21);
  });

  it("recovers valid fields individually from malformed storage", () => {
    const preferences = normalizeAppearance({ schemaVersion: 1, mode: "dark", presetId: "unknown",
      custom: { basePreset: "sand", light: { accent: "bad!", background: "#123" }, dark: [] },
      uiFont: { id: "custom", family: "Valid Font" }, codeFont: { id: "custom", family: "x; color:red" },
    });
    expect(preferences.mode).toBe("dark");
    expect(preferences.presetId).toBe("zhixu");
    expect(preferences.custom?.light).toEqual({ background: "#112233" });
    expect(preferences.custom?.dark).toEqual({});
    expect(preferences.uiFont).toEqual({ id: "custom", family: "Valid Font" });
    expect(preferences.codeFont).toEqual(DEFAULT_APPEARANCE.codeFont);
    expect(normalizeAppearance({ schemaVersion: 99, mode: "dark" })).toEqual(DEFAULT_APPEARANCE);
  });

  it("serializes two mode snapshots with a preferences signature", () => {
    const saved = JSON.parse(serializeAppearance(DEFAULT_APPEARANCE));
    expect(saved.snapshot.version).toBe(1);
    expect(saved.snapshot.signature).toEqual(expect.any(String));
    expect(saved.snapshot.light).toEqual(resolveAppearance(DEFAULT_APPEARANCE, "light"));
    expect(saved.snapshot.dark).toEqual(resolveAppearance(DEFAULT_APPEARANCE, "dark"));
  });
});
