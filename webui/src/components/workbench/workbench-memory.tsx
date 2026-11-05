import { useTranslation } from "react-i18next";

import type { SessionWorkbenchSnapshot } from "./session-workbench-api";
import { WorkbenchBadge, WorkbenchEmpty, WorkbenchSection, useWorkbenchText } from "./workbench-parts";

export function WorkbenchMemories({ snapshot }: {
  snapshot: SessionWorkbenchSnapshot | null; onOpenFilePreview?: (path: string) => void;
}) {
  const text = useWorkbenchText();
  const { i18n } = useTranslation();
  const memories = snapshot?.memories ?? [];
  const formatTime = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString(i18n.language, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  };
  return <div className="px-4 py-3">
    <p className="mb-3 text-[11px] leading-5 text-muted-foreground">{text("区分任务中的上下文与已保存的长期记忆。", "Working context and saved long-term memories are shown separately.")}</p>
    {memories.length ? <ul className="space-y-3">{memories.map((memory) => <li key={memory.id} className="rounded-md border border-border/55 bg-background/60 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <WorkbenchBadge>{memory.kind === "working" ? text("工作记忆", "Working memory") : text("长期记忆", "Long-term memory")}</WorkbenchBadge>
        <span className="text-[10px] text-muted-foreground">{memory.persisted ? text("已持久化", "Saved") : text("尚未持久化", "Not persisted")}</span>
      </div>
      <p className="text-xs font-medium [overflow-wrap:anywhere]">{memory.title}</p>
      <details className="mt-2">
        <summary className="cursor-pointer text-[11px] text-muted-foreground">{text("查看内容", "View content")}</summary>
        <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere]">{memory.content}</p>
      </details>
      <p className="mt-2 text-[10px] text-muted-foreground [overflow-wrap:anywhere]">{text("来源", "Source")}: {memory.source === "session_scratchpad" ? text("本会话工作记忆", "Session scratchpad") : memory.source.startsWith("episode:") ? `${text("会话经历", "Session episode")} ${memory.source.slice(8)}` : memory.source.startsWith("session:") ? text("本会话抽取", "Extracted in this session") : memory.source}</p>
      {memory.kind === "long_term" && <span className="mt-1 block text-[10px] text-muted-foreground [overflow-wrap:anywhere]">{memory.is_global ? text("归属：全局记忆", "Ownership: global memory") : text(`归属：工作区 ${(memory.workspace_id ?? "").slice(0, 8)}`, `Ownership: workspace ${(memory.workspace_id ?? "").slice(0, 8)}`)}</span>}
      {memory.updated_at && <time dateTime={memory.updated_at} className="mt-1 block text-[10px] text-muted-foreground">{formatTime(memory.updated_at)}</time>}
    </li>)}</ul> : <WorkbenchEmpty>{text("尚无与本会话关联的记忆。其他会话的长期记忆不会混入这里。", "No memories linked to this session. Memories from other sessions are excluded.")}</WorkbenchEmpty>}
  </div>;
}

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { fetchWorkbenchMemories } from "./session-workbench-api";
import type { NoteSummary, Observation, Scope } from "./workbench-live-types";
import { WorkbenchMemoryPreview } from "./WorkbenchMemoryPreview";

