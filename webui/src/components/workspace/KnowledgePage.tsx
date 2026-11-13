import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  FileText,
  Library,
  Loader2,
  Network,
  Plus,
  RefreshCcw,
  Search,
  SlidersHorizontal,
  Trash2,
  Wrench,
} from "lucide-react";


import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState } from "@/components/workspace/EmptyState";
import { KnowledgeLibraryList } from "./knowledge/KnowledgeLibraryList";
import { KnowledgeDocumentTable } from "./knowledge/KnowledgeDocumentTable";
import { KnowledgeDocumentDialog } from "./knowledge/KnowledgeDocumentDialog";
import { KnowledgeRetrievalPage } from "./knowledge/KnowledgeRetrievalPage";
import { KnowledgeImportSheet} from "./knowledge/KnowledgeImportSheet";
import { KnowledgeSettingsPanel } from "./knowledge/KnowledgeSettingsPanel";
import { Breadcrumb } from "@/components/ui/breadcrumb";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { KnowledgeStatus } from "./knowledge-shared/KnowledgeStatus";
import "./knowledge-shared/experience.css";
import {
  addKnowledgeDoc,
  addKnowledgeDocFromPath,
  createKnowledgeKb,
  deleteKnowledgeDoc,
  deleteKnowledgeKb,
  fetchKnowledgeDoc,
  fetchKnowledgeDocs,
  fetchKnowledgeKbs,
  fetchKnowledgeStats,
  reindexKnowledgeDoc,
  reindexKnowledgeKb,
  repairKnowledge,
  type KnowledgeChunk,
  type KnowledgeDoc,
  type KnowledgeKb,
  type KnowledgeStats,
  type KnowledgeVisibility,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

/** WebUI 上传口径（spec §13.1）：后缀白名单与 6 MB 上限，不含 csv。 */
const UPLOAD_MIME_BY_SUFFIX: Record<string, string> = {
  ".pdf": "application/pdf",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".md": "text/markdown",
  ".txt": "text/plain",
};
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const POLL_INTERVAL_MS = 2000;
const CHUNK_PREVIEW_PAGE_SIZE = 10;

// 图谱 tab 独立 chunk：图关闭时 d3-force 不进首屏包。
const KnowledgeGraphPanel = lazy(() =>
  import("@/components/workspace/knowledge/GraphPanel").then((module) => ({
    default: module.GraphPanel,
  })),
);

function fileSuffix(name: string): string {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("read failed"));
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}

function isDocActive(doc: KnowledgeDoc): boolean {
  return doc.status === "pending" || doc.status === "processing";
}

interface ConfirmState {
  kind: "kb" | "doc";
  id: string;
  label: string;
}

/**
 * 知识库页面（`#/knowledge`）。
 *
 * 左栏是知识库清单（作用域 + 文档数 + 启用态），主区是所选知识库的文档表与
 * 服务端路径入库，底部是检索预览。删除、修复等破坏性动作一律先确认，成功后
 * 从服务端重取列表——不做乐观删除，避免索引状态与界面不一致。
 */
