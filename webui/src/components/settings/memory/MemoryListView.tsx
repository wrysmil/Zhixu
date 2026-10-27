import { useCallback, useRef, useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";

import { DeleteConfirm } from "@/components/DeleteConfirm";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { deleteMemory, fetchMemory, listMemories, type ListMemoriesOpts } from "@/lib/api";
import type { MemoryOrigin, MemoryPayload, MemoryPriority, MemoryType } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import { MemoryEditDialog } from "./MemoryEditDialog";
import { DetailFields, DetailProvenance, ImportanceBadge, MemoryActions, MemoryFilters, MemoryLoadState, MemoryTime, SourceBadge, WorkspaceLabels, memoryRowClass, memorySelectClass, useMemoryCopy, useMemoryDetails, useMemoryList } from "./MemoryPresentation";

const TYPES: MemoryType[] = ["fact", "preference", "skill", "error", "rule", "experience"];

export function MemoryListView() {
  const { t } = useTranslation();
  const { text } = useMemoryCopy();
  const { client, token } = useClient();
  const [query, setQuery] = useState("");
  const [origin, setOrigin] = useState<MemoryOrigin | "">("");
  const [workspace, setWorkspace] = useState("");
  const [type, setType] = useState<MemoryType | "">("");
  const [priority, setPriority] = useState<MemoryPriority | "">("");
  const [order, setOrder] = useState<NonNullable<ListMemoriesOpts["order"]>>("created");
  const [editing, setEditing] = useState<MemoryPayload | "new" | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<MemoryPayload | null>(null);
  const [deleting, setDeleting] = useState(false);
  const deletingRef = useRef(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const load = useCallback(() => listMemories(token, { q: query.trim() || undefined, origin: origin || undefined, workspace: workspace || undefined, type: type || undefined, priority: priority || undefined, order }), [token, query, origin, workspace, type, priority, order]);
  const { result, loading, error, refresh } = useMemoryList(load, query.trim() ? 250 : 0);
  const details = useMemoryDetails<MemoryPayload>(async (id) => (await fetchMemory(token, id)).memory);
  const filtered = Boolean(query.trim() || origin || workspace || type || priority);
  const clear = () => { setQuery(""); setOrigin(""); setWorkspace(""); setType(""); setPriority(""); };

  const confirmDelete = async () => {
    if (!deleteCandidate || deletingRef.current) return;
    deletingRef.current = true; setDeleting(true); setMutationError(null);
    try {
      await deleteMemory(client, deleteCandidate.id);
      if (details.selected?.id === deleteCandidate.id) details.close();
      setDeleteCandidate(null); refresh();
    } catch (reason) { setMutationError(reason instanceof Error ? reason.message : String(reason)); }
    finally { deletingRef.current = false; setDeleting(false); }
  };

  return <div className="flex flex-col gap-2.5">
    <MemoryFilters query={query} onQuery={setQuery} origin={origin} onOrigin={setOrigin} workspace={workspace} onWorkspace={setWorkspace} result={result} onClear={clear} active={filtered} loading={loading} actions={<Button onClick={() => setEditing("new")} size="sm"><Plus className="mr-1.5 h-4 w-4" aria-hidden />{text("新增记忆", "New memory")}</Button>}>
      <label className="flex items-center gap-2">{text("类型", "Type")}<select className={memorySelectClass} value={type} onChange={(event) => setType(event.target.value as MemoryType | "")}><option value="">{text("全部", "All")}</option>{TYPES.map((value) => <option key={value} value={value}>{t(`settings.memory.type.${value}`, { defaultValue: value })}</option>)}</select></label>
      <label className="flex items-center gap-2">{text("保留时长", "Retention")}<select className={memorySelectClass} value={priority} onChange={(event) => setPriority(event.target.value as MemoryPriority | "")}><option value="">{text("全部", "All")}</option>{(["long_term", "short_term"] as const).map((value) => <option key={value} value={value}>{t(`settings.memory.priority.${value}`, { defaultValue: value })}</option>)}</select></label>
      <label className="flex items-center gap-2">{text("排序", "Sort")}<select className={memorySelectClass} value={order} onChange={(event) => setOrder(event.target.value as typeof order)}><option value="created">{text("创建时间 · 最新在前", "Created · newest first")}</option><option value="updated">{text("更新时间 · 最新在前", "Updated · newest first")}</option><option value="importance">{text("重要性 · 从高到低", "Importance · highest first")}</option></select></label>
    </MemoryFilters>
    {mutationError && <p role="alert" className="text-[12px] text-destructive">{mutationError}</p>}
    <MemoryLoadState error={error} loading={loading} empty={result.items.length === 0} filtered={filtered} onRetry={refresh} />
    {!error && result.items.length > 0 && <ul className="divide-y divide-border/55" aria-label={text("长期信息列表", "Saved memories")} aria-busy={loading}>{result.items.map((memory) => <li key={memory.id} className={memoryRowClass}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]"><SourceBadge record={memory} /><WorkspaceLabels workspaces={memory.workspaces} /><span className="text-muted-foreground">{t(`settings.memory.type.${memory.type}`, { defaultValue: memory.type })}</span><span className="text-muted-foreground">{t(`settings.memory.priority.${memory.priority}`, { defaultValue: memory.priority })}</span>{memory.superseded_by && <span className="text-amber-700 dark:text-amber-400">{text("已被替代", "Superseded")}</span>}{memory.expires_at && new Date(memory.expires_at).getTime() < Date.now() && <span className="text-amber-700 dark:text-amber-400">{text("已过期", "Expired")}</span>}</div>
          <p className="line-clamp-3 whitespace-pre-wrap break-words text-[13px] font-normal leading-6 text-foreground [overflow-wrap:anywhere]">{memory.content}</p>
        </div>
        <MemoryActions onDetails={() => void details.open(memory)} onEdit={() => setEditing(memory)} onDelete={() => { setMutationError(null); setDeleteCandidate(memory); }} busy={deleting} />
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] leading-5 text-muted-foreground"><MemoryTime value={memory.created_at} label={text("创建", "Created")} />{memory.updated_at !== memory.created_at && <MemoryTime value={memory.updated_at} label={text("更新", "Updated")} />}<ImportanceBadge value={memory.importance_score} /><span>{text(`已引用 ${memory.access_count} 次`, `Referenced ${memory.access_count} times`)}</span>{memory.tags.map((tag) => <span className="rounded bg-muted/60 px-1.5" key={tag}>#{tag}</span>)}</div>
    </li>)}</ul>}
    {editing && <MemoryEditDialog memory={editing === "new" ? null : editing} workspaces={result.workspace_options} initialWorkspace={editing === "new" ? workspace : editing.workspace_id ?? workspace} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); details.close(); refresh(); }} />}
    <DeleteConfirm open={deleteCandidate !== null} title={text("删除这条记忆？", "Delete this memory?")} onCancel={() => { if (!deletingRef.current) setDeleteCandidate(null); }} onConfirm={() => void confirmDelete()} />
    {deleting && <div className="fixed inset-0 z-[60] grid place-items-center bg-background/60"><Loader2 className="h-5 w-5 animate-spin" aria-label={text("删除中", "Deleting")} /></div>}
    <Dialog open={details.selected !== null} onOpenChange={(open) => { if (!open) details.close(); }}><DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto"><DialogHeader><DialogTitle>{text("记忆详情", "Memory details")}</DialogTitle><DialogDescription>{text("查看完整内容、来源和记录时间。", "Review the complete content, source and recorded times.")}</DialogDescription></DialogHeader>{details.error ? <div role="alert" className="text-[13px] text-destructive">{details.error}<Button variant="ghost" onClick={() => { if (details.selected) void details.open(details.selected); }}>{text("重试", "Retry")}</Button></div> : details.details ? <MemoryDetailFields memory={details.details} /> : <Loader2 className="mx-auto my-6 h-5 w-5 animate-spin" aria-label={text("加载中", "Loading")} />}</DialogContent></Dialog>
  </div>;
}

