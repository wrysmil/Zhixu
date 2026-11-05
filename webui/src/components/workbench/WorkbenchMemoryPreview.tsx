import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { fetchWorkbenchResource } from "./session-workbench-api";
import type { NotePreview } from "./workbench-live-types";
import { useWorkbenchText } from "./workbench-parts";

export function WorkbenchMemoryPreview({ token, sessionKey, resourceId, onClose }: { token: string; sessionKey: string; resourceId: string; onClose: () => void }) {
  const text = useWorkbenchText();
  const closeRef = useRef<HTMLButtonElement>(null);
  const [result, setResult] = useState<NotePreview | null>(null);
  const [error, setError] = useState<number | null>(null);
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const controller = new AbortController(); setResult(null); setError(null); closeRef.current?.focus();
    void fetchWorkbenchResource(token, sessionKey, resourceId, controller.signal).then(value => { if (!controller.signal.aborted) setResult(value); }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof ApiError ? reason.status : 0); });
    return () => { controller.abort(); if (previousFocus?.isConnected) previousFocus.focus(); };
  }, [token, sessionKey, resourceId]);
  return <section aria-label={text("记忆预览", "Memory preview")} className="border-b border-border px-4 py-3">
    <div className="flex items-center justify-between gap-2"><h3 className="min-w-0 break-words text-xs font-semibold">{result?.resource.title ?? text("记忆预览", "Memory preview")}</h3><button ref={closeRef} type="button" onClick={onClose} className="rounded px-2 py-1 text-xs focus-visible:ring-2 focus-visible:ring-ring">{text("关闭", "Close")}</button></div>
    {error !== null ? <p role="alert" className="mt-2 text-xs text-destructive">{error === 403 ? text("没有权限查看此记忆。", "You do not have permission to view this memory.") : error === 404 ? text("记忆不存在或已删除。", "This memory no longer exists.") : text("记忆加载失败，请重试。", "Could not load this memory.")}</p> : result ? <><pre className="mt-2 whitespace-pre-wrap break-words text-xs">{result.content}</pre>{result.truncated && <p className="mt-2 text-xs text-muted-foreground">{text("内容过长，已截断。", "Content was truncated.")}</p>}</> : <p role="status" className="mt-2 text-xs">{text("正在加载…", "Loading…")}</p>}
  </section>;
}
