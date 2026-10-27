import { useCallback, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

import { DeleteConfirm } from "@/components/DeleteConfirm";
import { Button } from "@/components/ui/button";
import { deleteEpisode, fetchEpisode, listEpisodes } from "@/lib/api";
import type { EpisodeOutcome, EpisodePayload, MemoryOrigin } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import { EpisodeDetailPanel } from "./EpisodeDetailPanel";
import { ImportanceBadge, MemoryActions, MemoryFilters, MemoryLoadState, MemoryTime, SourceBadge, WorkspaceLabels, memoryRowClass, memorySelectClass, useMemoryCopy, useMemoryDetails, useMemoryList } from "./MemoryPresentation";

export function EpisodeOutcomeLabel({ value }: { value: EpisodeOutcome }) {
  const { text } = useMemoryCopy();
  const labels = { completed: text("已完成", "Completed"), partial: text("部分完成", "Partially completed"), failed: text("失败", "Failed"), ongoing: text("进行中", "In progress") };
  return <span className={value === "failed" ? "text-destructive" : "text-muted-foreground"}>{labels[value] ?? value}</span>;
}

export function EpisodeListView() {
  const { text } = useMemoryCopy();
  const { client, token } = useClient();
  const [query, setQuery] = useState("");
  const [origin, setOrigin] = useState<MemoryOrigin | "">("");
  const [workspace, setWorkspace] = useState("");
  const [order, setOrder] = useState<"created" | "importance">("created");
  const [deleteCandidate, setDeleteCandidate] = useState<EpisodePayload | null>(null);
  const [deleting, setDeleting] = useState(false);
  const deletingRef = useRef(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const load = useCallback(() => listEpisodes(token, { q: query.trim() || undefined, origin: origin || undefined, workspace: workspace || undefined, order }), [token, query, origin, workspace, order]);
  const { result, loading, error, refresh } = useMemoryList(load, query.trim() ? 250 : 0);
  const details = useMemoryDetails<EpisodePayload>(async (id) => (await fetchEpisode(token, id)).episode);
  const filtered = Boolean(query.trim() || origin || workspace);
  const clear = () => { setQuery(""); setOrigin(""); setWorkspace(""); details.close(); };
  const confirmDelete = async () => {
    if (!deleteCandidate || deletingRef.current) return;
    deletingRef.current = true; setDeleting(true); setMutationError(null);
    try {
      await deleteEpisode(client, deleteCandidate.id);
      if (details.selected?.id === deleteCandidate.id) details.close();
      setDeleteCandidate(null); refresh();
    } catch (reason) { setMutationError(reason instanceof Error ? reason.message : String(reason)); }
    finally { deletingRef.current = false; setDeleting(false); }
  };
  return <div className="flex flex-col gap-2.5">
    <MemoryFilters query={query} onQuery={setQuery} origin={origin} onOrigin={setOrigin} workspace={workspace} onWorkspace={setWorkspace} result={result} onClear={clear} active={filtered} loading={loading}><label className="flex items-center gap-2">{text("排序", "Sort")}<select value={order} onChange={(event) => setOrder(event.target.value as typeof order)} className={memorySelectClass}><option value="created">{text("开始时间 · 最新在前", "Started · newest first")}</option><option value="importance">{text("重要性 · 从高到低", "Importance · highest first")}</option></select></label></MemoryFilters>
    {mutationError && <p role="alert" className="text-[12px] text-destructive">{mutationError}</p>}
    <MemoryLoadState error={error} loading={loading} empty={result.items.length === 0} filtered={filtered} onRetry={refresh} />
    {!error && result.items.length > 0 && <ul className="divide-y divide-border/55" aria-label={text("对话记录列表", "Conversation memories")} aria-busy={loading}>{result.items.map((episode) => <li key={episode.id} className={memoryRowClass}>
      <div className="flex items-start justify-between gap-2"><div className="min-w-0 flex-1">
        <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]"><SourceBadge record={episode} /><WorkspaceLabels workspaces={episode.workspaces} /><EpisodeOutcomeLabel value={episode.outcome} /></div>
        {episode.goal && <p className="mb-1 line-clamp-2 break-words text-[13px] font-medium leading-6">{episode.goal}</p>}
        <p className="line-clamp-3 whitespace-pre-wrap break-words text-[13px] leading-6 [overflow-wrap:anywhere]">{episode.summary || text("摘要未记录", "Summary not recorded")}</p>
      </div><MemoryActions expanded={details.selected?.id === episode.id} onDetails={() => { if (details.selected?.id === episode.id) details.close(); else void details.open(episode); }} onDelete={() => { setMutationError(null); setDeleteCandidate(episode); }} busy={deleting} /></div>
      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] leading-5 text-muted-foreground"><MemoryTime value={episode.started_at} label={text("开始", "Started")} /><MemoryTime value={episode.ended_at} label={text("结束", "Ended")} /><ImportanceBadge value={episode.importance_score} /><span>{text(`${episode.tools_used.length} 个工具`, `${episode.tools_used.length} tools`)}</span><span>{text(`${episode.linked_memory_ids.length} 条关联记忆`, `${episode.linked_memory_ids.length} linked memories`)}</span>{episode.tags.slice(0, 4).map((tag) => <span key={tag} className="rounded bg-muted/60 px-1.5">#{tag}</span>)}</div>
      {details.selected?.id === episode.id && (details.error ? <div role="alert" className="mt-3 text-[12px] text-destructive">{details.error}<Button variant="ghost" size="sm" onClick={() => void details.open(episode)}>{text("重试", "Retry")}</Button></div> : details.details ? <EpisodeDetailPanel episode={details.details} onClose={details.close} /> : <Loader2 className="my-4 h-4 w-4 animate-spin text-muted-foreground" aria-label={text("加载详情中", "Loading details")} />)}
    </li>)}</ul>}
    <DeleteConfirm open={deleteCandidate !== null} title={text("删除这条对话记录？", "Delete this conversation memory?")} onCancel={() => { if (!deletingRef.current) setDeleteCandidate(null); }} onConfirm={() => void confirmDelete()} />
    {deleting && <div className="fixed inset-0 z-[60] grid place-items-center bg-background/60"><Loader2 className="h-5 w-5 animate-spin" aria-label={text("删除中", "Deleting")} /></div>}
  </div>;
}
