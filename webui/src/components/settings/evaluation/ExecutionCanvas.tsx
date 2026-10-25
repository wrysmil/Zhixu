import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { ExecutionEdge } from "@/lib/evaluation-api";
import type { ProjectedExecutionNode } from "@/lib/execution-graph";
import { cn } from "@/lib/utils";

type NodePosition = { x: number; y: number };

function nodeLabel(node: ProjectedExecutionNode): string {
  if (node.kind === "tool_group") return `${node.tool_name} × ${node.count}`;
  return node.label || node.tool_name || node.agent_name || node.model || node.kind;
}

function nodeMeta(node: ProjectedExecutionNode): string {
  if (node.kind === "tool_group") {
    return `${node.succeeded} ok · ${node.failed} failed`;
  }
  return node.agent_name || node.call_id || node.execution_id || "—";
}

function positions(nodes: ProjectedExecutionNode[]): Map<string, NodePosition> {
  const map = new Map<string, NodePosition>();
  const columns = Math.min(4, Math.max(1, Math.ceil(Math.sqrt(nodes.length))));
  nodes.forEach((node, index) => {
    map.set(node.id, {
      x: 22 + (index % columns) * 222,
      y: 22 + Math.floor(index / columns) * 112,
    });
  });
  return map;
}

/** 状态不能只靠颜色表达：节点内额外给一行文字状态。 */
function statusTextKey(status: string): string {
  const suffix = status.charAt(0).toUpperCase() + status.slice(1);
  return `settings.evaluation.observability.status${suffix}`;
}

export function ExecutionCanvas({
  nodes,
  edges,
  selectedId,
  onSelect,
  omittedNodeCount = 0,
}: {
  nodes: ProjectedExecutionNode[];
  edges: ExecutionEdge[];
  selectedId: string | null;
  onSelect: (node: ProjectedExecutionNode) => void;
  omittedNodeCount?: number;
}) {
  const { t } = useTranslation();
  const layout = useMemo(() => positions(nodes), [nodes]);
  const statusLabel = (node: ProjectedExecutionNode): string =>
    t(statusTextKey(node.status), { defaultValue: node.status });
  const columns = Math.min(4, Math.max(1, Math.ceil(Math.sqrt(nodes.length))));
  const rows = Math.max(1, Math.ceil(nodes.length / columns));
  const width = Math.max(640, columns * 222 + 22);
  const height = Math.max(250, rows * 112 + 22);

  if (nodes.length === 0) {
    return (
      <div className="flex min-h-64 items-center justify-center rounded-floating border border-dashed border-border/70 bg-muted/20 px-6 text-center text-[13px] text-muted-foreground">
        {t("settings.evaluation.observability.noGraph", {
          defaultValue: "Execution relationships were not collected for this case.",
        })}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div
        className="max-w-full overflow-auto rounded-floating border border-border/70 bg-muted/20"
        aria-label={t("settings.evaluation.observability.canvas", {
          defaultValue: "Execution canvas",
        })}
      >
        <svg
          role="img"
          aria-label={t("settings.evaluation.observability.canvasDiagram", {
            defaultValue: "Agent, model and tool execution flow",
          })}
          viewBox={`0 0 ${width} ${height}`}
          className="min-h-64 min-w-[640px]"
        >
          <defs>
            <marker id="evaluation-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
              <path d="M0,0 L7,3.5 L0,7 Z" className="fill-muted-foreground/70" />
            </marker>
          </defs>
          {edges.map((edge) => {
            const from = layout.get(edge.from);
            const to = layout.get(edge.to);
            if (!from || !to) return null;
            return (
              <line
                key={edge.id}
                x1={from.x + 174}
                y1={from.y + 42}
                x2={to.x}
                y2={to.y + 42}
                className="stroke-muted-foreground/50"
                strokeWidth="1.5"
                markerEnd="url(#evaluation-arrow)"
              />
            );
          })}
          {nodes.map((node) => {
            const point = layout.get(node.id) ?? { x: 0, y: 0 };
            const selected = node.id === selectedId;
            return (
              <g
                key={node.id}
                role="button"
                tabIndex={0}
                aria-label={`${nodeLabel(node)} · ${node.status}`}
                aria-pressed={selected}
                onClick={() => onSelect(node)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(node);
                  }
                }}
                className="cursor-pointer outline-none"
                transform={`translate(${point.x} ${point.y})`}
              >
                <rect
                  width="174"
                  height="84"
                  rx="8"
                  className={cn(
                    "fill-background stroke-border transition-colors",
                    node.status === "failed" && "fill-destructive/10 stroke-destructive/70",
                    node.status === "running" && "fill-sky-500/10 stroke-sky-500/70",
                    selected && "stroke-primary",
                  )}
                  strokeWidth={selected ? 2.5 : 1.25}
                />
                <text x="12" y="26" className="fill-foreground text-[12px] font-medium">
                  {nodeLabel(node).slice(0, 23)}
                </text>
                <text x="12" y="48" className="fill-muted-foreground text-[10px]">
                  {nodeMeta(node).slice(0, 28)}
                </text>
                <text x="12" y="66" className="fill-muted-foreground text-[10px]">
                  {statusLabel(node)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      {omittedNodeCount > 0 ? (
        <p role="status" className="text-[11px] text-amber-700 dark:text-amber-300">
          {t("settings.evaluation.observability.nodeBudget", {
            count: omittedNodeCount,
            defaultValue: `${omittedNodeCount} nodes are outside the overview. Use tool filters to inspect them.`,
          })}
        </p>
      ) : null}
    </div>
  );
}
