export interface SourceRange {
  start: number;
  end: number;
}

export interface HighlightSegment {
  text: string;
  hit: boolean;
}

interface NormalizedIndex {
  text: string;
  /** normalized 字符 → 原文下标 */
  positions: number[];
}

/**
 * 折叠空白、做 NFKC 归一（入库切块会把全角标点转半角）并转小写，
 * 同时记录每个输出字符在原文里的下标，便于回找高亮区间。
 */
function normalizeWithMap(value: string): NormalizedIndex {
  let text = "";
  const positions: number[] = [];
  let pendingSpace = false;
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (/\s/.test(char)) {
      if (text.length > 0) pendingSpace = true;
      continue;
    }
    if (pendingSpace) {
      text += " ";
      positions.push(index);
      pendingSpace = false;
    }
    for (const output of char.normalize("NFKC").toLowerCase()) {
      text += output;
      positions.push(index);
    }
  }
  return { text, positions };
}

export function normalizeForMatch(value: string): string {
  return normalizeWithMap(value).text;
}

const PREFIX_FALLBACKS = [400, 120, 48, 24];

/**
 * 在原文里定位检索片段。
 *
 * 先做精确匹配（块内容本来就来自同一份抽取文本）；失败时按忽略空白的
 * 形式匹配，再用逐级缩短的前缀兜底，避免尾部差异导致整段高亮丢失。
 */
export function findChunkRange(source: string, chunk: string): SourceRange | null {
  const needle = chunk.trim();
  if (!needle || !source) return null;
  const direct = source.indexOf(needle);
  if (direct >= 0) return { start: direct, end: direct + needle.length };

  const haystack = normalizeWithMap(source);
  const candidates = [needle];
  for (const size of PREFIX_FALLBACKS) {
    if (needle.length > size) candidates.push(needle.slice(0, size));
  }  for (const candidate of candidates) {
    const normalized = normalizeWithMap(candidate).text;
    if (!normalized) continue;
    const index = haystack.text.indexOf(normalized);
    if (index < 0) continue;
    const start = haystack.positions[index];
    const end = haystack.positions[index + normalized.length - 1] + 1;
    return { start, end };
  }
  return null;
}

export function splitHighlighted(text: string, range: SourceRange | null): HighlightSegment[] {
  if (!range || range.start >= range.end || range.start >= text.length) {
    return text ? [{ text, hit: false }] : [];
  }
  const start = Math.max(0, range.start);
  const end = Math.min(text.length, range.end);
  const segments: HighlightSegment[] = [];
  if (start > 0) segments.push({ text: text.slice(0, start), hit: false });
  segments.push({ text: text.slice(start, end), hit: true });
  if (end < text.length) segments.push({ text: text.slice(end), hit: false });
  return segments;
}

const HEADING_PREFIX = /^(#{1,6}\s+)/;
const LIST_PREFIX = /^((?:[-*+]|\d+\.)\s+)/;

/**
 * 在 Markdown 原文里给命中段落包 `<mark>`。
 *
 * 逐段注入而不是整段包裹：跨空行或跨代码块/表格的 `<mark>` 会破坏
 * Markdown 结构。列表/标题的标记留在 `<mark>` 外面，避免 `##` 变成正文。
 */
export function injectMarkdownMarks(source: string, range: SourceRange | null): string | null {
  if (!range || range.start >= range.end) return null;
  const matched = source.slice(range.start, range.end);
  if (!matched.trim()) return null;
  if (matched.includes("```")) return null;

  const blocks: Array<{ start: number; end: number }> = [];
  const separator = /\n[ \t]*\n+/g;
  let last = 0;
  for (const match of matched.matchAll(separator)) {
    blocks.push({ start: last, end: match.index });
    last = match.index + match[0].length;
  }
  blocks.push({ start: last, end: matched.length });

  let marked = false;
  let output = source.slice(0, range.start);
  let cursor = range.start;
  for (const block of blocks) {
    const blockStart = range.start + block.start;
    const blockEnd = range.start + block.end;
    const blockText = source.slice(blockStart, blockEnd);
    if (!blockText.trim() || /^\s*\|/.test(blockText) || blockText.includes("```")) {
      output += source.slice(cursor, blockEnd);
      cursor = blockEnd;
      continue;
    }
    const leading = blockText.length - blockText.trimStart().length;
    let markStart = blockStart + leading;
    const markEnd = blockStart + blockText.trimEnd().length;
    const trimmed = source.slice(markStart, markEnd);
    const prefix = HEADING_PREFIX.exec(trimmed)?.[0] ?? LIST_PREFIX.exec(trimmed)?.[0] ?? "";
    if (prefix) markStart += prefix.length;
    if (markStart >= markEnd) {
      output += source.slice(cursor, blockEnd);
      cursor = blockEnd;
      continue;
    }
    output += source.slice(cursor, markStart);
    output += `<mark>${source.slice(markStart, markEnd)}</mark>`;
    cursor = markEnd;
    marked = true;
  }
  output += source.slice(cursor);
  return marked ? output : null;
}

/** docx 块级匹配：段落在命中片段里，或命中片段落在段落里。 */
export function blockMatchesChunk(blockText: string, chunkText: string): boolean {
  const block = normalizeForMatch(blockText);
  const chunk = normalizeForMatch(chunkText);
  if (block.length < 8 || !chunk) return false;
  return chunk.includes(block) || block.includes(chunk.slice(0, Math.min(chunk.length, 120)));
}
