import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Database,
  Loader2,
  Plus,
  RefreshCcw,
  Search,
  Trash2,
  Upload,
  Wrench,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState } from "@/components/workspace/EmptyState";
import { PageHeading } from "@/components/workspace/PageHeading";
import {
  addKnowledgeDoc,
  addKnowledgeDocFromPath,
  createKnowledgeKb,
  deleteKnowledgeDoc,
  deleteKnowledgeKb,
  fetchKnowledgeDocs,
  fetchKnowledgeKbs,
  fetchKnowledgeStats,
  reindexKnowledgeDoc,
  reindexKnowledgeKb,
  repairKnowledge,
  searchKnowledge,
  type KnowledgeDoc,
  type KnowledgeKb,
  type KnowledgeSearchMode,
  type KnowledgeSearchResult,
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
const MAX_UPLOAD_BYTES = 6 * 1024 * 1024;
const POLL_INTERVAL_MS = 2000;
const SEARCH_LIMITS = [5, 8, 12, 20];
const SEARCH_MODES: KnowledgeSearchMode[] = ["hybrid", "semantic", "keyword"];
const DOC_STATUS_KEYS: Record<string, string> = {
  pending: "statusPending",
  processing: "statusProcessing",
  ready: "statusReady",
  failed: "statusFailed",
};

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

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [kbs, setKbs] = useState<KnowledgeKb[]>([]);
  const [activeKbId, setActiveKbId] = useState<string | null>(null);
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

  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<KnowledgeSearchMode>("hybrid");
  const [limit, setLimit] = useState(8);
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<KnowledgeSearchResult | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);

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

  const loadDocs = useCallback(async () => {
    if (!activeKbId) {
      setDocs([]);
      return;
    }
    setDocsLoading(true);
    try {
      const payload = await fetchKnowledgeDocs(token, { kbId: activeKbId, workspaceIds: scope });
      setDocs(payload.documents);
      setDocsError(null);
    } catch (reason: unknown) {
      setDocsError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDocsLoading(false);
    }
  }, [activeKbId, scope, token]);

  useEffect(() => {
    void loadDocs();
  }, [loadDocs]);

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
      setBusy(true);
      setActionError(null);
      try {
        await action();
        if (after) await after();
      } catch (reason: unknown) {
        setActionError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const onCreateKb = useCallback(async () => {
    const name = newName.trim();
    if (!name) return;
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
        },
      );
      return;
    }
    await runAction(
      () => deleteKnowledgeDoc(client, target.id),
      async () => {
        await loadDocs();
        await loadStats();
      },
    );
  }, [client, confirm, loadDocs, loadKbs, loadStats, runAction]);

  const onUpload = useCallback(
    async (file: File) => {
      if (!activeKbId) return;
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
        const base64 = await readFileAsBase64(file);
        await addKnowledgeDoc(client, {
          kbId: activeKbId,
          name: file.name,
          dataUrl: `data:${mime};base64,${base64}`,
          workspaceId: defaultWorkspaceId,
        });
      }, async () => {
        await loadDocs();
        await loadStats();
      });
    },
    [activeKbId, client, defaultWorkspaceId, loadDocs, loadStats, runAction, t],
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
    }, async () => {
      await loadDocs();
      await loadStats();
    });
  }, [activeKbId, client, defaultWorkspaceId, loadDocs, loadStats, runAction, serverPath]);

  const onSearch = useCallback(async () => {
    const text = query.trim();
    if (!text) return;
    setSearching(true);
    setSearchError(null);
    try {
      const result = await searchKnowledge(token, {
        query: text,
        mode,
        limit,
        kbIds: activeKbId ? [activeKbId] : undefined,
        workspaceIds: scope,
      });
      setSearchResult(result);
    } catch (reason: unknown) {
      setSearchResult(null);
      setSearchError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSearching(false);
    }
  }, [activeKbId, limit, mode, query, scope, token]);

  const onRepair = useCallback(async () => {
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
    } catch (reason: unknown) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRepairing(false);
    }
  }, [
    client,
    defaultWorkspaceId,
    loadDocs,
    loadStats,
    repairFts,
    repairPurge,
    repairVectors,
  ]);

  const statusLabel = (doc: KnowledgeDoc): string => {
    const key = DOC_STATUS_KEYS[doc.status];
    return key ? t(`knowledge.${key}`) : doc.status;
  };

  const disabled = enabled === false;
  const visibleStats = stats ?? {
    enabled: true, kbs: 0, documents: 0, chunks: 0, pending: 0, vector: 0,
  };

  return (
    <div className="assistant-page flex h-full flex-col" data-testid="knowledge-page">
      <PageHeading
        kicker={t("knowledge.kicker")}
        title={t("knowledge.title")}
        description={t("knowledge.subtitle")}
        actions={
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setRepairOpen(true)}
              disabled={disabled}
              className="h-8 gap-1.5 rounded-full px-3 text-[12px] font-medium"
            >
              <Wrench className="h-3.5 w-3.5" aria-hidden />
              {t("knowledge.repair")}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => setCreateOpen((open) => !open)}
              disabled={disabled}
              className="h-8 gap-1.5 rounded-full px-3.5 text-[12px] font-semibold"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden />
              {t("knowledge.newKb")}
            </Button>
          </>
        }
      />

      {disabled ? (
        <div
          data-testid="knowledge-disabled-banner"
          role="status"
          className="mt-4 flex items-start gap-2 rounded-control border border-amber-300/70 bg-amber-50/80 px-3 py-2 text-[12px] leading-relaxed text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-300" aria-hidden />
          <span>{t("knowledge.disabledBanner")}</span>
        </div>
      ) : null}

      <section
        data-testid="knowledge-stats"
        aria-label={t("knowledge.stats")}
        className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-5"
      >
        <StatTile testId="knowledge-stat-kbs" label={t("knowledge.statKbs")} value={visibleStats.kbs} />
        <StatTile testId="knowledge-stat-docs" label={t("knowledge.statDocs")} value={visibleStats.documents} />
        <StatTile testId="knowledge-stat-chunks" label={t("knowledge.statChunks")} value={visibleStats.chunks} />
        <StatTile testId="knowledge-stat-pending" label={t("knowledge.statPending")} value={visibleStats.pending} />
        <StatTile
          testId="knowledge-stat-vectors"
          label={t("knowledge.statVectors")}
          value={visibleStats.vector ?? 0}
        />
      </section>

      {kbsError ? (
        <div role="alert" className="mt-4 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-[12px] text-destructive">
          {t("knowledge.loadError")}: {kbsError}
        </div>
      ) : null}

      {actionError ? (
        <div role="alert" className="mt-4 flex items-start gap-2 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-[12px] text-destructive">
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
        <div role="status" className="mt-4 flex items-center gap-2 rounded-control border border-emerald-300/70 bg-emerald-50/80 px-3 py-2 text-[12px] text-emerald-900 dark:border-emerald-700/40 dark:bg-emerald-950/30 dark:text-emerald-100">
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
          {t("knowledge.repairDone")}
        </div>
      ) : null}

      {createOpen && !disabled ? (
        <form
          className="mt-4 grid gap-3 rounded-panel border border-border/60 bg-muted/20 p-4 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            void onCreateKb();
          }}
        >
          <label className="flex flex-col gap-1 text-[12px] font-medium text-foreground">
            {t("knowledge.name")}
            <Input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder={t("knowledge.namePlaceholder")}
              className="h-9 text-[13px]"
            />
          </label>
          <label className="flex flex-col gap-1 text-[12px] font-medium text-foreground">
            {t("knowledge.visibility")}
            <select
              value={newVisibility}
              onChange={(event) => setNewVisibility(event.target.value as KnowledgeVisibility)}
              className="h-9 rounded-control border border-input bg-background px-2 text-[13px]"
            >
              <option value="workspace">{t("knowledge.visibilityWorkspace")}</option>
              <option value="global">{t("knowledge.visibilityGlobal")}</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[12px] font-medium text-foreground sm:col-span-2">
            {t("knowledge.description")}
            <Textarea
              value={newDescription}
              onChange={(event) => setNewDescription(event.target.value)}
              placeholder={t("knowledge.descriptionPlaceholder")}
              rows={2}
              className="text-[13px]"
            />
          </label>
          <div className="flex items-center gap-2 sm:col-span-2">
            <Button
              type="submit"
              size="sm"
              disabled={creating || !newName.trim()}
              className="h-8 rounded-full px-3.5 text-[12px] font-semibold"
            >
              {creating ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
              {t("knowledge.create")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setCreateOpen(false)}
              className="h-8 rounded-full px-3 text-[12px]"
            >
              {t("knowledge.cancel")}
            </Button>
          </div>
        </form>
      ) : null}

      <div className="mt-4 flex min-h-0 flex-1 flex-col gap-4 lg:flex-row">
        <aside data-testid="knowledge-kb-list" className="w-full shrink-0 lg:w-64">
          <p className="px-1 pb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            {t("knowledge.kbTitle")}
          </p>
          {kbsLoading ? (
            <p className="flex items-center gap-2 px-1 py-3 text-[12px] text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              {t("knowledge.loading")}
            </p>
          ) : kbs.length === 0 ? (
            <EmptyState title={t("knowledge.kbEmpty")} description={t("knowledge.kbEmptyHint")} />
          ) : (
            <ul className="flex flex-col gap-1">
              {kbs.map((kb) => {
                const active = kb.id === activeKbId;
                return (
                  <li
                    key={kb.id}
                    data-active={active || undefined}
                    className={cn(
                      "group rounded-control border px-2.5 py-2 transition-colors",
                      active
                        ? "border-primary/40 bg-primary/10"
                        : "border-transparent hover:bg-muted/60",
                    )}
                  >
                    <div className="flex items-start gap-1">
                      <button
                        type="button"
                        aria-pressed={active}
                        onClick={() => setActiveKbId(kb.id)}
                        className="min-w-0 flex-1 text-left"
                      >
                        <span className="block truncate text-[13px] font-semibold text-foreground">
                          {kb.name}
                        </span>
                        <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                          <span className="rounded-full border border-border/70 px-1.5 py-0.5">
                            {kb.visibility === "global"
                              ? t("knowledge.visibilityGlobal")
                              : t("knowledge.visibilityWorkspace")}
                          </span>
                          <span>{t("knowledge.docCount", { count: kb.documentCount })}</span>
                          <span
                            className={cn(
                              "rounded-full px-1.5 py-0.5",
                              kb.isEnabled
                                ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
                                : "bg-muted text-muted-foreground",
                            )}
                          >
                            {kb.isEnabled ? t("knowledge.enabled") : t("knowledge.disabled")}
                          </span>
                        </span>
                      </button>
                      <button
                        type="button"
                        aria-label={t("knowledge.deleteKb")}
                        title={t("knowledge.deleteKb")}
                        disabled={busy}
                        onClick={() => setConfirm({ kind: "kb", id: kb.id, label: kb.name })}
                        className="shrink-0 rounded-control p-1 text-muted-foreground opacity-60 transition-colors hover:text-destructive"
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-4">
          <div className="rounded-panel border border-border/60 bg-background p-4">
            <div className="flex flex-wrap items-center gap-2 pb-3">
              <h2 className="flex-1 text-[13px] font-semibold text-foreground">
                {t("knowledge.documents")}
                {activeKb ? (
                  <span className="ml-2 text-[11px] font-normal text-muted-foreground">
                    {activeKb.name}
                  </span>
                ) : null}
              </h2>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!activeKbId || busy}
                onClick={() => {
                  if (!activeKbId) return;
                  void runAction(
                    () => reindexKnowledgeKb(client, activeKbId),
                    async () => {
                      await loadDocs();
                      await loadStats();
                    },
                  );
                }}
                className="h-7 gap-1.5 rounded-full px-3 text-[11.5px] font-medium"
              >
                <RefreshCcw className="h-3.5 w-3.5" aria-hidden />
                {t("knowledge.reindexKb")}
              </Button>
            </div>

            {!activeKbId ? (
              <p className="py-6 text-center text-[12.5px] text-muted-foreground">
                {t("knowledge.noSelection")}
              </p>
            ) : (
              <>
                <div className="flex flex-wrap items-end gap-2 pb-3">
                  <label className="flex min-w-[180px] flex-1 flex-col gap-1 text-[11px] font-medium text-muted-foreground">
                    {t("knowledge.upload")}
                    <input
                      type="file"
                      aria-label={t("knowledge.upload")}
                      disabled={busy}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        event.target.value = "";
                        if (file) void onUpload(file);
                      }}
                      className="text-[12px]"
                    />
                    <span className="text-[10.5px] font-normal">{t("knowledge.uploadHint")}</span>
                  </label>
                  <div className="flex min-w-[200px] flex-1 flex-col gap-1">
                    <span className="text-[11px] font-medium text-muted-foreground">
                      {t("knowledge.addPath")}
                    </span>
                    <div className="flex gap-1.5">
                      <Input
                        aria-label={t("knowledge.addPath")}
                        value={serverPath}
                        onChange={(event) => setServerPath(event.target.value)}
                        placeholder={t("knowledge.addPathPlaceholder")}
                        className="h-8 font-mono text-[11.5px]"
                      />
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        disabled={busy || !serverPath.trim()}
                        onClick={() => void onAddServerPath()}
                        className="h-8 shrink-0 rounded-full px-3 text-[11.5px]"
                      >
                        <Upload className="h-3.5 w-3.5" aria-hidden />
                        {t("knowledge.add")}
                      </Button>
                    </div>
                  </div>
                </div>

                {docsError ? (
                  <div role="alert" className="mb-2 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-[12px] text-destructive">
                    {t("knowledge.documentsError")}: {docsError}
                  </div>
                ) : null}

                {docsLoading && docs.length === 0 ? (
                  <p className="flex items-center gap-2 py-4 text-[12px] text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                    {t("knowledge.loading")}
                  </p>
                ) : docs.length === 0 ? (
                  <EmptyState
                    title={t("knowledge.documentsEmpty")}
                    description={t("knowledge.documentsEmptyHint")}
                  />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse text-left text-[12px]">
                      <thead>
                        <tr className="text-[10.5px] uppercase tracking-[0.06em] text-muted-foreground">
                          <th className="py-1.5 pr-2 font-semibold">{t("knowledge.columnTitle")}</th>
                          <th className="py-1.5 pr-2 font-semibold">{t("knowledge.columnSource")}</th>
                          <th className="py-1.5 pr-2 font-semibold">{t("knowledge.columnStatus")}</th>
                          <th className="py-1.5 pr-2 font-semibold">{t("knowledge.columnSize")}</th>
                          <th className="py-1.5 pr-2 font-semibold">{t("knowledge.columnChunks")}</th>
                          <th className="py-1.5 font-semibold">{t("knowledge.columnActions")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {docs.map((doc) => (
                          <tr key={doc.id} className="border-t border-border/50 align-top">
                            <td className="py-2 pr-2">
                              <span className="block max-w-[220px] truncate font-medium text-foreground">
                                {doc.title}
                              </span>
                              {doc.error ? (
                                <span className="mt-1 block max-w-[240px] break-words text-[11px] text-destructive">
                                  {t("knowledge.errorLabel", { message: doc.error })}
                                </span>
                              ) : null}
                            </td>
                            <td className="py-2 pr-2">
                              <span className="block max-w-[180px] truncate font-mono text-[11px] text-muted-foreground">
                                {doc.originalName || doc.mime || "—"}
                              </span>
                            </td>
                            <td className="py-2 pr-2">
                              <DocStatusBadge status={doc.status} label={statusLabel(doc)} />
                            </td>
                            <td className="py-2 pr-2 text-muted-foreground">
                              {t("knowledge.sizeValue", { count: doc.charCount })}
                            </td>
                            <td className="py-2 pr-2 tabular-nums text-muted-foreground">
                              {doc.chunkCount}
                            </td>
                            <td className="py-2">
                              <div className="flex items-center gap-1">
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  disabled={busy || isDocActive(doc)}
                                  onClick={() => {
                                    void runAction(
                                      () => reindexKnowledgeDoc(client, doc.id),
                                      async () => {
                                        await loadDocs();
                                        await loadStats();
                                      },
                                    );
                                  }}
                                  className="h-7 rounded-full px-2.5 text-[11px]"
                                >
                                  <RefreshCcw className="h-3 w-3" aria-hidden />
                                  {t("knowledge.reindex")}
                                </Button>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  aria-label={t("knowledge.deleteDoc")}
                                  title={t("knowledge.deleteDoc")}
                                  disabled={busy}
                                  onClick={() => setConfirm({ kind: "doc", id: doc.id, label: doc.title })}
                                  className="h-7 rounded-full px-2 text-destructive"
                                >
                                  <Trash2 className="h-3.5 w-3.5" aria-hidden />
                                </Button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </div>

          <div className="rounded-panel border border-border/60 bg-background p-4">
            <h2 className="pb-3 text-[13px] font-semibold text-foreground">
              {t("knowledge.searchTitle")}
            </h2>
            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void onSearch();
              }}
            >
              <label className="flex min-w-[200px] flex-1 flex-col gap-1 text-[11px] font-medium text-muted-foreground">
                {t("knowledge.query")}
                <Input
                  aria-label={t("knowledge.query")}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={t("knowledge.queryPlaceholder")}
                  className="h-8 text-[12.5px]"
                />
              </label>
              <label className="flex flex-col gap-1 text-[11px] font-medium text-muted-foreground">
                {t("knowledge.mode")}
                <select
                  aria-label={t("knowledge.mode")}
                  value={mode}
                  onChange={(event) => setMode(event.target.value as KnowledgeSearchMode)}
                  className="h-8 rounded-control border border-input bg-background px-2 text-[12px]"
                >
                  {SEARCH_MODES.map((value) => (
                    <option key={value} value={value}>
                      {t(`knowledge.mode${value.charAt(0).toUpperCase()}${value.slice(1)}`)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-[11px] font-medium text-muted-foreground">
                {t("knowledge.limit")}
                <select
                  aria-label={t("knowledge.limit")}
                  value={String(limit)}
                  onChange={(event) => setLimit(Number(event.target.value))}
                  className="h-8 rounded-control border border-input bg-background px-2 text-[12px]"
                >
                  {SEARCH_LIMITS.map((value) => (
                    <option key={value} value={String(value)}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <Button
                type="submit"
                size="sm"
                disabled={searching || !query.trim()}
                className="h-8 gap-1.5 rounded-full px-3.5 text-[12px] font-semibold"
              >
                {searching ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <Search className="h-3.5 w-3.5" aria-hidden />
                )}
                {t("knowledge.search")}
              </Button>
            </form>

            {searchError ? (
              <div role="alert" className="mt-3 rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-[12px] text-destructive">
                {t("knowledge.searchError")}: {searchError}
              </div>
            ) : searching ? (
              <p className="mt-3 flex items-center gap-2 text-[12px] text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                {t("knowledge.searching")}
              </p>
            ) : !searchResult ? (
              <p className="mt-3 text-[12px] text-muted-foreground">{t("knowledge.searchIdle")}</p>
            ) : (
              <SearchResults
                result={searchResult}
                emptyLabel={
                  searchResult.reason
                    ? t("knowledge.searchFiltered", { reason: searchResult.reason })
                    : t("knowledge.searchEmpty")
                }
              />
            )}
          </div>
        </section>
      </div>

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
            <p className="mt-2 text-[12.5px] leading-relaxed text-muted-foreground">
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
                className="h-8 rounded-full px-3 text-[12px]"
              >
                {t("knowledge.cancel")}
              </Button>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                onClick={() => void onConfirmDelete()}
                className="h-8 rounded-full px-3.5 text-[12px] font-semibold"
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
            <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">
              {t("knowledge.repairHint")}
            </p>
            <div className="mt-3 flex flex-col gap-2 text-[12.5px]">
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
              <p className="mt-2 flex items-start gap-1.5 rounded-control border border-destructive/30 bg-destructive/5 px-2.5 py-2 text-[11.5px] text-destructive">
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
                className="h-8 rounded-full px-3 text-[12px]"
              >
                {t("knowledge.cancel")}
              </Button>
              <Button
                type="button"
                variant={repairPurge ? "destructive" : "default"}
                size="sm"
                onClick={() => void onRepair()}
                disabled={repairing || (!repairFts && !repairVectors && !repairPurge)}
                className="h-8 gap-1.5 rounded-full px-3.5 text-[12px] font-semibold"
              >
                {repairing ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                {repairing ? t("knowledge.repairing") : t("knowledge.repairRun")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function StatTile({
  testId,
  label,
  value,
}: {
  testId: string;
  label: string;
  value: number;
}) {
  return (
    <div
      data-testid={testId}
      className="rounded-control border border-border/60 bg-muted/20 px-2.5 py-2"
    >
      <p className="text-[10.5px] uppercase tracking-[0.06em] text-muted-foreground">{label}</p>
      <p className="mt-0.5 flex items-center gap-1 text-[15px] font-semibold text-foreground">
        <Database className="h-3 w-3 text-muted-foreground" aria-hidden />
        {value}
      </p>
    </div>
  );
}

function DocStatusBadge({ status, label }: { status: string; label: string }) {
  const tone =
    status === "ready"
      ? "border-emerald-300/70 bg-emerald-50 text-emerald-900 dark:border-emerald-700/40 dark:bg-emerald-950/30 dark:text-emerald-200"
      : status === "failed"
        ? "border-destructive/40 bg-destructive/5 text-destructive"
        : "border-amber-300/70 bg-amber-50 text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100";
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-medium", tone)}>
      {status === "pending" || status === "processing" ? (
        <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden />
      ) : null}
      {label}
    </span>
  );
}

function SearchResults({
  result,
  emptyLabel,
}: {
  result: KnowledgeSearchResult;
  emptyLabel: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="mt-3 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5 text-[10.5px]">
        <span className="rounded-full border border-border/70 px-2 py-0.5 font-medium text-foreground">
          {t("knowledge.modeUsed", { mode: result.modeUsed })}
        </span>
        {result.routes.length > 0 ? (
          <span className="rounded-full border border-border/70 px-2 py-0.5 text-muted-foreground">
            {t("knowledge.routes", { routes: result.routes.join(", ") })}
          </span>
        ) : null}
        {result.degraded.length > 0 ? (
          <span className="rounded-full border border-amber-300/70 bg-amber-50 px-2 py-0.5 text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100">
            {t("knowledge.degraded", { reasons: result.degraded.join(", ") })}
          </span>
        ) : null}
        <span className="text-muted-foreground">
          {t("knowledge.hitCount", { count: result.hits.length })}
        </span>
      </div>
      {result.hits.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">{emptyLabel}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {result.hits.map((hit) => (
            <li
              key={hit.chunkId}
              className="rounded-control border border-border/60 bg-muted/10 px-3 py-2"
            >
              <div className="flex flex-wrap items-center gap-2 text-[10.5px] text-muted-foreground">
                <span className="rounded-full bg-primary/10 px-2 py-0.5 font-semibold text-primary">
                  {hit.score.toFixed(3)}
                </span>
                {hit.heading ? (
                  <span className="max-w-[280px] truncate text-foreground">{hit.heading}</span>
                ) : null}
                {typeof hit.page === "number" ? (
                  <span>{t("knowledge.pageLabel", { page: hit.page })}</span>
                ) : null}
                <span className="font-mono">{hit.chunkId}</span>
                <span className="font-mono">{hit.docId}</span>
              </div>
              <p className="mt-1 whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-foreground">
                {hit.content}
              </p>
              {hit.locator ? (
                <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">{hit.locator}</p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
