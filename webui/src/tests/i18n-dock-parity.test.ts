// dock/startPanel i18n 键表收口用例（批次 home-dock-pty-terminal-v1.1 Task 12）。
// 语义：10 语言的 dock.* 键集必须相等；代码中所有字面量消费键（t("dock.*")/t("startPanel.*")）
// 必须在每个 locale 有真实条目（组件 defaultValue 只是兜底，不作为缺键豁免）；
// startPanel.shellUnavailable 补键齐备；已弃用的 workbench.fileTree 不得回流。
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const LOCALES = ["en", "zh-CN", "zh-TW", "ja", "ko", "vi", "fr", "es", "pt-BR", "id"];
const ROOT = resolve(__dirname, "../..");

const DOCK_KEYS = [
  "tabFile",
  "tabBrowser",
  "plusAria",
  "newTerminal",
  "shellChoices",
  "takeoverRunning",
  "takeoverAction",
  "takeoverHint",
  "closeTab",
  "platformDefaultShell",
];

function loadLocale(lang: string): Record<string, Record<string, string>> {
  return JSON.parse(readFileSync(join(ROOT, "src/i18n/locales", lang, "common.json"), "utf8"));
}

// 扫描 src 下所有 ts/tsx 源码（排除测试与 locale），收集字面量消费键。
function collectConsumedKeys(): Set<string> {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (name === "tests" || name === "i18n" || name === "node_modules") continue;
      if (statSync(p).isDirectory()) walk(p);
      else if (/\.(ts|tsx)$/.test(name)) files.push(p);
    }
  };
  walk(join(ROOT, "src"));
  const pattern = /\bt\("((?:dock|startPanel)\.[A-Za-z_]+)"/g;
  const keys = new Set<string>();
  for (const f of files) {
    for (const m of readFileSync(f, "utf8").matchAll(pattern)) keys.add(m[1]);
  }
  return keys;
}

describe("i18n dock key parity", () => {
  it("keeps the dock.* key set identical across all 10 locales", () => {
    for (const lang of LOCALES) {
      const dock = loadLocale(lang).dock;
      expect(Object.keys(dock).sort(), `dock keys of ${lang}`).toEqual([...DOCK_KEYS].sort());
    }
  });

  it("defines every consumed dock/startPanel key in all 10 locales", () => {
    const consumed = [...collectConsumedKeys()];
    expect(consumed.length).toBeGreaterThan(0);
    for (const lang of LOCALES) {
      const data = loadLocale(lang);
      for (const key of consumed) {
        const [ns, leaf] = key.split(".");
        expect(data[ns]?.[leaf], `${key} missing in ${lang}`).toBeTypeOf("string");
      }
    }
  });

  it("carries the startPanel.shellUnavailable fallback in all 10 locales", () => {
    for (const lang of LOCALES) {
      const value = loadLocale(lang).startPanel?.shellUnavailable;
      expect(value, `shellUnavailable missing in ${lang}`).toBeTruthy();
    }
  });

  it("keeps the retired workbench.fileTree key removed", () => {
    for (const lang of LOCALES) {
      expect(loadLocale(lang).workbench, `workbench.fileTree still present in ${lang}`).not.toHaveProperty(
        "fileTree",
      );
    }
  });
});
