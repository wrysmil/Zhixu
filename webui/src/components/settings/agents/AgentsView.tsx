import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FolderTree, LayoutGrid, List, Plus, Search, UserPlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { AgentCard } from "@/components/settings/agents/AgentCard";
import { AgentRow } from "@/components/settings/agents/AgentRow";
import { AgentTreeView } from "@/components/settings/agents/AgentTreeView";
import { AgentEditorDialog } from "@/components/settings/agents/AgentEditorDialog";
import { useClient } from "@/providers/ClientProvider";
import {
  deleteAgent,
  listAgents,
  loadAgentCatalog,
  resetAgent,
  saveAgent,
  setAgentVisibility,
} from "@/lib/agents/api";
import { createBlankAgent, slugifyAgentId, uniqueAgentId } from "@/lib/agents/catalog";
import type { AgentCatalog } from "@/lib/agents/catalog";
import type { AgentProfile } from "@/lib/agents/types";
import { isDefaultMainAgent } from "@/lib/agents/types";
import { AGENT_CATEGORY_LABEL_KEY } from "@/lib/agents/i18n";
import { notifySaved } from "@/lib/save-feedback";

const ALL = "__all__";
type ViewMode = "list" | "card" | "tree";

export function AgentsView() {
  const { t } = useTranslation();
  const { client, getToken, modelName } = useClient();
  // modelId 为 null 表示「跟随全局」，这里要的是那个全局值的可读标签。
  const globalModelLabel = modelName ?? "";
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  /** 写操作的错误单独放：读失败和写失败要能区分开显示。 */
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [categoryFilter, setCategoryFilter] = useState<string>(ALL);
  const [query, setQuery] = useState("");
  // 默认卡片视图：卡片一屏能横向扫过图标、描述与能力摘要，列表只能看到名称
  // 和一行描述；「点开看详情」的成本交给编辑弹窗，不再让首屏承担。
  const [view, setView] = useState<ViewMode>("card");

  const [editing, setEditing] = useState<{ agent: AgentProfile | null; isNew: boolean } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<AgentProfile | null>(null);

  const originalsRef = useRef(new Map<string, AgentProfile>());

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    void (async () => {
      try {
        const token = await getToken();
        const [listPayload, catalogPayload] = await Promise.all([
          listAgents(token),
          loadAgentCatalog(token),
        ]);
        if (cancelled) return;
        setAgents(listPayload.agents);
        setCatalog(catalogPayload);
        originalsRef.current = new Map(
          listPayload.agents.map((agent) => [agent.id, agent]),
        );
      } catch (reason: unknown) {
        if (!cancelled) {
          setLoadError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // 只在挂载时取一次数据：getToken 每次渲染都是新引用，进依赖会无限重取。
  }, []);

  const visibleAgents = useMemo(() => agents.filter((agent) => !agent.hidden), [agents]);

  const categoryTabs = useMemo(() => {
    const counts = new Map<string, number>();
    for (const agent of visibleAgents) {
      if (!agent.categoryId) continue;
      counts.set(agent.categoryId, (counts.get(agent.categoryId) ?? 0) + 1);
    }
    return (catalog?.categories ?? [])
      .slice()
      .sort((a, b) => a.order - b.order)
      .map((category) => ({
        id: category.id,
        color: category.color,
        count: counts.get(category.id) ?? 0,
      }));
  }, [visibleAgents, catalog]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return visibleAgents
      .filter((agent) => {
        if (categoryFilter !== ALL && agent.categoryId !== categoryFilter) return false;
        if (!needle) return true;
        return (
          agent.name.toLowerCase().includes(needle) ||
          agent.description.toLowerCase().includes(needle) ||
          agent.id.toLowerCase().includes(needle)
        );
      })
      .sort(
        (a, b) => Number(!isDefaultMainAgent(a)) - Number(!isDefaultMainAgent(b)),
      );
  }, [visibleAgents, categoryFilter, query]);

  const hiddenAgents = useMemo(() => agents.filter((agent) => agent.hidden), [agents]);
  const agentIds = useMemo(() => agents.map((agent) => agent.id), [agents]);

  /** 写操作的统一壳：跑 mutation、成功后并入列表、失败只落到 actionError。 */
  const runWrite = async (work: () => Promise<AgentProfile>) => {
    setBusy(true);
    setActionError(null);
    try {
      const saved = await work();
      setAgents((prev) => {
        const index = prev.findIndex((agent) => agent.id === saved.id);
        if (index === -1) return [...prev, saved];
        const copy = prev.slice();
        copy[index] = saved;
        return copy;
      });
      originalsRef.current.set(saved.id, saved);
      notifySaved();
      return saved;
    } catch (reason: unknown) {
      setActionError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const handleSave = (next: AgentProfile) => {
    void runWrite(async () => {
      const payload = await saveAgent(client, next);
      return payload.agent;
    }).then((saved) => {
      if (!saved) return;
      // 保存后留在编辑器里接着改：基线换成刚落库的那份，dirty 随之归零。
      // 改回 setEditing(null) 会让用户每存一次就被弹回列表，想微调提示词就得重开一遍。
      setEditing((prev) => (prev ? { ...prev, agent: saved, isNew: false } : prev));
    });
  };

  const handleDuplicate = (agent: AgentProfile) => {
    const id = uniqueAgentId(
      `${slugifyAgentId(agent.name)}-copy`,
      new Set(agentIds),
    );
    void runWrite(async () => {
      const payload = await saveAgent(client, {
        ...agent,
        id,
        name: `${agent.name} ${t("settings.agents.duplicateSuffix", "副本")}`,
        type: "custom",
        customized: false,
      });
      return payload.agent;
    });
  };

  const handleToggleHidden = (agent: AgentProfile) => {
    void runWrite(async () => {
      const payload = await setAgentVisibility(client, agent.id, !agent.hidden);
      return payload.agent;
    });
  };

  const handleDelete = () => {
    if (!pendingDelete) return;
    const target = pendingDelete;
    setPendingDelete(null);
    setBusy(true);
    setActionError(null);
    void deleteAgent(client, target.id)
      .then(() => {
        setAgents((prev) => prev.filter((agent) => agent.id !== target.id));
      })
      .catch((reason: unknown) => {
        setActionError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        setBusy(false);
      });
  };

  const handleCreate = () => {
    const blank = createBlankAgent();
    const id = uniqueAgentId(
      slugifyAgentId(blank.name || t("settings.agents.untitled", "未命名")),
      new Set(agentIds),
    );
    setEditing({ agent: { ...blank, id }, isNew: true });
  };

  const handleReset = (draft: AgentProfile) => {
    void runWrite(async () => {
      const payload = await resetAgent(client, draft.id);
      return payload.agent;
    }).then((saved) => {
      if (saved) setEditing(null);
    });
  };

  const filtersActive = categoryFilter !== ALL || query.trim().length > 0;

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[14rem] flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("settings.agents.search", "搜索名称、ID 或描述…")}
            aria-label={t("settings.agents.search", "搜索名称或描述")}
            className="h-9 pl-9 text-[13px]"
          />
        </div>

        <SegmentedControl
          value={view}
          onChange={setView}
          ariaLabel={t("settings.agents.viewMode", "视图模式")}
          options={[
            {
              value: "list",
              label: (
                <span className="inline-flex items-center gap-1">
                  <List className="h-3 w-3" aria-hidden />
                  {t("settings.agents.viewList", "列表")}
                </span>
              ),
            },
            {
              value: "card",
              label: (
                <span className="inline-flex items-center gap-1">
                  <LayoutGrid className="h-3 w-3" aria-hidden />
                  {t("settings.agents.viewCard", "卡片")}
                </span>
              ),
            },
            {
              value: "tree",
              label: (
                <span className="inline-flex items-center gap-1">
                  <FolderTree className="h-3 w-3" aria-hidden />
                  {t("settings.agents.viewTree", "树")}
                </span>
              ),
            },
          ]}
        />

        <Button type="button" size="sm" onClick={handleCreate} className="gap-1.5 text-[13px]">
          <Plus className="h-3.5 w-3.5" aria-hidden />
          {t("settings.agents.create", "新建 Agent")}
        </Button>
      </div>

      {categoryTabs.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <FilterChip active={categoryFilter === ALL} onClick={() => setCategoryFilter(ALL)}>
            {t("settings.agents.categoryAll", "全部")} {visibleAgents.length}
          </FilterChip>
          {categoryTabs.map((category) => (
            <FilterChip
              key={category.id}
              active={categoryFilter === category.id}
              onClick={() => setCategoryFilter(category.id)}
              color={category.color}
            >
              {t(AGENT_CATEGORY_LABEL_KEY[category.id], category.id)} {category.count}
            </FilterChip>
          ))}
        </div>
      ) : null}

      {loadError || actionError ? (
        <div
          role="alert"
          className="rounded-panel border border-rose-300/70 bg-rose-50/70 px-4 py-3 text-[13px] text-rose-900 dark:border-rose-700/40 dark:bg-rose-950/30 dark:text-rose-200"
        >
          {loadError ?? actionError}
        </div>
      ) : null}

      {loading ? (
        <div className="flex h-32 items-center justify-center text-[13px] text-muted-foreground">
          {t("settings.agents.loading", "加载中…")}
        </div>
      ) : !loadError && catalog && filtered.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-panel border border-dashed border-border px-6 py-14 text-center">
          <UserPlus className="h-7 w-7 text-muted-foreground/60" aria-hidden />
          <p className="text-[13px] text-muted-foreground">
            {filtersActive
              ? t("settings.agents.noMatch", "没有匹配的智能体，换个关键词或清掉筛选试试。")
              : t("settings.agents.emptyHint", "还没有智能体。新建一个，把技能、工具和模型按需组合起来。")}
          </p>
          {!filtersActive ? (
            <Button type="button" size="sm" onClick={handleCreate} className="gap-1.5 text-[13px]">
              <Plus className="h-3.5 w-3.5" aria-hidden />
              {t("settings.agents.create", "新建 Agent")}
            </Button>
          ) : null}
        </div>
      ) : null}

      {catalog && filtered.length > 0 ? (
        <>
          {view === "list" ? (
            <div className="space-y-1.5">
              {filtered.map((agent) => (
                <AgentRow
                  key={agent.id}
                  agent={agent}
                  onEdit={() => setEditing({ agent, isNew: false })}
                  onDuplicate={() => handleDuplicate(agent)}
                  onToggleHidden={() => handleToggleHidden(agent)}
                  onDelete={agent.type === "system" ? undefined : () => setPendingDelete(agent)}
                />
              ))}
            </div>
          ) : null}

          {view === "card" ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-3.5">
              {filtered.map((agent) => (
                <AgentCard
                  key={agent.id}
                  agent={agent}
                  agentIds={agentIds}
                  tools={catalog.tools}
                  skills={catalog.skills}
                  models={catalog.models}
                  onEdit={() => setEditing({ agent, isNew: false })}
                  onDuplicate={() => handleDuplicate(agent)}
                  onToggleHidden={() => handleToggleHidden(agent)}
                  onDelete={agent.type === "system" ? undefined : () => setPendingDelete(agent)}
                />
              ))}
            </div>
          ) : null}

          {view === "tree" ? (
            <AgentTreeView
              agents={filtered}
              tools={catalog.tools}
              skills={catalog.skills}
              models={catalog.models}
              globalModelLabel={globalModelLabel}
            />
          ) : null}
        </>
      ) : null}

      {hiddenAgents.length > 0 && catalog && !loadError ? (
        <details className="rounded-panel bg-settings-surface px-4 py-3">
          <summary className="cursor-pointer text-[12px] text-muted-foreground">
            {t("settings.agents.hiddenGroup", "已隐藏的智能体（{{count}}）", {
              count: hiddenAgents.length,
            })}
          </summary>
          <div className="mt-3 space-y-1.5">
            {hiddenAgents.map((agent) => (
              <AgentRow
                key={agent.id}
                agent={agent}
                dimmed
                onEdit={() => setEditing({ agent, isNew: false })}
                onDuplicate={() => handleDuplicate(agent)}
                onToggleHidden={() => handleToggleHidden(agent)}
                onDelete={agent.type === "system" ? undefined : () => setPendingDelete(agent)}
              />
            ))}
          </div>
        </details>
      ) : null}

      {catalog ? (
        <AgentEditorDialog
          open={editing !== null}
          agent={editing?.agent ?? null}
          isNew={editing?.isNew ?? false}

          catalog={catalog}
          globalModelLabel={globalModelLabel}
          saving={busy}
          onOpenChange={(open) => {
            if (!open) setEditing(null);
          }}
          onSave={handleSave}
          onReset={handleReset}
        />
      ) : null}

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("settings.agents.deleteTitle", "删除「{{name}}」？", {
                name: pendingDelete?.name ?? "",
              })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("settings.agents.deleteBody", "删除后不可恢复，历史会话不受影响。")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel", "取消")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-white hover:bg-destructive/90"
            >
              {t("settings.agents.card.delete", "删除")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

function FilterChip({
  active,
  onClick,
  color,
  children,
}: {
  active: boolean;
  onClick: () => void;
  color?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={
        active
          ? "inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1 text-[12px] font-medium text-foreground"
          : "inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-transparent px-2.5 py-1 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
      }
    >
      {color ? (
        <span
          aria-hidden
          className="h-2 w-2 shrink-0 rounded-full"
          style={{ backgroundColor: color }}
        />
      ) : null}
      {children}
    </button>
  );
}
