import { describe, expect, it } from "vitest";

import {
  mergeExecutionPage,
  projectExecutionGraph,
} from "@/lib/execution-graph";
import type { ExecutionNode, ExecutionPage } from "@/lib/evaluation-api";

function toolNode(index: number, overrides: Partial<ExecutionNode> = {}): ExecutionNode {
  return {
    id: `tool-${index}`,
    kind: "tool_call",
    agent_id: "agent-research",
    agent_name: "Researcher",
    execution_id: "exec-research",
    parent_execution_id: "exec-root",
    call_id: `call-${index}`,
    model: null,
    status: "succeeded",
    started_at: `2026-10-25T00:00:${String(index).padStart(2, "0")}Z`,
    duration_ms: index,
    input_tokens: null,
    output_tokens: null,
    total_tokens: null,
    usage_source: "unavailable",
    evidence_refs: [`event-${index}`],
    tool_name: "web_search",
    phase_id: "research",
    ...overrides,
  };
}

function page(nodes: ExecutionNode[], seq: number): ExecutionPage {
  return {
    data: { nodes, edges: [] },
    next_after_seq: seq,
    has_more: false,
    sealed: true,
    coverage: {
      trace: "complete",
      identity: "partial",
      usage_attribution: "unavailable",
      reasons: [],
    },
  };
}

describe("execution graph projection", () => {
  it("merges incremental pages by stable node id and keeps null distinct from zero", () => {
    const first = mergeExecutionPage(null, page([toolNode(1)], 1));
    const merged = mergeExecutionPage(
      first,
      page([toolNode(1, { status: "failed", duration_ms: 0, total_tokens: null })], 2),
    );

    expect(merged.data.nodes).toHaveLength(1);
    expect(merged.data.nodes[0]).toMatchObject({
      id: "tool-1",
      status: "failed",
      duration_ms: 0,
      total_tokens: null,
    });
    expect(merged.next_after_seq).toBe(2);
  });

  it("shows 40 tools directly and groups 41 tools with full failure counts", () => {
    const forty = Array.from({ length: 40 }, (_, index) => toolNode(index));
    const fortyOne = [
      ...forty,
      toolNode(40, { status: "failed" }),
    ];

    expect(projectExecutionGraph(forty, []).grouped).toBe(false);
    const projected = projectExecutionGraph(fortyOne, []);
    expect(projected.grouped).toBe(true);
    expect(projected.nodes).toHaveLength(1);
    expect(projected.nodes[0]).toMatchObject({
      kind: "tool_group",
      count: 41,
      succeeded: 40,
      failed: 1,
    });
  });

  it("does not merge phase-less calls across an intervening activity", () => {
    const nodes = [
      toolNode(1, { phase_id: null }),
      {
        ...toolNode(2),
        id: "agent-summary",
        kind: "agent_activity" as const,
        call_id: null,
        tool_name: null,
        phase_id: null,
      },
      toolNode(3, { phase_id: null }),
      ...Array.from({ length: 39 }, (_, index) => toolNode(index + 4, {
        tool_name: "read_file",
        phase_id: "read",
      })),
    ];

    const projected = projectExecutionGraph(nodes, []);
    const webGroups = projected.nodes.filter(
      (node) => node.kind === "tool_group" && node.tool_name === "web_search",
    );
    expect(webGroups).toHaveLength(2);
  });

  it("never exceeds the visible node budget and reports omitted nodes", () => {
    const nodes = Array.from({ length: 120 }, (_, index) => toolNode(index, {
      tool_name: `tool_${index}`,
      phase_id: `phase_${index}`,
    }));
    const projected = projectExecutionGraph(nodes, [], { maxVisibleNodes: 80 });
    expect(projected.nodes.length).toBeLessThanOrEqual(80);
    expect(projected.omittedNodeCount).toBe(40);
  });
});
