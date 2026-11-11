import { useEffect, useMemo, useRef, useState } from "react";
import type { WheelEvent } from "react";
import { useTranslation } from "react-i18next";

import type { KnowledgeGraphEdge, KnowledgeGraphNode } from "@/lib/api";

interface PositionedNode extends KnowledgeGraphNode {
  x: number;
  y: number;
}

interface Link {
  source: PositionedNode;
  target: PositionedNode;
  relation: string;
  weight: number;
}

interface GraphViewProps {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  centerId: string;
  onSelect?: (entityId: string) => void;
  onExpand?: (entityId: string) => void | Promise<void>;
}

const MAX_NODES = 50;
const MAX_EDGES = 100;
const PALETTE = ["#2563eb", "#0d9488", "#d97706", "#7c3aed", "#db2777", "#64748b"];

function nodeColor(type: string): string {
  let hash = 0;
  for (let index = 0; index < type.length; index += 1) {
    hash = (hash * 31 + type.charCodeAt(index)) % 997;
  }
  return PALETTE[hash % PALETTE.length];
}

export function GraphView({
  nodes,
  edges,
  centerId,
  onSelect,
  onExpand,
}: GraphViewProps) {
  const { t } = useTranslation();
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [layout, setLayout] = useState<{ nodes: PositionedNode[]; links: Link[] } | null>(
    null,
  );
  const [viewBox, setViewBox] = useState({
    x: -320,
    y: -240,
    width: 640,
    height: 480,
  });

  const { cappedNodes, cappedEdges, truncated } = useMemo(() => {
    const nextNodes = nodes.slice(0, MAX_NODES);
    const ids = new Set(nextNodes.map((node) => node.id));
    const nextEdges = edges
      .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
      .slice(0, MAX_EDGES);
    return {
      cappedNodes: nextNodes,
      cappedEdges: nextEdges,
      truncated: nodes.length > nextNodes.length || edges.length > nextEdges.length,
    };
  }, [nodes, edges]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } =
        await import("d3-force");
      const simNodes = cappedNodes.map((node) => ({ ...node, x: 0, y: 0 }));
      const simLinks = cappedEdges.map((edge) => ({ ...edge }));
      const simulation = forceSimulation(simNodes as never)
        .force(
          "link",
          forceLink(simLinks as never)
            .id((node) => (node as unknown as PositionedNode).id)
            .distance(90),
        )
        .force("charge", forceManyBody().strength(-220))
        .force("center", forceCenter(0, 0))
        .force("collide", forceCollide(28))
        .stop();
      for (let step = 0; step < 180; step += 1) simulation.tick();
      if (cancelled) return;
      setLayout({
        nodes: simNodes as PositionedNode[],
        links: simLinks as unknown as Link[],
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [cappedNodes, cappedEdges]);

  useEffect(() => {
    if (!layout || layout.nodes.length === 0) return;
    const xs = layout.nodes.map((node) => node.x);
    const ys = layout.nodes.map((node) => node.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    setViewBox({
      x: minX - 60,
      y: minY - 60,
      width: Math.max(maxX - minX + 120, 260),
      height: Math.max(maxY - minY + 120, 200),
    });
  }, [layout]);

  const handleWheel = (event: WheelEvent<SVGSVGElement>) => {
    event.preventDefault();
    const factor = event.deltaY > 0 ? 1.12 : 0.89;
    setViewBox((current) => ({
      x: current.x,
      y: current.y,
      width: current.width * factor,
      height: current.height * factor,
    }));
  };

  return (
    <div className="relative overflow-hidden rounded-panel border border-border/60 bg-background">
      {truncated ? (
        <p className="border-b border-border/60 px-3 py-2 text-[11.5px] text-muted-foreground">
          {t("knowledge.graph.truncated", { count: cappedNodes.length })}
        </p>
      ) : null}
      <svg
        ref={svgRef}
        role="img"
        aria-label={t("knowledge.graph.subgraph")}
        viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`}
        className="h-[360px] w-full touch-none"
        onWheel={handleWheel}
      >
        {layout?.links.map((link) => (
          <line
            key={`${link.source.id}-${link.target.id}-${link.relation}`}
            x1={link.source.x}
            y1={link.source.y}
            x2={link.target.x}
            y2={link.target.y}
            className="text-muted-foreground"
            stroke="currentColor"
            strokeWidth={1.2}
            strokeOpacity={0.55}
          >
            <title>{link.relation}</title>
          </line>
        ))}
        {layout?.nodes.map((node) => (
          <g
            key={node.id}
            data-graph-node
            transform={`translate(${node.x},${node.y})`}
            className="cursor-pointer"
            onClick={() => onSelect?.(node.id)}
            onDoubleClick={() => void onExpand?.(node.id)}
          >
            <circle
              r={node.id === centerId ? 12 : 8}
              fill={nodeColor(node.type)}
              fillOpacity={node.id === centerId ? 1 : 0.82}
              stroke="var(--background, #fff)"
              strokeWidth={2}
            >
              <title>{`${node.name} · ${node.type}`}</title>
            </circle>
            <text
              y={22}
              textAnchor="middle"
              className="text-foreground text-[9px]"
              style={{ pointerEvents: "none", fill: "currentColor" }}
            >
              {node.name.length > 12 ? `${node.name.slice(0, 12)}…` : node.name}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
