import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Plus, Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import { DeleteConfirm } from "@/components/DeleteConfirm";
import { Button } from "@/components/ui/button";
import { formControlFocusClassName } from "@/components/ui/form-control";
import { Input } from "@/components/ui/input";
import { deleteMemory, listMemories, searchMemories } from "@/lib/api";
import type { MemoryPayload, MemoryPriority, MemoryType } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";
import { MemoryEditDialog } from "./MemoryEditDialog";

const MEMORY_TYPES: MemoryType[] = ["fact", "preference", "skill", "error", "rule", "experience"];
const MEMORY_PRIORITIES: MemoryPriority[] = ["short_term", "long_term"];
const ORDER_OPTIONS = [
  { value: "created", labelKey: "settings.memory.orderCreated", defaultLabel: "Recently updated" },
  { value: "importance", labelKey: "settings.memory.orderImportance", defaultLabel: "Importance" },
] as const;

const SELECT_CLASS = cn(
  "h-8 rounded-control border border-input bg-background px-2 text-[12.5px] text-foreground transition-colors",
  formControlFocusClassName,
);

export function MemoryListView() {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { client, token } = useClient();

  const [items, setItems] = useState<MemoryPayload[]>([]);
  const [type, setType] = useState<MemoryType | "">("");
  const [priority, setPriority] = useState<MemoryPriority | "">("");
  const [order, setOrder] = useState<"created" | "importance">("created");
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState<MemoryPayload | "new" | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<MemoryPayload | null>(null);
  const [deleting, setDeleting] = useState(false);

  const reload = useCallback(async () => {
    try {
      const result = await listMemories(token, { type: type || undefined, order });
      setItems(result.items);
      setLoadError(null);
    } catch (reason) {
      setLoadError((reason as Error).message);
    }
  }, [token, type, order]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) return;
    setSearching(true);
    const handle = setTimeout(async () => {
      try {
        const result = await searchMemories(token, trimmed);
        setItems(result.items);
        setLoadError(null);
      } catch (reason) {
        setLoadError((reason as Error).message);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(handle);
  }, [query, token]);

  const visible = useMemo(
    () => (priority ? items.filter((memory) => memory.priority === priority) : items),
    [items, priority],
  );

  const onSaved = useCallback(() => {
    setEditing(null);
    void reload();
  }, [reload]);

  const confirmDelete = useCallback(async () => {
    if (!deleteCandidate) return;
    setDeleting(true);
    try {
      await deleteMemory(client, deleteCandidate.id);
      setDeleteCandidate(null);
      await reload();
    } finally {
      setDeleting(false);
    }
  }, [client, deleteCandidate, reload]);

  const trimmedQuery = query.trim();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={tx("settings.memory.searchPlaceholder", "Search memories...")}
            className="pl-9"
            aria-label={tx("settings.memory.searchPlaceholder", "Search memories...")}
          />
        </div>
        <Button onClick={() => setEditing("new")} className="shrink-0">
          <Plus className="mr-1.5 h-4 w-4" aria-hidden />
          {tx("settings.memory.actions.newMemory", "New memory")}
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px] text-muted-foreground">
        <label className="flex items-center gap-2">
          {tx("settings.memory.filterType", "Type")}
          <select
            value={type}
            onChange={(event) => setType(event.target.value as MemoryType | "")}
            className={SELECT_CLASS}
          >
            <option value="">{tx("settings.memory.filterAll", "All")}</option>
            {MEMORY_TYPES.map((value) => (
              <option key={value} value={value}>
                {tx(`settings.memory.type.${value}`, value)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          {tx("settings.memory.filterPriority", "Priority")}
          <select
            value={priority}
            onChange={(event) => setPriority(event.target.value as MemoryPriority | "")}
            className={SELECT_CLASS}
          >
            <option value="">{tx("settings.memory.filterAll", "All")}</option>
            {MEMORY_PRIORITIES.map((value) => (
              <option key={value} value={value}>
                {tx(`settings.memory.priority.${value}`, value)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          {tx("settings.memory.filterOrder", "Sort")}
          <select
            value={order}
            onChange={(event) => setOrder(event.target.value as "created" | "importance")}
            className={SELECT_CLASS}
          >
            {ORDER_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {tx(option.labelKey, option.defaultLabel)}
              </option>
            ))}
          </select>
        </label>
        <span className="ml-auto inline-flex items-center gap-1.5">
          {searching && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
          {t("settings.memory.resultCount", {
            defaultValue: "{{count}} results",
            count: visible.length,
          })}
        </span>
      </div>

      {loadError ? (
        <div className="rounded-panel bg-settings-surface px-4 py-12 text-center text-[13px] text-destructive">
          {loadError}
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-panel bg-settings-surface px-4 py-12 text-center text-[13px] text-muted-foreground">
          {trimmedQuery
            ? tx("settings.memory.noMatches", "No matching memories")
            : tx("settings.memory.empty", "No memories yet. Create your first one above.")}
        </div>
      ) : (
        <ul className="divide-y divide-border/60 overflow-hidden rounded-xl border border-border/65 bg-card/65">
          {visible.map((memory) => (
            <MemoryCard
              key={memory.id}
              memory={memory}
              onEdit={() => setEditing(memory)}
              onDelete={() => setDeleteCandidate(memory)}
            />
          ))}
        </ul>
      )}

      {editing && (
        <MemoryEditDialog
          memory={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={onSaved}
        />
      )}

      <DeleteConfirm
        open={deleteCandidate !== null}
        title={tx("settings.memory.deleteTitle", "Delete this memory?")}
        onCancel={() => setDeleteCandidate(null)}
        onConfirm={() => void confirmDelete()}
      />
      {deleting && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-background/60">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
        </div>
      )}
    </div>
  );
}

function MemoryCard({
  memory,
  onEdit,
  onDelete,
}: {
  memory: MemoryPayload;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  return (
    <li className="px-4 py-4 sm:px-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="break-words text-[13px] font-medium leading-6 text-foreground">
            {memory.content}
          </div>
          <div className="mt-0.5 text-[12px] leading-5 text-muted-foreground">
            {t("settings.memory.metaLine", {
              defaultValue: "{{type}} · {{priority}} · {{importance}}",
              type: tx(`settings.memory.type.${memory.type}`, memory.type),
              priority: tx(`settings.memory.priority.${memory.priority}`, memory.priority),
              importance: memory.importance_score.toFixed(2),
            })}
            {memory.tags.length > 0 && ` · ${memory.tags.join(", ")}`}
            {memory.superseded_by
              ? ` · ${tx("settings.memory.superseded", "superseded")}`
              : ""}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <button
            type="button"
            onClick={onEdit}
            className="rounded-sm text-[12.5px] text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {tx("settings.memory.edit", "Edit")}
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="rounded-sm text-[12.5px] text-destructive underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {tx("settings.memory.delete", "Delete")}
          </button>
        </div>
      </div>
    </li>
  );
}
