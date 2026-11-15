import { describe, expect, it } from "vitest";

import {
  blockMatchesChunk,
  findChunkRange,
  injectMarkdownMarks,
  normalizeForMatch,
  splitHighlighted,
} from "@/lib/knowledge-highlight";

describe("knowledge highlight", () => {
  it("locates an exact chunk in the source", () => {
    const source = "一、适用范围\n\n随访频次按血压分级执行。";
    const range = findChunkRange(source, "随访频次按血压分级执行。");
    expect(range).toEqual({ start: 8, end: 20 });
  });

  it("falls back to whitespace-insensitive matching", () => {
    const source = "随访频次\n\n  按血压分级   执行。";
    const range = findChunkRange(source, "随访频次 按血压分级 执行。");
    expect(range).not.toBeNull();
    expect(source.slice(range!.start, range!.end)).toBe("随访频次\n\n  按血压分级   执行。");
  });

  it("matches chunks whose punctuation was normalized to half width at ingest", () => {
    const source = "随访频次按血压分级执行：1 级每 3 个月一次，2 级每 2 个月一次。";
    const chunk = "随访频次按血压分级执行:1 级每 3 个月一次,2 级每 2 个月一次。";
    const range = findChunkRange(source, chunk);
    expect(range).not.toBeNull();
    expect(source.slice(range!.start, range!.end)).toBe(source);
  });

  it("retries with a shorter prefix when the tail differs", () => {    const source = "三、随访频次\n\n随访频次按血压分级执行：1 级每 3 个月。";
    const chunk = `${"三、随访频次\n\n随访频次按血压分级执行：1 级每 3 个月。"} 其余内容已被截断`;
    const range = findChunkRange(source, chunk);
    expect(range).not.toBeNull();
    expect(source.slice(range!.start, range!.end)).toContain("随访频次按血压分级执行");
  });

  it("returns null when the chunk is absent", () => {
    expect(findChunkRange("无关内容", "随访频次")).toBeNull();
    expect(normalizeForMatch("  A\n\n B  ")).toBe("a b");
  });

  it("splits plain text into hit and non-hit segments", () => {
    expect(splitHighlighted("abcdef", { start: 2, end: 4 })).toEqual([
      { text: "ab", hit: false },
      { text: "cd", hit: true },
      { text: "ef", hit: false },
    ]);
    expect(splitHighlighted("abcdef", null)).toEqual([{ text: "abcdef", hit: false }]);
  });

  it("wraps markdown paragraphs while keeping heading markers outside the mark", () => {
    const source = "## 三、随访频次\n\n随访频次按血压分级执行。\n\n四、用药";
    const needle = "## 三、随访频次\n\n随访频次按血压分级执行。";
    const output = injectMarkdownMarks(source, findChunkRange(source, needle));
    expect(output).toBe(
      "## <mark>三、随访频次</mark>\n\n<mark>随访频次按血压分级执行。</mark>\n\n四、用药",
    );
  });

  it("refuses to inject marks into code fences or tables", () => {
    const fenced = "```\ncode\n```";
    expect(injectMarkdownMarks(fenced, findChunkRange(fenced, fenced))).toBeNull();
    const table = "| a | b |\n| - | - |\n| 1 | 2 |";
    expect(injectMarkdownMarks(table, findChunkRange(table, table))).toBeNull();
  });

  it("matches docx blocks that lie inside the cited chunk", () => {
    expect(blockMatchesChunk("随访频次按血压分级执行。", "三、随访频次\n随访频次按血压分级执行。")).toBe(true);
    expect(blockMatchesChunk("无关段落", "随访频次按血压分级执行。")).toBe(false);
    expect(blockMatchesChunk("短", "短")).toBe(false);
  });
});
