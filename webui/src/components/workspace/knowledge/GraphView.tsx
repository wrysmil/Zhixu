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
  selectedId?: string;
  onSelect?: (entityId: string) => void;
  onExpand?: (entityId: string) => void | Promise<void>;
}

const MAX_NODES = 50;
const MAX_EDGES = 100;

export function GraphView({
  nodes,
  edges,
  centerId,
  selectedId,
  onSelect,
  onExpand,
}: GraphViewProps) {
  const { t } = useTranslation();
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [localSelectedId, setLocalSelectedId] = useState(centerId);
  const activeId = selectedId ?? localSelectedId;
  const selectNode = (id: string) => {
    setLocalSelectedId(id);
    onSelect?.(id);
  };
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

  // Identical refresh payloads must not rerun the static layout or reset zoom.
  const layoutKey = JSON.stringify({ nodes: cappedNodes, edges: cappedEdges });
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const graph = JSON.parse(layoutKey) as { nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[] };
      const { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } =
        await import("d3-force");
      const simNodes = graph.nodes.map((node) => ({ ...node, x: 0, y: 0 }));
      const simLinks = graph.edges.map((edge) => ({ ...edge }));
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
  }, [layoutKey]);

  const relatedIds = new Set([activeId]);
  cappedEdges.forEach((edge) => {
    if (edge.source === activeId) relatedIds.add(edge.target);
    if (edge.target === activeId) relatedIds.add(edge.source);
  });
  const activeNode = cappedNodes.find((node) => node.id === activeId);

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
        <p className="border-b border-border/60 px-3 py-2 text-[12px] text-muted-foreground">
          {t("knowledge.graph.truncated", { count: cappedNodes.length })}
        </p>
      ) : null}
      <svg
        ref={svgRef}
        role="group"
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
            data-related={link.source.id === activeId || link.target.id === activeId}
            className="text-foreground transition-[stroke-opacity,stroke-width] duration-[120ms] ease-[cubic-bezier(.2,.8,.2,1)] motion-reduce:transition-none"
            stroke="currentColor"
            strokeWidth={link.source.id === activeId || link.target.id === activeId ? 2.4 : 1.2}
            strokeOpacity={link.source.id === activeId || link.target.id === activeId ? 0.85 : 0.2}
          >
            <title>{link.relation}</title>
          </line>
        ))}
        {layout?.nodes.map((node) => (
          <g
            key={node.id}
            data-graph-node
            role="button"
            tabIndex={0}
            aria-label={`${node.name} · ${node.type}`}
            aria-pressed={node.id === activeId}
            data-related={relatedIds.has(node.id)}
            transform={`translate(${node.x},${node.y})`}
            className="group cursor-pointer text-foreground outline-none"
            onClick={() => selectNode(node.id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                if (!event.repeat) selectNode(node.id);
              }
            }}
          >
            <circle r={18} fill="none" stroke="currentColor" strokeWidth={2}
              className="opacity-0 group-focus-visible:opacity-100" />
            <circle
              r={node.id === activeId ? 12 : 8}
              fill="currentColor"
              fillOpacity={relatedIds.has(node.id) ? 0.9 : 0.35}
              stroke="currentColor"
              strokeWidth={node.id === activeId ? 3 : 1}
              className="transition-[fill-opacity,stroke-width] duration-[120ms] ease-[cubic-bezier(.2,.8,.2,1)] motion-reduce:transition-none"
            >
              <title>{`${node.name} · ${node.type}`}</title>
            </circle>
            <text
              y={22}
              textAnchor="middle"
              className="text-foreground text-[12px]"
              style={{ pointerEvents: "none", fill: "currentColor" }}
            >
              {node.name.length > 12 ? `${node.name.slice(0, 12)}…` : node.name}
            </text>
          </g>
        ))}
      </svg>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 px-3 py-2 text-[12px]">
        <p role="status" className="min-w-0 break-words text-muted-foreground">
          {activeNode ? `${t('knowledge.experience.graphSelected', { defaultValue: '已选择' })}：${activeNode.name} · ${activeNode.type}` :
            t('knowledge.experience.graphSelectHint', { defaultValue: '选择节点查看详情，展开按钮加载关联' })}
        </p>
        {onExpand && activeNode ? <button type="button"
          className="shrink-0 rounded-control border border-border px-3 py-1.5 text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={() => void onExpand(activeNode.id)}>
          {t('knowledge.experience.graphExpand', { defaultValue: '展开关联' })}
        </button> : null}
      </div>
    </div>
  );
}
