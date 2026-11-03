// 评估 UI i18n 键表收口用例。
// 语义：评估组件消费的每一个 settings.evaluation.* 键（含 `base + "..."` 拼接形式）
// 必须在 10 个语言包里都有真实条目——组件里的 defaultValue 只是兜底，
// 不能当作缺键豁免；缺键会在页面上直接漏出裸键（settings.evaluation....）。
// 同时锁定 settings.evaluation 子树的键集在 10 语言之间完全一致。
import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const LOCALES = ["en", "zh-CN", "zh-TW", "ja", "ko", "vi", "fr", "es", "pt-BR", "id"];
const ROOT = resolve(__dirname, "../..");
const EVAL_DIR = join(ROOT, "src/components/settings/evaluation");

type Json = Record<string, unknown>;

function loadLocale(lang: string): Json {
  return JSON.parse(readFileSync(join(ROOT, "src/i18n/locales", lang, "common.json"), "utf8"));
}

/** 沿点号路径取值，取不到返回 undefined。 */
function pick(data: Json, path: string): unknown {
  let cur: unknown = data;
  for (const seg of path.split(".")) {
    if (typeof cur !== "object" || cur === null) return undefined;
    cur = (cur as Json)[seg];
  }
  return cur;
}

/** 收集对象树里的所有叶子键路径。 */
function leafKeys(data: unknown, prefix = ""): string[] {
  if (typeof data !== "object" || data === null) return prefix ? [prefix] : [];
  const out: string[] = [];
  for (const [k, v] of Object.entries(data as Json)) {
    const p = prefix ? `${prefix}.${k}` : k;
    out.push(...leafKeys(v, p));
  }
  return out;
}

/** 扫描评估组件，解析出所有被消费的 settings.evaluation.* 键。 */
function collectConsumedKeys(): Set<string> {
  const keys = new Set<string>();
  for (const name of readdirSync(EVAL_DIR)) {
    if (!/\.tsx?$/.test(name)) continue;
    const source = readFileSync(join(EVAL_DIR, name), "utf8");

    // 1) 直接字面量：t("settings.evaluation....")
    for (const m of source.matchAll(/\bt\(\s*"(settings\.evaluation\.[A-Za-z0-9_.]+)"/g)) {
      keys.add(m[1]);
    }

    // 2) 常量前缀：const base = "settings.evaluation...."
    const bases = new Map<string, string>();
    for (const m of source.matchAll(/(?:const|let)\s+([A-Za-z0-9_]+)\s*=\s*"(settings\.evaluation\.[A-Za-z0-9_.]*)"/g)) {
      bases.set(m[1], m[2]);
    }

    // 3) 拼接形式：base + "leaf"（可多级拼接）
    for (const [varName, prefix] of bases) {
      const pattern = new RegExp(`\\b${varName}\\s*\\+\\s*"([^"]+)"`, "g");
      for (const m of source.matchAll(pattern)) keys.add(`${prefix}${m[1]}`);
    }
  }
  return keys;
}

describe("i18n evaluation key parity", () => {
  it("defines every consumed settings.evaluation key in all 10 locales", () => {
    const consumed = [...collectConsumedKeys()];
    expect(consumed.length).toBeGreaterThan(0);
    for (const lang of LOCALES) {
      const data = loadLocale(lang);
      for (const key of consumed) {
        expect(pick(data, key), `${key} missing in ${lang}`).toBeTypeOf("string");
      }
    }
  });

  it("keeps the settings.evaluation key set identical across all 10 locales", () => {
    const baseline = leafKeys(pick(loadLocale("en"), "settings.evaluation")).sort();
    expect(baseline.length).toBeGreaterThan(0);
    for (const lang of LOCALES) {
      const current = leafKeys(pick(loadLocale(lang), "settings.evaluation")).sort();
      expect(current, `settings.evaluation key set differs in ${lang}`).toEqual(baseline);
    }
  });

  it("keeps the four keys that used to leak raw ids defined in every locale", () => {
    const required = [
      "settings.evaluation.reliability.calibration.unknown",
      "settings.evaluation.reliability.reviews.failed",
      "settings.evaluation.reliability.scorecard.taskCompletionHint",
      "settings.evaluation.reliability.scorecard.qualificationHint",
    ];
    for (const lang of LOCALES) {
      const data = loadLocale(lang);
      for (const key of required) {
        expect(pick(data, key), `${key} missing in ${lang}`).toBeTypeOf("string");
      }
    }
  });
});
