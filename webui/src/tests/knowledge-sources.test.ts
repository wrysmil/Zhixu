import { describe, expect, it } from "vitest";

import {
  collectKnowledgeSourcesByTurn,
  parseKnowledgeSearchSources,
  stripKnowledgeHandle,
} from "@/lib/knowledge-sources";
import type { UIMessage } from "@/lib/types";

const RESULT = `<knowledge_results mode="hybrid" mode_used="hybrid" routes="vector,keyword">
<notice>未精排</notice>
<result rank="1" score="1.000" chunk="chunk://chk_a" doc="doc://doc_1" kb="规范库" title="门诊高血压 &amp; 随访" heading="第三节 &quot;随访频次&quot;" page="2" chars="12"><content>每 2 个月随访一次。</content></result>
<result rank="2" score="0.800" chunk="chunk://chk_b" doc="doc://doc_2" kb="另一库" title="分级说明" heading="" page="" chars="8"><content>1 级每 3 个月。</content></result>
</knowledge_results>`;

function traceMessage(
  id: string,
  turnId: string,
  toolEvents: NonNullable<UIMessage["toolEvents"]>,
): UIMessage {
  return {
    id,
    role: "tool",
    kind: "trace",
    content: "knowledge_search()",
    turnId,
    toolEvents,
    createdAt: 1,
  };
}

describe("knowledge sources", () => {
  it("strips the doc:// and chunk:// handle prefixes", () => {
    expect(stripKnowledgeHandle("chunk://chk_a", "chunk")).toBe("chk_a");
    expect(stripKnowledgeHandle("doc_1", "doc")).toBe("doc_1");
  });

  it("parses result attributes, unescapes them and keeps the excerpt", () => {
    const hits = parseKnowledgeSearchSources(RESULT);
    expect(hits).toHaveLength(2);
    expect(hits[0]).toEqual({
      chunkId: "chk_a",
      docId: "doc_1",
      title: "门诊高血压 & 随访",
      kb: "规范库",
      heading: '第三节 "随访频次"',
      page: "2",
      score: "1.000",
      content: "每 2 个月随访一次。",
    });
    expect(hits[1].page).toBeNull();
    expect(hits[1].content).toBe("1 级每 3 个月。");
  });

  it("ignores tool results that are not knowledge search output", () => {
    expect(parseKnowledgeSearchSources("web_search ok")).toEqual([]);
    expect(parseKnowledgeSearchSources({ results: [] })).toEqual([]);
  });

  it("groups sources by turn, ignoring start events and duplicate chunks", () => {
    const messages: UIMessage[] = [
      traceMessage("t1", "turn-1", [
        { version: 1, phase: "start", call_id: "c1", name: "knowledge_search", result: null },
        { version: 1, phase: "end", call_id: "c1", name: "knowledge_search", result: RESULT },
      ]),
      traceMessage("t2", "turn-1", [
        {
          version: 1,
          phase: "end",
          call_id: "c2",
          name: "knowledge_search",
          result: RESULT.replace("chk_b", "chk_c"),
        },
      ]),
      traceMessage("t3", "turn-2", [
        {
          version: 1,
          phase: "end",
          call_id: "c3",
          name: "knowledge_search",
          result: RESULT.replace(/chk_a|chk_b/g, "chk_d"),
        },
      ]),
      {
        id: "a1",
        role: "assistant",
        content: "每 2 个月一次。",
        turnId: "turn-1",
        createdAt: 2,
      },
    ];

    const byTurn = collectKnowledgeSourcesByTurn(messages);
    expect(byTurn.get("turn-1")?.map((hit) => hit.chunkId)).toEqual(["chk_a", "chk_b", "chk_c"]);
    expect(byTurn.get("turn-2")?.map((hit) => hit.chunkId)).toEqual(["chk_d"]);
  });
});