export function WorkbenchLiveMemory({ snapshot, token, sessionKey }: { snapshot: SessionWorkbenchSnapshot; token: string; sessionKey: string }) {
  const text = useWorkbenchText();
  const memory = snapshot.memory_context!;
  const [pages, setPages] = useState(memory.catalogs);
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setPages(memory.catalogs); }, [memory.catalogs.workspace.revision, memory.catalogs.global.revision]);
  useEffect(() => { setPreview(null); }, [sessionKey, token, snapshot.scope?.project_id]);
  useEffect(() => { const controller = new AbortController(); controllerRef.current = controller; return () => controller.abort(); }, [sessionKey, token, snapshot.scope?.project_id]);
  const controllerRef = useRefController();
  const more = async (scope: Scope) => {
    const page = pages[scope]; if (!page.next_cursor) return;
    const signal = controllerRef.current?.signal; if (!signal) return;
    setError(null);
    try {
      const next = await fetchWorkbenchMemories(token, sessionKey, scope, page.next_cursor, signal);
      if (!signal.aborted) setPages(old => old[scope].revision === page.revision && old[scope].next_cursor === page.next_cursor ? ({ ...old, [scope]: { ...next, items: [...old[scope].items, ...next.items] } }) : old);
    } catch (reason) {
      if (signal.aborted) return;
      if (reason instanceof ApiError && reason.status === 409) {
        try { const first = await fetchWorkbenchMemories(token, sessionKey, scope, null, signal); if (!signal.aborted) setPages(old => old[scope].revision === page.revision ? ({ ...old, [scope]: first }) : old); }
        catch { if (!signal.aborted) setError(text("目录刷新失败", "Could not refresh the catalog")); }
      } else setError(text("加载更多失败", "Could not load more"));
    }
  };
  const unique = (items: Observation[]) => [...new Map(items.map(item => [`${item.resource?.id ?? item.id}:${item.status === "failed" ? "failed" : "ok"}`, item])).values()];
  const groups: [string, Observation[]][] = [[text("已读取", "Read"), memory.reads], [text("写入", "Written"), memory.writes], [text("已提供目录", "Context provided"), memory.provided], [text("索引维护", "Index maintenance"), memory.indexes]];
  return <>
    {preview && <WorkbenchMemoryPreview key={`${sessionKey}:${preview}`} token={token} sessionKey={sessionKey} resourceId={preview} onClose={() => setPreview(null)} />}
    {groups.map(([label, entries]) => <WorkbenchSection key={label} title={label} count={memory.coverage === "unrecorded" && !entries.length ? undefined : unique(entries).length} defaultOpen>
      {entries.length ? <ul className="space-y-1">{unique(entries).map(item => <li key={`${item.resource?.id ?? item.id}:${item.status === "failed" ? "failed" : "ok"}`} className="break-words text-xs">{item.resource?.title || item.label} · {item.status === "failed" ? text("失败", "Failed") : item.kind === "context_provided" ? text("已提供，读取范围未确认", "Provided; read extent not confirmed") : item.extent === "partial" ? text("部分", "Partial") : item.extent === "full" ? text("完整", "Full") : text("范围未知", "Extent unknown")}</li>)}</ul> : <WorkbenchEmpty>{memory.coverage === "unrecorded" ? text("未记录", "Not recorded") : text("暂无记录", "No records")}</WorkbenchEmpty>}
    </WorkbenchSection>)}
    {(["workspace", "global"] as Scope[]).map(group => <WorkbenchSection key={group} title={group === "workspace" ? text("项目记忆", "Project memories") : text("全局记忆", "Global memories")} count={pages[group].total} defaultOpen>
      <ul className="space-y-2">{pages[group].items.map((item: NoteSummary) => <li key={item.id}><button type="button" disabled={!item.preview_available} onClick={() => setPreview(item.id)} className="text-left text-xs enabled:hover:underline disabled:opacity-60">{item.title}</button>{item.description && <p className="break-words text-[11px] text-muted-foreground">{item.description}</p>}</li>)}</ul>
      {pages[group].next_cursor && <button type="button" onClick={() => void more(group)} className="mt-2 text-xs underline">{text("加载更多", "Load more")}</button>}
    </WorkbenchSection>)}
    {memory.profile.available && memory.profile.resource && <WorkbenchSection title={text("画像", "Profile")} defaultOpen><button type="button" className="text-xs underline" onClick={() => setPreview(memory.profile.resource!.id)}>{memory.profile.resource.title}</button></WorkbenchSection>}
    {error && <p role="alert" className="px-4 text-xs text-destructive">{error}</p>}
  </>;
}
import { useRef } from "react";
function useRefController() { return useRef<AbortController | null>(null); }