export function KnowledgePage({ workspaceIds }: { workspaceIds?: string[] }) {
  const { t } = useTranslation();
  const { client, token } = useClient();
  const scope = useMemo(
    () => (workspaceIds && workspaceIds.length > 0 ? workspaceIds : undefined),
    [workspaceIds],
  );
  const defaultWorkspaceId = workspaceIds?.[0];

  const rootRef = useRef<HTMLDivElement>(null);
  const listPosition = useRef<{ id: string; positions: [HTMLElement, number][] } | null>(null);
  const [libraryQuery, setLibraryQuery] = useState("");
  const [visibilityFilter, setVisibilityFilter] = useState("all");
  const [sort, setSort] = useState("name");
  const [docOffset, setDocOffset] = useState(0);
  const [docTotal, setDocTotal] = useState(0);
  const docsViewRef = useRef<object | null>(null);
  const docsSeq = useRef(0);
  const mutationLock = useRef(false);
  const createLock = useRef(false);
  const repairLock = useRef(false);
  const [importOpen, setImportOpen] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadStage, setUploadStage] = useState("");
  const [uploadStageIndex, setUploadStageIndex] = useState(-1);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [kbs, setKbs] = useState<KnowledgeKb[]>([]);
  const [activeKbId, setActiveKbId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailTab, setDetailTab] = useState<"documents" | "graph" | "settings">(
    "documents",
  );
  const [kbsLoading, setKbsLoading] = useState(true);
  const [kbsError, setKbsError] = useState<string | null>(null);

  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [docsError, setDocsError] = useState<string | null>(null);
  const [stats, setStats] = useState<KnowledgeStats | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newVisibility, setNewVisibility] = useState<KnowledgeVisibility>("workspace");
  const [creating, setCreating] = useState(false);

  const [serverPath, setServerPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const [confirm, setConfirm] = useState<ConfirmState | null>(null);

  const [chunkPreview, setChunkPreview] = useState<KnowledgeDoc | null>(null);
  const [previewChunks, setPreviewChunks] = useState<KnowledgeChunk[]>([]);
  const [previewTotal, setPreviewTotal] = useState(0);
  const [previewDocTotal, setPreviewDocTotal] = useState(0);
  const [previewOffset, setPreviewOffset] = useState(0);
  const [previewQuery, setPreviewQuery] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewSeq = useRef(0);
  // 检索是知识库域的独立页；返回时回到进入前的层级。
  const [retrievalOpen, setRetrievalOpen] = useState(false);
  const [retrievalFromDetail, setRetrievalFromDetail] = useState(false);
  const openRetrieval = useCallback((fromDetail: boolean) => {
    setRetrievalFromDetail(fromDetail);
    setRetrievalOpen(true);
  }, []);
  const closeRetrieval = useCallback(() => {
    setRetrievalOpen(false);
    if (!retrievalFromDetail) setDetailOpen(false);
  }, [retrievalFromDetail]);
  // 库级变更（删除/重建/修复）后退出检索页，避免展示旧作用域。
  const clearSearch = useCallback(() => {
    setRetrievalOpen(false);
    setRetrievalFromDetail(false);
  }, []);

  const [repairOpen, setRepairOpen] = useState(false);
  const [repairFts, setRepairFts] = useState(true);
  const [repairVectors, setRepairVectors] = useState(true);
  const [repairPurge, setRepairPurge] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [repairDone, setRepairDone] = useState(false);

  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadStats = useCallback(async () => {
    try {
      const payload = await fetchKnowledgeStats(token, { workspaceIds: scope });
      setStats(payload);
    } catch {
      /* 统计是辅助信息，失败不影响主流程 */
    }
  }, [scope, token]);

  const loadKbs = useCallback(async () => {
    setKbsLoading(true);
    try {
      const payload = await fetchKnowledgeKbs(token, { workspaceIds: scope });
      setKbs(payload.kbs);
      setEnabled(payload.enabled);
      setKbsError(null);
      setActiveKbId((current) => {
        if (current && payload.kbs.some((kb) => kb.id === current)) return current;
        return payload.kbs[0]?.id ?? null;
      });
    } catch (reason: unknown) {
      setKbsError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setKbsLoading(false);
    }
  }, [scope, token]);

  useEffect(() => {
    void loadKbs();
    void loadStats();
  }, [loadKbs, loadStats]);

  const docsView = useMemo(() => ({}), [activeKbId, detailOpen, docOffset, scope, token]);
  const loadDocs = useCallback(async () => {
    if (docsViewRef.current !== docsView) return;
    if (!activeKbId || !detailOpen) {
      if (!activeKbId) setDocs([]);
      return;
    }
    const seq = ++docsSeq.current;
    setDocsLoading(true);
    try {
      const payload = await fetchKnowledgeDocs(token, { kbId: activeKbId, workspaceIds: scope, limit: 20, offset: docOffset });
      if (seq !== docsSeq.current) return;
      setDocTotal(payload.total);
      setDocs(payload.documents);
      setDocsError(null);
    } catch (reason: unknown) {
      if (seq !== docsSeq.current) return;
      setDocsError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (seq === docsSeq.current) setDocsLoading(false);
    }
  }, [activeKbId, detailOpen, docOffset, docsView, scope, token]);

  useEffect(() => {
    docsViewRef.current = docsView;
    void loadDocs();
    return () => { docsSeq.current += 1; docsViewRef.current = null; };
  }, [docsView, loadDocs]);

  // 仅当有文档仍在 pipelined 状态时轮询；ready/failed 立即停表。
  useEffect(() => {
    if (!docs.some(isDocActive)) return;
    pollTimer.current = setTimeout(() => {
      void loadDocs();
      void loadStats();
    }, POLL_INTERVAL_MS);
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
      pollTimer.current = null;
    };
  }, [docs, loadDocs, loadStats]);

  const activeKb = kbs.find((kb) => kb.id === activeKbId) ?? null;

  const runAction = useCallback(
    async (action: () => Promise<unknown>, after?: () => Promise<void> | void) => {
      if (mutationLock.current) return;
      mutationLock.current = true;
      setBusy(true);
      setActionError(null);
      try {
        await action();
        if (after) await after();
      } catch (reason: unknown) {
        setActionError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        mutationLock.current = false;
        setBusy(false);
      }
    },
    [],
  );

  const onCreateKb = useCallback(async () => {
    const name = newName.trim();
    if (!name || createLock.current) return;
    createLock.current = true;
    setCreating(true);
    setActionError(null);
    try {
      await createKnowledgeKb(client, {
        name,
        description: newDescription.trim(),
        visibility: newVisibility,
        workspaceId: defaultWorkspaceId,
      });
      setNewName("");
      setNewDescription("");
      setNewVisibility("workspace");
      setCreateOpen(false);
      await loadKbs();
      await loadStats();
    } catch (reason: unknown) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      createLock.current = false;
      setCreating(false);
    }
  }, [client, defaultWorkspaceId, loadKbs, loadStats, newDescription, newName, newVisibility]);

  const onConfirmDelete = useCallback(async () => {
    if (!confirm) return;
    const target = confirm;
    setConfirm(null);
    if (target.kind === "kb") {
      await runAction(
        () => deleteKnowledgeKb(client, target.id),
        async () => {
          await loadKbs();
          await loadStats();
          clearSearch();
          setDetailOpen(false);
          setDetailTab("documents");
        },
      );
      return;
    }
    await runAction(
      () => deleteKnowledgeDoc(client, target.id),
      async () => {
        await loadDocs();
        await loadKbs();
        await loadStats();
        clearSearch();
      },
    );
  }, [clearSearch, client, confirm, loadDocs, loadKbs, loadStats, runAction]);

  const closeChunkPreview = useCallback(() => {
    previewSeq.current += 1;
    setChunkPreview(null);
    setPreviewChunks([]);
    setPreviewTotal(0);
    setPreviewDocTotal(0);
    setPreviewOffset(0);
    setPreviewQuery("");
    setPreviewError(null);
    setPreviewLoading(false);
  }, []);

  const loadChunkPreview = useCallback(
    async (doc: KnowledgeDoc, offset: number, query: string) => {
      const seq = previewSeq.current + 1;
      previewSeq.current = seq;
      setPreviewOffset(offset);
      setPreviewQuery(query);
      setPreviewError(null);
      setPreviewLoading(true);
      try {
        const payload = await fetchKnowledgeDoc(token, {
          id: doc.id,
          chunkLimit: CHUNK_PREVIEW_PAGE_SIZE,
          chunkOffset: offset,
          chunkQuery: query || undefined,
        });
        if (seq !== previewSeq.current) return;
        setChunkPreview(payload.document ?? doc);
        setPreviewChunks(payload.chunks);
        setPreviewTotal(
          typeof payload.chunkTotal === "number" ? payload.chunkTotal : payload.chunkCount,
        );
        setPreviewDocTotal(payload.chunkCount);
      } catch (reason: unknown) {
        if (seq !== previewSeq.current) return;
        setPreviewError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (seq === previewSeq.current) setPreviewLoading(false);
      }
    },
    [token],
  );

  const onOpenChunkPreview = useCallback(
    async (doc: KnowledgeDoc) => {
      setChunkPreview(doc);
      setPreviewChunks([]);
      setPreviewTotal(doc.chunkCount);
      setPreviewDocTotal(doc.chunkCount);
      await loadChunkPreview(doc, 0, "");
    },
    [loadChunkPreview],
  );

  const onChunkPageChange = useCallback(
    (offset: number, query: string) => {
      if (chunkPreview) void loadChunkPreview(chunkPreview, offset, query);
    },
    [chunkPreview, loadChunkPreview],
  );

  const onUpload = useCallback(
    async (file: File) => {
      if (!activeKbId || mutationLock.current) return;
      setUploadFile(file);
      const suffix = fileSuffix(file.name);
      const mime = UPLOAD_MIME_BY_SUFFIX[suffix];
      if (!mime) {
        setActionError(t("knowledge.uploadBadType"));
        return;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        setActionError(t("knowledge.uploadTooLarge"));
        return;
      }
      await runAction(async () => {
        setUploadStageIndex(0);
        setUploadStage(t("knowledge.experience.reading", { defaultValue: "正在读取文件" }));
        let base64: string;
        try { base64 = await readFileAsBase64(file); }
        catch { throw new Error(t("knowledge.experience.readFailed", { defaultValue: "无法读取文件，请重新选择后重试" })); }
        setUploadStageIndex(1);
        setUploadStage(t("knowledge.experience.submitting", { defaultValue: "正在提交资料" }));
        await addKnowledgeDoc(client, {
          kbId: activeKbId,
          name: file.name,
          dataUrl: `data:${mime};base64,${base64}`,
          workspaceId: defaultWorkspaceId,
        });
        setUploadStageIndex(2);
        setUploadFile(null);
        setImportOpen(false);
      }, async () => {
        await loadDocs();
        await loadKbs();
        await loadStats();
        clearSearch();
      });
    },
    [activeKbId, clearSearch, client, defaultWorkspaceId, loadDocs, loadKbs, loadStats, runAction, t],
  );

  const onAddServerPath = useCallback(async () => {
    const path = serverPath.trim();
    if (!activeKbId || !path) return;
    await runAction(async () => {
      await addKnowledgeDocFromPath(client, {
        kbId: activeKbId,
        path,
        workspaceId: defaultWorkspaceId,
      });
      setServerPath("");
      setImportOpen(false);
    }, async () => {
      await loadDocs();
      await loadKbs();
      await loadStats();
      clearSearch();
    });
  }, [
    activeKbId,
    clearSearch,
    client,
    defaultWorkspaceId,
    loadDocs,
    loadKbs,
    loadStats,
    runAction,
    serverPath,
  ]);

  const onRepair = useCallback(async () => {
    if (repairLock.current) return;
    repairLock.current = true;
    setRepairing(true);
    setRepairDone(false);
    setActionError(null);
    try {
      await repairKnowledge(client, {
        fts: repairFts,
        vectors: repairVectors,
        purge: repairPurge,
        workspaceId: defaultWorkspaceId,
      });
      setRepairOpen(false);
      setRepairDone(true);
      await loadStats();
      await loadDocs();
      await loadKbs();
      clearSearch();
    } catch (reason: unknown) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      repairLock.current = false;
      setRepairing(false);
    }
  }, [
    clearSearch,
    client,
    defaultWorkspaceId,
    loadDocs,
    loadStats,
    repairFts,
    repairPurge,
    repairVectors,
  ]);

  const disabled = enabled === false;
  const visibleStats = stats ?? {
    enabled: true, kbs: 0, documents: 0, chunks: 0, pending: 0, vector: 0,
  };

  const filteredKbs = kbs.filter(kb => kb.name.toLocaleLowerCase().includes(libraryQuery.toLocaleLowerCase())
    && (visibilityFilter === "all" || kb.visibility === visibilityFilter)).sort((a, b) =>
      sort === "documents" ? b.documentCount - a.documentCount :
      sort === "updated" ? (b.updatedAt || "").localeCompare(a.updatedAt || "") : a.name.localeCompare(b.name));

  const openLibrary = (kb: KnowledgeKb) => {
    const positions: [HTMLElement, number][] = [];
    let element: HTMLElement | null = rootRef.current;
    while (element) { positions.push([element, element.scrollTop]); element = element.parentElement; }
    if (document.scrollingElement instanceof HTMLElement) positions.push([document.scrollingElement, document.scrollingElement.scrollTop]);
    listPosition.current = { id: kb.id, positions };
    docsSeq.current += 1;
    clearSearch();
    if (kb.id !== activeKbId) { setDocs([]); setDocTotal(0); }
    setDocOffset(0);
    setActiveKbId(kb.id);
    setDetailTab("documents");
    setDetailOpen(true);
  };
  const returnToLibraries = () => {
    docsSeq.current += 1;
    clearSearch();
    closeChunkPreview();
    setDetailOpen(false);
  };
  useEffect(() => {
    if (detailOpen || !listPosition.current) return;
    const { id, positions } = listPosition.current;
    rootRef.current?.querySelector<HTMLButtonElement>(`[data-kb-id="${id}"]`)?.focus({ preventScroll: true });
    for (const [element, top] of positions) element.scrollTop = top;
  }, [detailOpen]);
  useEffect(() => () => { previewSeq.current += 1; }, []);

  return (
    <div ref={rootRef} className="knowledge-experience assistant-page flex h-full min-w-0 flex-col" data-testid="knowledge-page">
      {retrievalOpen ? (
        <KnowledgeRetrievalPage
          token={token}
          transport={client}
          scope={scope}
          kbs={kbs}
          initialKbId={detailOpen && activeKb ? activeKb.id : undefined}
          onBack={closeRetrieval}
          onOpenDoc={(doc) => void onOpenChunkPreview(doc)}
        />
      ) : detailOpen && activeKb ? (
        <div className="flex flex-col gap-3">
          <Breadcrumb
            ariaLabel={t("knowledge.title")}
            items={[
              {
                label: t("knowledge.title"),
                ariaLabel: t("knowledge.experience.backToLibraries", { defaultValue: "返回知识库" }),
                onClick: returnToLibraries,
              },
              { label: activeKb.name, current: true },
            ]}
          />
          <div className="knowledge-panel knowledge-page-header p-5">
            <div className="flex flex-wrap items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <Library className="h-5 w-5" aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                <h1 className="text-[18px] font-semibold text-foreground">{activeKb.name}</h1>
                {activeKb.description ? (
                  <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                    {activeKb.description}
                  </p>
                ) : null}
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  <span className="rounded-full border border-border/70 bg-background/70 px-1.5 py-0.5">
                    {activeKb.visibility === "global"
                      ? t("knowledge.visibilityGlobal")
                      : t("knowledge.visibilityWorkspace")}
                  </span>
                  <span className="rounded-full bg-muted/60 px-1.5 py-0.5 tabular-nums">
                    {t("knowledge.docCount", { count: activeKb.documentCount })}
                  </span>
                  <span
                    className={cn(
                      "rounded-full px-1.5 py-0.5",
                      activeKb.isEnabled
                        ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
                        : "bg-muted text-muted-foreground",
                    )}
                  >
                    {activeKb.isEnabled ? t("knowledge.enabled") : t("knowledge.disabled")}
                  </span>
                  {activeKb.updatedAt ? (
                    <span className="tabular-nums">{activeKb.updatedAt.slice(0, 10)}</span>
                  ) : null}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => openRetrieval(true)}
                  className="h-8 gap-1.5 rounded-full px-3 text-sm font-medium"
                >
                  <Search className="h-3.5 w-3.5" aria-hidden />
                  {t("knowledge.searchTitle")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    void runAction(
                      () => reindexKnowledgeKb(client, activeKb.id),
                      async () => {
                        await loadDocs();
                        await loadKbs();
                        await loadStats();
                        clearSearch();
                      },
                    );
                  }}
                  className="h-8 gap-1.5 rounded-full px-3 text-sm font-medium"
                >
                  <RefreshCcw className="h-3.5 w-3.5" aria-hidden />
                  {t("knowledge.reindexKb")}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={t("knowledge.deleteKb")}
                  title={t("knowledge.deleteKb")}
                  disabled={busy}
                  onClick={() => setConfirm({ kind: "kb", id: activeKb.id, label: activeKb.name })}
                  className="h-8 gap-1.5 rounded-full px-2.5 text-sm text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  {t("knowledge.deleteKb")}
                </Button>
              </div>
            </div>
          </div>
          <SegmentedControl
            mode="tabs"
            ariaLabel={t("knowledge.title")}
            value={detailTab}
            onChange={(value) => setDetailTab(value as typeof detailTab)}
            className="self-start"
            options={[
              {
                value: "documents",
                label: (
                  <span className="inline-flex items-center gap-1.5">
                    <FileText className="h-3.5 w-3.5" aria-hidden />
                    {t("knowledge.documents")}
                  </span>
                ),
              },
              {
                value: "graph",
                label: (
                  <span className="inline-flex items-center gap-1.5">
                    <Network className="h-3.5 w-3.5" aria-hidden />
                    {t("knowledge.graph.tab")}
                  </span>
                ),
              },
              {
                value: "settings",
                label: (
                  <span className="inline-flex items-center gap-1.5">
                    <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden />
                    {t("knowledge.settings.tab", { defaultValue: "检索设置" })}
                  </span>
                ),
              },
            ]}
          />
        </div>
      ) : (
        <header className="knowledge-page-header">
          <div className="space-y-2"><h1>{t("knowledge.title")}</h1><p className="text-sm text-muted-foreground">{t("knowledge.subtitle")}</p></div>
          <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => openRetrieval(false)}
                disabled={!kbs.length}
                className="h-8 gap-1.5 rounded-full px-3 text-sm font-medium"
              >
                <Search className="h-3.5 w-3.5" aria-hidden />
                {t("knowledge.searchTitle")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setRepairOpen(true)}
                disabled={disabled}
                className="h-8 gap-1.5 rounded-full px-3 text-sm font-medium"
              >
                <Wrench className="h-3.5 w-3.5" aria-hidden />
                {t("knowledge.repair")}
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={() => setCreateOpen((open) => !open)}
                disabled={disabled}
                className="h-8 gap-1.5 rounded-full px-3.5 text-sm font-semibold"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden />
                {t("knowledge.newKb")}
              </Button>
