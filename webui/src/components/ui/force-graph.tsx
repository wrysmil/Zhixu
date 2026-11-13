import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode, WheelEvent } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

export interface ForceGraphNode {
  id: string;
  label: string;
  /** Optional group name, shown as a secondary caption. */
  group?: string;
  /** Drives node radius. */
  weight?: number;
}

export interface ForceGraphEdge {
  source: string;
  target: string;
  weight?: number;
  label?: string;
}

/** Minimal structural view of the d3-force simulation we drive. */
interface ForceSimulation {
  alpha(): number;
  alphaTarget(value: number): ForceSimulation;
  alphaDecay(value: number): ForceSimulation;
  restart(): ForceSimulation;
  stop(): ForceSimulation;
  on(event: "tick", handler: () => void): ForceSimulation;
}

interface PositionedNode extends ForceGraphNode {
  x: number;
  y: number;
  fx?: number | null;
  fy?: number | null;
}

interface PositionedLink {
  source: PositionedNode;
  target: PositionedNode;
  label?: string;
  weight: number;
}

/** Frames between two React renders while the simulation is alive. */
const TICK_INTERVAL_MS = 32;
const MAX_NODES = 60;
const MAX_EDGES = 140;

export interface ForceGraphProps {
  nodes: ForceGraphNode[];
  edges: ForceGraphEdge[];
  activeId?: string;
  onSelect?: (nodeId: string) => void;
  /** Rendered under the canvas; use it for legends and status text. */
  footer?: ReactNode;
  ariaLabel?: string;
  emptyLabel?: string;
  className?: string;
  heightClassName?: string;
  /** Only use for graphs whose edges carry a meaningful source and target. */
  directed?: boolean;
}

/**
 * Shared force-directed graph: continuous simulation, draggable nodes,
 * neighbour highlighting and a selected-node halo.
 *
 * Both the knowledge graph and the memory relation view render through this
 * component so the motion language stays identical across the product.
 */
