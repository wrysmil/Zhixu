import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { ForceGraph, type ForceGraphEdge, type ForceGraphNode } from "@/components/ui/force-graph";
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from "@/lib/api";

interface GraphViewProps {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  centerId: string;
  selectedId?: string;
  onSelect?: (entityId: string) => void;
  onExpand?: (entityId: string) => void | Promise<void>;
}

/**
 * Knowledge-graph view. It renders the shared `ForceGraph` (continuous
 * simulation, draggable nodes, neighbour highlighting) and only keeps the
 * domain parts here: the entity footer and the "expand relations" action.
 */
export function GraphView({
  nodes,
  edges,
  centerId,
  selectedId,
  onSelect,
  onExpand,
}: GraphViewProps) {
  const { t } = useTranslation();
  const [localSelectedId, setLocalSelectedId] = useState(centerId);
  const activeId = selectedId ?? localSelectedId;
  const selectNode = (id: string) => {
    setLocalSelectedId(id);
    onSelect?.(id);
  };

  const { graphNodes, graphEdges } = useMemo(() => {
    const graphNodes: ForceGraphNode[] = nodes.map((node) => ({
      id: node.id,
      label: node.name,
      group: node.type,
    }));
    const graphEdges: ForceGraphEdge[] = edges.map((edge) => ({
      source: edge.source,
      target: edge.target,
      label: edge.relation,
    }));
    return { graphNodes, graphEdges };
  }, [nodes, edges]);

  const activeNode = nodes.find((node) => node.id === activeId);

  return (
    <ForceGraph
      nodes={graphNodes}
      edges={graphEdges}
      activeId={activeId}
      directed
      onSelect={selectNode}
      ariaLabel={t("knowledge.graph.subgraph")}
      emptyLabel={t("knowledge.experience.graphNoRelations", { defaultValue: "暂无关联节点，仍可查看下方文档证据" })}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 px-3 py-2 text-[12px]">
          <p role="status" className="min-w-0 break-words text-muted-foreground">
            {activeNode
              ? `${t("knowledge.experience.graphSelected", { defaultValue: "已选择" })}：${activeNode.name} · ${activeNode.type}`
              : t("knowledge.experience.graphSelectHint", { defaultValue: "选择节点查看详情，展开按钮加载关联" })}
          </p>
          {onExpand && activeNode ? (
            <button
              type="button"
              className="shrink-0 rounded-control border border-border px-3 py-1.5 text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => void onExpand(activeNode.id)}
            >
              {t("knowledge.experience.graphExpand", { defaultValue: "展开关联" })}
            </button>
          ) : null}
        </div>
      }
    />
  );
}