function MemoryDetailFields({ memory }: { memory: MemoryPayload }) {
  const { text } = useMemoryCopy();
  const { t } = useTranslation();
  return <div className="space-y-5"><p className="whitespace-pre-wrap break-words text-[13px] leading-6 [overflow-wrap:anywhere]">{memory.content}</p><DetailFields fields={[
    [text("来源", "Source"), <DetailProvenance record={memory} />],
    [text("工作区", "Workspace"), <div className="flex flex-wrap gap-2"><WorkspaceLabels workspaces={memory.workspaces} /></div>],
    [text("创建时间", "Created"), <MemoryTime value={memory.created_at} detailed />],
    [text("更新时间", "Updated"), <MemoryTime value={memory.updated_at} detailed />],
    [text("类型", "Type"), t(`settings.memory.type.${memory.type}`, { defaultValue: memory.type })],
    [text("保留时长", "Retention"), t(`settings.memory.priority.${memory.priority}`, { defaultValue: memory.priority })],
    [text("重要性", "Importance"), <ImportanceBadge value={memory.importance_score} />],
    [text("置信度", "Confidence"), `${Math.round(memory.confidence * 100)}%`],
    [text("引用次数", "Reference count"), String(memory.access_count)],
    [text("最近引用", "Last referenced"), memory.last_accessed_at ? <MemoryTime value={memory.last_accessed_at} detailed /> : "—"],
    [text("过期时间", "Expires"), memory.expires_at ? <MemoryTime value={memory.expires_at} detailed /> : "—"],
    [text("标签", "Tags"), memory.tags.join(" · ")],
    [text("主体", "Subject"), memory.subject], [text("关系", "Predicate"), memory.predicate],
    [text("来源对话记录", "Source episode"), memory.source_episode_id],
    [text("替代记录", "Superseded by"), memory.superseded_by], [text("记录 ID", "Record ID"), memory.id],
  ]} />{Object.keys(memory.metadata).length > 0 && <details className="text-[12px]"><summary className="cursor-pointer text-muted-foreground">{text("原始元数据", "Original metadata")}</summary><pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/40 p-3">{JSON.stringify(memory.metadata, null, 2)}</pre></details>}</div>;
}