export function ForceGraph({
  nodes,
  edges,
  activeId,
  onSelect,
  footer,
  ariaLabel,
  emptyLabel,
  className,
  heightClassName = "h-[360px]",
  directed = false,
}: ForceGraphProps) {
  const { t } = useTranslation();
  const markerId = `graph-arrow-${useId().replace(/:/g, "")}`;
  const svgRef = useRef<SVGSVGElement | null>(null);
  const simulationRef = useRef<ForceSimulation | null>(null);
  const nodesRef = useRef<PositionedNode[]>([]);
  const dragRef = useRef<{ id: string; moved: boolean } | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [layout, setLayout] = useState<{ nodes: PositionedNode[]; links: PositionedLink[] } | null>(null);
  const [viewBox, setViewBox] = useState({ x: -420, y: -280, width: 840, height: 560 });

  const capped = useMemo(() => {
    const nextNodes = nodes.slice(0, MAX_NODES);
    const ids = new Set(nextNodes.map((node) => node.id));
    const nextEdges = edges
      .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
      .slice(0, MAX_EDGES);
    return { nodes: nextNodes, edges: nextEdges, truncated: nodes.length > nextNodes.length || edges.length > nextEdges.length };
  }, [nodes, edges]);

  const layoutKey = JSON.stringify(capped.nodes);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } = await import("d3-force");
      if (cancelled) return;
      const simNodes: PositionedNode[] = capped.nodes.map((node, index) => {
        const angle = index * 2.399963;
        const radius = 30 * Math.sqrt(index);
        return { ...node, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
      });
      const simLinks = capped.edges.map((edge) => ({ source: edge.source, target: edge.target, label: edge.label, weight: edge.weight ?? 1 }));
      const simulation = forceSimulation(simNodes as never)
        .force(
          "link",
          forceLink(simLinks as never)
            .id((node) => (node as unknown as PositionedNode).id)
            .distance((link) => 190 - Math.min(35, (link as unknown as PositionedLink).weight * 4)),
        )
        .force("charge", forceManyBody().strength(-850))
        .force("center", forceCenter(0, 0))
        .force("collide", forceCollide(58))
        // 持续仿真并缓慢降温：入场时节点是「活」的，之后仍可被拖拽唤醒。
        .alphaDecay(0.035)
        .alpha(1) as unknown as ForceSimulation;

      nodesRef.current = simNodes;
      simulationRef.current = simulation;

      let lastPublish = 0;
      const publish = (force = false) => {
        if (cancelled) return;
        const now = Date.now();
        if (!force && now - lastPublish < TICK_INTERVAL_MS) return;
        lastPublish = now;
        setLayout({
          nodes: nodesRef.current.map((node) => ({ ...node })),
          links: simLinks as unknown as PositionedLink[],
        });
      };
      simulation.on("tick", () => publish());
      simulation.restart();
      publish(true);
    })();
    return () => {
      cancelled = true;
      simulationRef.current?.stop();
      simulationRef.current = null;
    };
  }, [layoutKey, capped.edges]);

  const neighbourIds = useMemo(() => {
    const map = new Set<string>();
    if (!hoveredId) return map;
    capped.edges.forEach((edge) => {
      if (edge.source === hoveredId) map.add(edge.target);
      if (edge.target === hoveredId) map.add(edge.source);
    });
    return map;
  }, [hoveredId, capped.edges]);

  const focusIds = useMemo(() => {
    const map = new Set<string>();
    if (!hoveredId) return map;
    map.add(hoveredId);
    neighbourIds.forEach((id) => map.add(id));
    return map;
  }, [hoveredId, neighbourIds]);

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

  /** 把指针位置换算成 SVG 用户坐标；无 SVG 几何实现的环境返回 null。 */
  const toSvgPoint = useCallback((event: ReactPointerEvent<SVGGElement>) => {
    const svg = svgRef.current;
    if (!svg || typeof svg.getScreenCTM !== "function") return null;
    const matrix = svg.getScreenCTM();
    if (!matrix) return null;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(matrix.inverse());
  }, []);

  const handlePointerDown = (event: ReactPointerEvent<SVGGElement>, nodeId: string) => {
    if (event.button !== 0) return;
    dragRef.current = { id: nodeId, moved: false };
    (event.currentTarget as SVGGElement).setPointerCapture?.(event.pointerId);
    // 拖拽期间提高热量，让邻域跟着节点走；松手后弹性回落。
    simulationRef.current?.alphaTarget(0.25).restart();
  };

  const handlePointerMove = (event: ReactPointerEvent<SVGGElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const point = toSvgPoint(event);
    if (!point) return;
    drag.moved = true;
    const node = nodesRef.current.find((item) => item.id === drag.id);
    if (!node) return;
    node.fx = point.x;
    node.fy = point.y;
    node.x = point.x;
    node.y = point.y;
    simulationRef.current?.restart();
  };

  const handlePointerUp = (event: ReactPointerEvent<SVGGElement>, nodeId: string) => {
    const drag = dragRef.current;
    (event.currentTarget as SVGGElement).releasePointerCapture?.(event.pointerId);
    dragRef.current = null;
    if (!drag) return;
    const node = nodesRef.current.find((item) => item.id === nodeId);
    if (node) {
      // 松手后不再钉住节点，弹簧把它推回自然位置。
      node.fx = null;
      node.fy = null;
    }
    simulationRef.current?.alphaTarget(0).restart();
    if (!drag.moved) onSelect?.(nodeId);
  };

  if (nodes.length === 0) {
    return (
      <div className={cn("rounded-panel border border-border/60 bg-background px-4 py-10 text-center text-sm text-muted-foreground", className)}>
        {emptyLabel ?? t("knowledge.experience.graphNoRelations", { defaultValue: "暂无关联节点" })}
      </div>
    );
  }

  return (
    <div className={cn("relative overflow-hidden rounded-panel border border-border/60 bg-background", className)}
      style={{ backgroundImage: "radial-gradient(ellipse at 50% 45%, hsl(var(--primary) / 0.07), transparent 60%), radial-gradient(hsl(var(--primary) / 0.10) 0.8px, transparent 0.8px)", backgroundSize: "100% 100%, 22px 22px" }}>
      <svg
        ref={svgRef}
        role="group"
        aria-label={ariaLabel ?? t("knowledge.graph.subgraph", { defaultValue: "关系图谱" })}
        viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`}
        className={cn("w-full touch-none select-none", heightClassName)}
        onWheel={handleWheel}
      >
        <defs>
          <marker id={markerId} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto" markerUnits="userSpaceOnUse">
            <path d="M1 1 L7 4 L1 7" fill="none" stroke="hsl(var(--primary))" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          </marker>
          <filter id="force-graph-node-glow" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        {layout?.links.map((link, index) => {
          const highlighted = link.source.id === activeId || link.target.id === activeId;
          const hovered = hoveredId !== null && (link.source.id === hoveredId || link.target.id === hoveredId);
          const dimmed = hoveredId !== null && !hovered && !highlighted;
          const dx = link.target.x - link.source.x;
          const dy = link.target.y - link.source.y;
          const length = Math.hypot(dx, dy);
          // Self-relations cannot be meaningfully drawn as straight lines.
          if (length < 1) return null;
          const sx = link.source.x + (dx / length) * 18;
          const sy = link.source.y + (dy / length) * 18;
          const tx = link.target.x - (dx / length) * 22;
          const ty = link.target.y - (dy / length) * 22;
          return (
            <g key={`${link.source.id}-${link.target.id}-${link.label ?? ""}-${index}`}>
            <line
              x1={sx}
              y1={sy}
              x2={tx}
              y2={ty}
              className="text-primary transition-[stroke-opacity,stroke-width] duration-[120ms] ease-[cubic-bezier(.2,.8,.2,1)] motion-reduce:transition-none"
              stroke="currentColor"
              strokeWidth={highlighted ? 1.8 : 1.1}
              strokeOpacity={dimmed ? 0.04 : highlighted ? 0.48 : hovered ? 0.5 : 0.18}
              markerEnd={directed ? `url(#${markerId})` : undefined}
            >
              {link.label ? <title>{link.label}</title> : null}
            </line>
            {directed && !dimmed ? (
              <line x1={sx} y1={sy} x2={tx} y2={ty}
                className="graph-flow-line" stroke="hsl(var(--primary))"
                strokeWidth={highlighted || hovered ? 2.5 : 1.7} strokeLinecap="round"
                strokeOpacity={highlighted || hovered ? 0.85 : 0.45}
                strokeDasharray="3 45" pointerEvents="none" aria-hidden="true" />
            ) : null}
            </g>
          );
        })}
        {layout?.nodes.map((node, index) => {
          const isActive = node.id === activeId;
          const isHovered = node.id === hoveredId;
          const dimmed = hoveredId !== null && !focusIds.has(node.id);
          const radius = isActive ? 18 : 12 + Math.min(4, Math.sqrt(node.weight ?? 1));
          return (
            <g
              key={node.id}
              data-force-node
              role="button"
              tabIndex={0}
              aria-label={node.group ? `${node.label} · ${node.group}` : node.label}
              aria-pressed={isActive}
              transform={`translate(${node.x},${node.y})`}
              className="group cursor-grab text-foreground outline-none active:cursor-grabbing"
              style={{ opacity: dimmed ? 0.28 : 1, transition: "opacity 140ms cubic-bezier(.2,.8,.2,1)" }}
              onPointerEnter={() => setHoveredId(node.id)}
              onPointerLeave={() => setHoveredId((current) => (current === node.id ? null : current))}
              onPointerDown={(event) => handlePointerDown(event, node.id)}
              onPointerMove={handlePointerMove}
              onPointerUp={(event) => handlePointerUp(event, node.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  if (!event.repeat) onSelect?.(node.id);
                }
              }}
            >
              {isActive ? (
                <circle r={radius + 9} fill="hsl(var(--primary) / 0.08)" stroke="hsl(var(--primary) / 0.22)" strokeWidth={1} />
              ) : null}
              {isActive ? <circle r={radius + 15} fill="none" stroke="hsl(var(--primary) / 0.18)" strokeWidth="1" strokeDasharray="3 6" /> : null}
              <circle r={radius + 10} fill="none" stroke="currentColor" strokeWidth={2} className="opacity-0 group-focus-visible:opacity-100" />
              <circle
                r={radius}
                fill={isActive ? "hsl(var(--primary))" : "hsl(var(--background))"}
                fillOpacity={1}
                stroke={isActive ? "hsl(var(--primary))" : "hsl(var(--primary) / 0.65)"}
                strokeWidth={isActive ? 2 : 1.5}
                filter={isHovered ? "url(#force-graph-node-glow)" : undefined}
                className="transition-[fill-opacity,stroke-width] duration-[120ms] ease-[cubic-bezier(.2,.8,.2,1)] motion-reduce:transition-none"
                style={{ animation: "motion-scale-in 200ms cubic-bezier(.2,.8,.2,1) both", animationDelay: `${Math.min(index, 14) * 22}ms` }}
              />
              {!isActive ? <circle r="3.5" fill="hsl(var(--primary) / 0.75)" pointerEvents="none" /> : <circle r="4" fill="hsl(var(--primary-foreground))" pointerEvents="none" />}
              <text
                y={radius + 19}
                textAnchor="middle"
                className="text-foreground text-[13px] font-medium"
                style={{ pointerEvents: "none", fill: "currentColor", paintOrder: "stroke", stroke: "hsl(var(--background))", strokeWidth: 4, strokeLinejoin: "round" }}
              >
                {node.label.length > 10 ? `${node.label.slice(0, 10)}…` : node.label}
              </text>
            </g>
          );
        })}
      </svg>
      {footer}
    </div>
  );
}
