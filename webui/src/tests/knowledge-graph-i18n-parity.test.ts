import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const LOCALES = [
  "en",
  "zh-CN",
  "zh-TW",
  "ja",
  "ko",
  "vi",
  "fr",
  "es",
  "pt-BR",
  "id",
];
const ROOT = resolve(__dirname, "../..");

type Json = Record<string, unknown>;

function loadLocale(lang: string): Json {
  return JSON.parse(
    readFileSync(join(ROOT, "src/i18n/locales", lang, "common.json"), "utf8"),
  );
}

function leafKeys(data: unknown, prefix = ""): string[] {
  if (typeof data !== "object" || data === null) return prefix ? [prefix] : [];
  const out: string[] = [];
  for (const [key, value] of Object.entries(data as Json)) {
    const path = prefix ? `${prefix}.${key}` : key;
    out.push(...leafKeys(value, path));
  }
  return out;
}

function graphKeys(lang: string): string[] {
  const data = loadLocale(lang);
  const knowledge = data.knowledge as Json | undefined;
  return leafKeys(knowledge?.graph ?? {}).sort();
}

describe("knowledge.graph i18n parity", () => {
  it("uses the same key set across all 10 locales", () => {
    const base = graphKeys("en");
    expect(base.length).toBeGreaterThanOrEqual(25);
    for (const lang of LOCALES) {
      expect(graphKeys(lang), lang).toEqual(base);
    }
  });
});
