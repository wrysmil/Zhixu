// dock 快捷键解析单测（批次 home-dock-pty-terminal-v1.1 Task 11 / spec B2）。
// 语义：meta||ctrl + p → 文件标签、meta||ctrl + t → 浏览器标签；alt/shift 组合放行；
// 解析器不区分首页/会话页（无 isHome 限定），scope 由调用方（App）传入。
import { describe, expect, it } from "vitest";

import { resolveDockShortcut } from "@/lib/dock-shortcuts";

function key(
  k: string,
  mods: { meta?: boolean; ctrl?: boolean; alt?: boolean; shift?: boolean } = {},
): globalThis.KeyboardEvent {
  return new window.KeyboardEvent("keydown", {
    key: k,
    metaKey: !!mods.meta,
    ctrlKey: !!mods.ctrl,
    altKey: !!mods.alt,
    shiftKey: !!mods.shift,
    bubbles: true,
    cancelable: true,
  });
}

describe("resolveDockShortcut", () => {
  it("opens/focuses the dock tab for the active scope from anywhere", () => {
    expect(resolveDockShortcut(key("p", { meta: true }))).toBe("file");
    expect(resolveDockShortcut(key("t", { meta: true }))).toBe("browser");
    expect(resolveDockShortcut(key("t", { meta: true, shift: true }))).toBeNull();
  });

  it("accepts ctrl chords on non-mac platforms and ignores alt combinations", () => {
    expect(resolveDockShortcut(key("p", { ctrl: true }))).toBe("file");
    expect(resolveDockShortcut(key("t", { ctrl: true }))).toBe("browser");
    expect(resolveDockShortcut(key("p", { meta: true, alt: true }))).toBeNull();
    expect(resolveDockShortcut(key("t", { ctrl: true, alt: true }))).toBeNull();
  });

  it("passes through unrelated keys and unmodified typing", () => {
    expect(resolveDockShortcut(key("k", { meta: true }))).toBeNull();
    expect(resolveDockShortcut(key("p"))).toBeNull();
    expect(resolveDockShortcut(key("t", { shift: true }))).toBeNull();
  });

  it("gives terminal tabs no shortcut (only file/browser resolve)", () => {
    expect(resolveDockShortcut(key("e", { meta: true }))).toBeNull();
  });
});
