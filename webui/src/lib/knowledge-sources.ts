import type { UIMessage } from "@/lib/types";

/** 一次知识检索命中的来源信息，供回复底部来源条与原文面板共用。 */
export interface KnowledgeSourceHit {
  chunkId: string;
  docId: string;
  title: string;
  kb: string;
  heading: string;
  page: string | null;
  score: string;
  /** 检索结果里自带的片段正文（可能被服务端截断）。 */
  content: string;
}

const RESULT_RE = /<result\b([^>]*)>([\s\S]*?)<\/result>/g;
const ATTR_RE = /([a-zA-Z_][\w-]*)="([^"]*)"/g;
const CONTENT_RE = /<content>([\s\S]*?)<\/content>/;

/** 与 `nanobot/agent/tools/knowledge_common.py::escape_xml` 对称的反转义。 */
export function unescapeXml(value: string): string {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

/** 工具结果里的句柄带 `doc://` / `chunk://` 前缀，知识库 API 只认裸 id。 */
export function stripKnowledgeHandle(value: string, scheme: "doc" | "chunk"): string {
  const prefix = `${scheme}://`;
  return value.startsWith(prefix) ? value.slice(prefix.length) : value;
}

/** 从 `knowledge_search` 的工具结果里提取引用来源；非检索结果返回空数组。 */
export function parseKnowledgeSearchSources(result: unknown): KnowledgeSourceHit[] {
  if (typeof result !== "string" || !result.includes("<knowledge_results")) return [];
  const hits: KnowledgeSourceHit[] = [];
  RESULT_RE.lastIndex = 0;
  for (const match of result.matchAll(RESULT_RE)) {
    const attrs: Record<string, string> = {};
    for (const [, key, raw] of match[1].matchAll(ATTR_RE)) {
      attrs[key] = unescapeXml(raw);
    }
    const chunkId = stripKnowledgeHandle(attrs.chunk ?? "", "chunk");
    if (!chunkId) continue;
    const content = CONTENT_RE.exec(match[2])?.[1];
    hits.push({
      chunkId,
      docId: stripKnowledgeHandle(attrs.doc ?? "", "doc"),
      title: attrs.title?.trim() || attrs.doc || chunkId,
      kb: attrs.kb ?? "",
      heading: attrs.heading ?? "",
      page: attrs.page ? attrs.page : null,
      score: attrs.score ?? "",
      content: content ? unescapeXml(content) : "",
    });
  }
  return hits;
}

/**
 * 按回合汇总来源：检索发生在 trace 行上，最终答案在另一条消息里，
 * 用 `turnId` 把两者接起来。同一回合内按 chunk 去重并保持首次出现顺序。
 */
export function collectKnowledgeSourcesByTurn(
  messages: UIMessage[],
): Map<string, KnowledgeSourceHit[]> {
  const byTurn = new Map<string, KnowledgeSourceHit[]>();
  const seen = new Set<string>();
  for (const message of messages) {
    const turnId = message.turnId;
    if (!turnId) continue;
    for (const event of message.toolEvents ?? []) {
      if (event.phase !== "end" || event.name !== "knowledge_search") continue;
      for (const hit of parseKnowledgeSearchSources(event.result)) {
        const key = `${turnId}\u0000${hit.chunkId}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const list = byTurn.get(turnId);
        if (list) list.push(hit);
        else byTurn.set(turnId, [hit]);
      }
    }
  }
  return byTurn;
}
