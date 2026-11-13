import { useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCcw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { ForceGraph, type ForceGraphEdge, type ForceGraphNode } from "@/components/ui/force-graph";
import { listMemories } from "@/lib/api";
import type { MemoryPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

const FETCH_LIMIT = 400;
/** A tag needs at least this many memories before it becomes a node. */
const MIN_TAG_COUNT = 2;
/** Co-occurrence needs this many shared memories before it becomes an edge. */
const MIN_EDGE_COUNT = 2;
const MAX_TAG_NODES = 18;
const HUB_ID = "hub:self";

interface RelationGraph {
  nodes: ForceGraphNode[];
  edges: ForceGraphEdge[];
  memoryCount: number;
}

/**
 * Build the relation graph from real memory records.
 *
 * Nodes are the tags that show up across memories (plus the "我" hub and the
 * memory types); edges are tag pairs that keep showing up together, which is
 * the closest honest approximation of "what is related" without a dedicated
 * relation service.
 */
export function buildRelationGraph(memories: MemoryPayload[]): RelationGraph {
  const tagCounts = new Map<string, number>();
  const tagPairs = new Map<string, number>();

  for (const memory of memories) {
    const tags = [...new Set((memory.tags ?? []).map((tag) => tag.trim()).filter(Boolean))];
    for (const tag of tags) tagCounts.set(tag, (tagCounts.get(tag) ?? 0) + 1);
    for (let i = 0; i < tags.length; i += 1) {
      for (let j = i + 1; j < tags.length; j += 1) {
        const key = tags[i] < tags[j] ? `${tags[i]}::${tags[j]}` : `${tags[j]}::${tags[i]}`;
        tagPairs.set(key, (tagPairs.get(key) ?? 0) + 1);
      }
    }
  }

  const keptTags = [...tagCounts.entries()]
    .filter(([, count]) => count >= MIN_TAG_COUNT)
    .sort((a, b) => b[1] - a[1])
    .slice(0, MAX_TAG_NODES)
    .map(([tag]) => tag);
  const kept = new Set(keptTags);

  const nodes: ForceGraphNode[] = memories.length ? [
    { id: HUB_ID, label: "我", group: "主体", weight: memories.length },
    ...keptTags.map((tag) => ({ id: `tag:${tag}`, label: tag, group: `${tagCounts.get(tag) ?? 0} 条`, weight: tagCounts.get(tag) ?? 1 })),
  ] : [];

  const edges: ForceGraphEdge[] = [];
  for (const tag of keptTags) {
    edges.push({ source: HUB_ID, target: `tag:${tag}`, weight: tagCounts.get(tag) ?? 1, label: `${tagCounts.get(tag) ?? 0} 条记忆` });
  }
  for (const [key, count] of tagPairs) {
    if (count < MIN_EDGE_COUNT) continue;
    const [left, right] = key.split("::");
    if (!kept.has(left) || !kept.has(right)) continue;
    edges.push({ source: `tag:${left}`, target: `tag:${right}`, weight: count, label: `共同出现 ${count} 次` });
  }

  return { nodes, edges, memoryCount: memories.length };
}

export function MemoryRelationView() {
  const { t } = useTranslation();
  const { token } = useClient();
  const [memories, setMemories] = useState<MemoryPayload[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listMemories(token, { limit: FETCH_LIMIT, order: "updated" });
      setMemories(result.items ?? []);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const graph = useMemo(() => buildRelationGraph(memories), [memories]);

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        {t("settings.memory.relationLoading", { defaultValue: "正在整理记忆关系…" })}
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3 rounded-panel border border-destructive/30 bg-destructive/[0.05] p-4 text-sm text-destructive">
        <span className="min-w-0 flex-1 break-words">{error}</span>
        <Button size="sm" variant="outline" onClick={() => void load()}>
          <RefreshCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          {t("settings.memory.retry", { defaultValue: "重试" })}
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-muted-foreground">
        <span>
          {t("settings.memory.relationSummary", {
            count: graph.memoryCount,
            nodes: graph.nodes.length,
            defaultValue: `基于 ${graph.memoryCount} 条记忆，连接 ${graph.nodes.length} 个主题`,
          })}
        </span>
        <Button size="sm" variant="ghost" onClick={() => void load()}>
          <RefreshCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          {t("settings.memory.relationRefresh", { defaultValue: "重新计算" })}
        </Button>
      </div>
      <ForceGraph
        nodes={graph.nodes}
        edges={graph.edges}
        ariaLabel={t("settings.memory.relationTitle", { defaultValue: "记忆关系视图" })}
        emptyLabel={t("settings.memory.relationEmpty", { defaultValue: "记忆还不够多，多聊几句就会出现关系" })}
        heightClassName="h-[420px]"
        footer={
          <div className="flex flex-wrap items-center gap-3 border-t border-border/60 px-3 py-2 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-primary" aria-hidden />
              {t("settings.memory.relationLegendHub", { defaultValue: "主体" })}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-foreground/50" aria-hidden />
              {t("settings.memory.relationLegendTag", { defaultValue: "高频标签" })}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-px w-4 bg-foreground/40" aria-hidden />
              {t("settings.memory.relationLegendEdge", { defaultValue: "共同出现" })}
            </span>
            <span className="ml-auto">
              {t("settings.memory.relationHint", { defaultValue: "拖拽节点可重新排布，悬停高亮邻居" })}
            </span>
          </div>
        }
      />
    </div>
  );
}