</div>
        </header>
      )}

      {disabled ? (
        <div
          data-testid="knowledge-disabled-banner"
          role="status"
          className="mt-4 flex items-start gap-2 rounded-control border border-amber-300/70 bg-amber-50/80 px-3 py-2 text-sm leading-relaxed text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-300" aria-hidden />
          <span>{t("knowledge.disabledBanner")}</span>
        </div>
      ) : null}

      {!detailOpen ? (
      <section
        data-testid="knowledge-stats"
        aria-label={t("knowledge.stats")}
        className="mt-5 flex flex-wrap gap-x-6 gap-y-2 border-y py-4"
      >
        {[
          ["kbs", t("knowledge.statKbs"), visibleStats.kbs],
          ["docs", t("knowledge.statDocs"), visibleStats.documents],
          ["chunks", t("knowledge.statChunks"), visibleStats.chunks],
          ["pending", t("knowledge.statPending"), visibleStats.pending],
          ["vectors", t("knowledge.statVectors"), visibleStats.vector ?? 0],
        ].map(([id, label, value]) => <div key={id} className="flex items-baseline gap-2 text-sm text-muted-foreground">
          <span data-testid={`knowledge-stat-${id}`} className="font-semibold tabular-nums text-foreground">{stats ? value : "—"}</span><span>{label}</span>
        </div>)}
      </section>
      ) : null}

      {kbsError ? (
        <div role="alert" className="mt-4 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {t("knowledge.loadError")}: {kbsError}
        </div>
      ) : null}

      {actionError ? (
        <div role="alert" className="mt-4 flex items-start gap-2 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <span className="min-w-0 flex-1 break-words">{t("knowledge.actionError")}: {actionError}</span>
          <button
            type="button"
            aria-label={t("knowledge.close")}
            onClick={() => setActionError(null)}
            className="shrink-0"
          >
            <CircleSlash className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
      ) : null}

      {repairDone ? (
        <div role="status" className="mt-4 flex items-center gap-2 rounded-control border border-emerald-300/70 bg-emerald-50/80 px-3 py-2 text-sm text-emerald-900 dark:border-emerald-700/40 dark:bg-emerald-950/30 dark:text-emerald-100">
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
          {t("knowledge.repairDone")}
        </div>
      ) : null}

      {!detailOpen && createOpen && !disabled ? (
        <form
          className="mt-4 grid gap-3 rounded-panel border border-border/60 bg-muted/20 p-4 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            void onCreateKb();
          }}
        >
          <label className="flex flex-col gap-1 text-sm font-medium text-foreground">
            {t("knowledge.name")}
            <Input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder={t("knowledge.namePlaceholder")}
              className="h-9 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm font-medium text-foreground">
            {t("knowledge.visibility")}
            <select
              value={newVisibility}
              onChange={(event) => setNewVisibility(event.target.value as KnowledgeVisibility)}
              className="h-9 rounded-control border border-input bg-background px-2 text-sm"
            >
              <option value="workspace">{t("knowledge.visibilityWorkspace")}</option>
              <option value="global">{t("knowledge.visibilityGlobal")}</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm font-medium text-foreground sm:col-span-2">
            {t("knowledge.description")}
            <Textarea
              value={newDescription}
              onChange={(event) => setNewDescription(event.target.value)}
              placeholder={t("knowledge.descriptionPlaceholder")}
              rows={2}
              className="text-sm"
            />
          </label>
          <div className="flex items-center gap-2 sm:col-span-2">
            <Button
              type="submit"
              size="sm"
              disabled={creating || !newName.trim()}
              className="h-8 rounded-full px-3.5 text-sm font-semibold"
            >
              {creating ? <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden /> : null}
              {t("knowledge.create")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setCreateOpen(false)}
              className="h-8 rounded-full px-3 text-sm"
            >
              {t("knowledge.cancel")}
            </Button>
          </div>
        </form>
      ) : null}

      <div className="mt-4 flex flex-col gap-4">
        {!detailOpen ? (
        <section className="knowledge-motion knowledge-motion-enter" data-testid="knowledge-kb-list" aria-label={t("knowledge.kbTitle")}>
          <div className="knowledge-toolbar mb-5">
            <Input className="h-10 w-full sm:max-w-xs" aria-label={t("knowledge.experience.searchLibraries", { defaultValue: "搜索知识库名称" })} placeholder={t("knowledge.experience.searchLibraries", { defaultValue: "搜索知识库名称" })} value={libraryQuery} onChange={e => setLibraryQuery(e.target.value)} />
            {libraryQuery && <Button variant="ghost" onClick={() => setLibraryQuery("")}>{t("knowledge.experience.clearSearch", { defaultValue: "清空搜索" })}</Button>}
            <label className="flex items-center gap-2 text-sm">{t("knowledge.experience.filterScope", { defaultValue: "筛选范围" })}<select className="h-10 rounded border bg-background px-3" value={visibilityFilter} onChange={e => setVisibilityFilter(e.target.value)}>
              <option value="all">{t("knowledge.experience.allScopes", { defaultValue: "全部范围" })}</option>
              <option value="workspace">{t("knowledge.visibilityWorkspace")}</option><option value="global">{t("knowledge.visibilityGlobal")}</option>
            </select></label>
            <label className="flex items-center gap-2 text-sm">{t("knowledge.experience.sort", { defaultValue: "排序" })}<select className="h-10 rounded border bg-background px-3" value={sort} onChange={e => setSort(e.target.value)}>
              <option value="name">{t("knowledge.name")}</option>
              <option value="documents">{t("knowledge.statDocs")}</option>
              <option value="updated">{t("knowledge.experience.updated", { defaultValue: "更新日期" })}</option>
            </select></label>
          </div>
          {kbsLoading && !kbs.length ? <KnowledgeStatus label={t("knowledge.loading")} tone="pending" busy /> :
            !kbs.length ? <EmptyState title={t("knowledge.kbEmpty")} description={t("knowledge.kbEmptyHint")} /> :
            !filteredKbs.length ? <p className="py-8 text-sm text-muted-foreground">{t("knowledge.experience.noMatchingLibraries", { defaultValue: "没有符合条件的知识库" })}</p> :
            <KnowledgeLibraryList kbs={filteredKbs} busy={busy} onOpen={openLibrary} onDelete={kb => setConfirm({ kind: "kb", id: kb.id, label: kb.name })} />}
          {kbsError && <Button variant="outline" onClick={() => void loadKbs()}>{t("knowledge.experience.retry", { defaultValue: "重试" })}</Button>}
        </section>
        ) : null}

        {detailOpen ? (
        <div className="grid gap-4">
          {detailTab === "documents" ? (
          <div className="h-fit min-w-0 rounded-panel border border-border/60 bg-background p-4">
            <div className="flex flex-wrap items-center gap-2 pb-3">
              <h2 className="flex flex-1 items-center gap-2 text-sm font-semibold text-foreground">
                <span className="flex h-6 w-6 items-center justify-center rounded-md bg-sky-100 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
                  <FileText className="h-3.5 w-3.5" aria-hidden />
                </span>
                {t("knowledge.documents")}
                {activeKb ? (
                  <span className="text-xs font-normal text-muted-foreground">
                    {activeKb.name}
                  </span>
                ) : null}
                <span className="rounded-full bg-muted/60 px-2 py-0.5 text-xs font-medium tabular-nums text-muted-foreground">
                  {docs.length}
                </span>
              </h2>
            </div>

            {!activeKbId ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                {t("knowledge.noSelection")}
              </p>
            ) : (
              <>
                <div className="knowledge-toolbar mb-4">
                  <Button onClick={() => { setActionError(null); setImportOpen(true); }} disabled={busy}>{t("knowledge.experience.addMaterial", { defaultValue: "添加资料" })}</Button>
                  {docsLoading && <KnowledgeStatus label={t("knowledge.loading")} tone="pending" busy />}
                </div>

                {docsError ? (
                  <div role="alert" className="mb-2 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                    {t("knowledge.documentsError")}: {docsError}
                    <Button variant="ghost" onClick={() => void loadDocs()}>{t("knowledge.experience.retry", { defaultValue: "重试" })}</Button>
                  </div>
                ) : null}

                {docsLoading && docs.length === 0 ? (
                  <p className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden />
                    {t("knowledge.loading")}
                  </p>
                ) : docs.length === 0 ? (
                  <EmptyState
                    title={t("knowledge.documentsEmpty")}
                    description={t("knowledge.documentsEmptyHint")}
                  />
                ) : (
                  <KnowledgeDocumentTable docs={docs} busy={busy} onOpenChunkPreview={doc => void onOpenChunkPreview(doc)}
                    onDelete={doc => setConfirm({ kind: "doc", id: doc.id, label: doc.title })}
                    onReindex={doc => void runAction(() => reindexKnowledgeDoc(client, doc.id), async () => {
                      await loadDocs(); await loadKbs(); await loadStats(); clearSearch();
                    })} />
                )}
                <nav className="mt-4 flex flex-wrap items-center justify-end gap-3 text-sm" aria-label={t("knowledge.experience.pagination", { defaultValue: "文档分页" })}>
                  <span className="tabular-nums">{docTotal ? docOffset + 1 : 0}–{Math.min(docOffset + docs.length, docTotal)} / {docTotal}</span>
                  <Button variant="outline" disabled={docsLoading || docOffset === 0} onClick={() => { docsSeq.current += 1; setDocOffset(Math.max(0, docOffset - 20)); }}>{t("knowledge.experience.previous", { defaultValue: "上一页" })}</Button>
                  <Button variant="outline" disabled={docsLoading || docOffset + 20 >= docTotal} onClick={() => { docsSeq.current += 1; setDocOffset(docOffset + 20); }}>{t("knowledge.experience.next", { defaultValue: "下一页" })}</Button>
                </nav>
              </>
            )}
          </div>
          ) : null}
                    {detailTab === "settings" ? (
            <KnowledgeSettingsPanel token={token} transport={client} />
          ) : null}
          {detailTab === "graph" && activeKb ? (
            <Suspense
              fallback={
                <p className="flex items-center gap-2 rounded-panel border border-border/60 bg-background p-4 text-sm text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden />
                  {t("knowledge.loading")}
                </p>
              }
            >
              <KnowledgeGraphPanel
                kbId={activeKb.id}
                token={token}
                client={client}
                onOpenChunk={async (docId) => {
                  await onOpenChunkPreview(docs.find(doc => doc.id === docId) ?? {
                    id: docId, kbId: activeKb.id, title: docId, originalName: "",
                    status: "ready", chunkCount: 0, charCount: 0,
                  });
                }}
              />
            </Suspense>
          ) : null}
        </div>
        ) : null}
      </div>

      <KnowledgeImportSheet open={importOpen} onClose={() => setImportOpen(false)} busy={busy} error={actionError} stage={uploadStage} stageIndex={uploadStageIndex}
        file={uploadFile} path={serverPath} onPath={setServerPath} onUpload={file => void onUpload(file)} onAddPath={() => void onAddServerPath()} />
      <KnowledgeDocumentDialog doc={chunkPreview} token={token} previewChunks={previewChunks} previewTotal={previewTotal}
        previewDocTotal={previewDocTotal} previewOffset={previewOffset} previewQuery={previewQuery}
        previewPageSize={CHUNK_PREVIEW_PAGE_SIZE} previewLoading={previewLoading}
        previewError={previewError} onClose={closeChunkPreview} onPageChange={onChunkPageChange}
        onRetry={() => { if (chunkPreview) void loadChunkPreview(chunkPreview, previewOffset, previewQuery); }} />

      {confirm ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="knowledge-confirm-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        >
          <div className="w-full max-w-[420px] rounded-panel border border-border bg-background p-5 shadow-2xl">
            <h2 id="knowledge-confirm-title" className="text-[14px] font-semibold text-destructive">
              {confirm.kind === "kb" ? t("knowledge.deleteKb") : t("knowledge.deleteDoc")}
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {confirm.kind === "kb"
                ? t("knowledge.deleteKbConfirm", { name: confirm.label })
                : t("knowledge.deleteDocConfirm", { title: confirm.label })}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setConfirm(null)}
                className="h-8 rounded-full px-3 text-sm"
              >
                {t("knowledge.cancel")}
              </Button>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                onClick={() => void onConfirmDelete()}
                className="h-8 rounded-full px-3.5 text-sm font-semibold"
              >
                {t("knowledge.confirm")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {repairOpen ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="knowledge-repair-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        >
          <div className="w-full max-w-[460px] rounded-panel border border-border bg-background p-5 shadow-2xl">
            <h2 id="knowledge-repair-title" className="text-[14px] font-semibold text-foreground">
              {t("knowledge.repairTitle")}
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {t("knowledge.repairHint")}
            </p>
            <div className="mt-3 flex flex-col gap-2 text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={repairFts}
                  onChange={(event) => setRepairFts(event.target.checked)}
                  className="h-3.5 w-3.5"
                />
                {t("knowledge.repairFts")}
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={repairVectors}
                  onChange={(event) => setRepairVectors(event.target.checked)}
                  className="h-3.5 w-3.5"
                />
                {t("knowledge.repairVectors")}
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={repairPurge}
                  onChange={(event) => setRepairPurge(event.target.checked)}
                  className="h-3.5 w-3.5"
                />
                {t("knowledge.repairPurge")}
              </label>
            </div>
            {repairPurge ? (
              <p className="mt-2 flex items-start gap-1.5 rounded-control border border-destructive/30 bg-destructive/5 px-2.5 py-2 text-sm text-destructive">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                {t("knowledge.repairPurgeWarning")}
              </p>
            ) : null}
            <div className="mt-4 flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setRepairOpen(false)}
                disabled={repairing}
                className="h-8 rounded-full px-3 text-sm"
              >
                {t("knowledge.cancel")}
              </Button>
              <Button
                type="button"
                variant={repairPurge ? "destructive" : "default"}
                size="sm"
                onClick={() => void onRepair()}
                disabled={repairing || (!repairFts && !repairVectors && !repairPurge)}
                className="h-8 gap-1.5 rounded-full px-3.5 text-sm font-semibold"
              >
                {repairing ? <Loader2 className="h-3.5 w-3.5 knowledge-motion animate-spin" aria-hidden /> : null}
                {repairing ? t("knowledge.repairing") : t("knowledge.repairRun")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

