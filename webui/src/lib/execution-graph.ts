import type {
  ExecutionEdge,
  ExecutionNode,
  ExecutionPage,
  ToolCallStatus,
} from "@/lib/evaluation-api";

export type ToolGroupNode = Omit<ExecutionNode, "kind" | "status"> & {
  kind: "tool_group";
  status: ToolCallStatus;
  tool_name: string;
  count: number;
  succeeded: number;
  failed: number;
  running: number;
  cancelled: number;
  unknown: number;
  call_ids: string[];
};

export type ProjectedExecutionNode = ExecutionNode | ToolGroupNode;

export type ExecutionProjection = {
  nodes: ProjectedExecutionNode[];
  edges: ExecutionEdge[];
  grouped: boolean;
  omittedNodeCount: number;
};

function mergeById<T extends { id: string }>(current: T[], incoming: T[]): T[] {
  const merged = new Map(current.map((item) => [item.id, item]));
  for (const item of incoming) {
    merged.set(item.id, { ...merged.get(item.id), ...item });
  }
  return [...merged.values()];
}

export function mergeExecutionPage(
  current: ExecutionPage | null,
  incoming: ExecutionPage,
): ExecutionPage {
  if (!current) return incoming;
  return {
    ...incoming,
    data: {
      nodes: mergeById(current.data.nodes, incoming.data.nodes),
      edges: mergeById(current.data.edges, incoming.data.edges),
    },
    coverage: {
      ...incoming.coverage,
      reasons: [...new Set([...current.coverage.reasons, ...incoming.coverage.reasons])],
    },
  };
}

function groupStatus(counts: Record<ToolCallStatus, number>): ToolCallStatus {
  if (counts.failed > 0) return "failed";
  if (counts.running > 0) return "running";
  if (counts.cancelled > 0) return "cancelled";
  if (counts.unknown > 0) return "unknown";
  return "succeeded";
}

function encodePart(value: string | null | undefined): string {
  return encodeURIComponent(value ?? "unassigned");
}

function groupId(node: ExecutionNode, consecutiveBlock: number): string {
  const phasePart = node.phase_id
    ? `phase:${encodePart(node.phase_id)}`
    : `block:${consecutiveBlock}`;
  return `tool-group:${encodePart(node.execution_id)}:${phasePart}:${encodePart(node.tool_name)}`;
}

function makeToolGroup(
  id: string,
  tools: ExecutionNode[],
): ToolGroupNode {
  const counts: Record<ToolCallStatus, number> = {
    succeeded: 0,
    failed: 0,
    running: 0,
    cancelled: 0,
    unknown: 0,
  };
  for (const tool of tools) counts[tool.status] += 1;
  const first = tools[0];
  const tokenTotal = tools.some((tool) => tool.total_tokens != null)
    ? tools.reduce((sum, tool) => sum + (tool.total_tokens ?? 0), 0)
    : null;
  return {
    ...first,
    id,
    kind: "tool_group",
    call_id: null,
    status: groupStatus(counts),
    duration_ms: tools.some((tool) => tool.duration_ms != null)
      ? tools.reduce((sum, tool) => sum + (tool.duration_ms ?? 0), 0)
      : null,
    input_tokens: null,
    output_tokens: null,
    total_tokens: tokenTotal,
    evidence_refs: [...new Set(tools.flatMap((tool) => tool.evidence_refs))],
    tool_name: first.tool_name ?? "unknown_tool",
    count: tools.length,
    ...counts,
    call_ids: tools.flatMap((tool) => tool.call_id ? [tool.call_id] : []),
  };
}

export function projectExecutionGraph(
  nodes: ExecutionNode[],
  edges: ExecutionEdge[],
  options: { groupThreshold?: number; maxVisibleNodes?: number } = {},
): ExecutionProjection {
  const groupThreshold = options.groupThreshold ?? 40;
  const maxVisibleNodes = options.maxVisibleNodes ?? 80;
  const toolCount = nodes.filter((node) => node.kind === "tool_call").length;
  if (toolCount <= groupThreshold) {
    return {
      nodes: nodes.slice(0, maxVisibleNodes),
      edges,
      grouped: false,
      omittedNodeCount: Math.max(0, nodes.length - maxVisibleNodes),
    };
  }

  const groups = new Map<string, ExecutionNode[]>();
  const nodeToGroup = new Map<string, string>();
  const projected: ProjectedExecutionNode[] = [];
  let consecutiveBlock = 0;
  let previousPhaseLessKey = "";

  for (const node of nodes) {
    if (node.kind !== "tool_call" || !node.tool_name) {
      projected.push(node);
      previousPhaseLessKey = "";
      continue;
    }
    const phaseLessKey = `${node.execution_id ?? ""}\u0000${node.tool_name}`;
    if (!node.phase_id && phaseLessKey !== previousPhaseLessKey) consecutiveBlock += 1;
    previousPhaseLessKey = node.phase_id ? "" : phaseLessKey;
    const id = groupId(node, consecutiveBlock);
    const bucket = groups.get(id) ?? [];
    bucket.push(node);
    groups.set(id, bucket);
    nodeToGroup.set(node.id, id);
  }

  for (const [id, tools] of groups) projected.push(makeToolGroup(id, tools));

  const dedupedEdges = new Map<string, ExecutionEdge>();
  for (const edge of edges) {
    const from = nodeToGroup.get(edge.from) ?? edge.from;
    const to = nodeToGroup.get(edge.to) ?? edge.to;
    if (from === to) continue;
    const key = `${from}\u0000${to}\u0000${edge.kind}`;
    if (!dedupedEdges.has(key)) {
      dedupedEdges.set(key, { ...edge, id: `projected:${edge.kind}:${from}:${to}`, from, to });
    }
  }

  return {
    nodes: projected.slice(0, maxVisibleNodes),
    edges: [...dedupedEdges.values()],
    grouped: true,
    omittedNodeCount: Math.max(0, projected.length - maxVisibleNodes),
  };
}
