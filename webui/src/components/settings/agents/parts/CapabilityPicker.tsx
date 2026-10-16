import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, ChevronRight, Lock, Minus, Search, X } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Badge, ModeSelector } from "@/components/settings/agents/shared";
import type { AgentSelection, SelectionMode, ToolRisk } from "@/lib/agents/types";
import { resolveSelection } from "@/lib/agents/types";
import { TOOL_RISK_LABEL_KEY } from "@/lib/agents/i18n";
import { cn } from "@/lib/utils";

export interface CapabilityItem {
  id: string;
  label: string;
  description: string;
  group: string;
  risk?: ToolRisk;
  locked?: boolean;
  /** Cannot be picked right now — e.g. would close a sub-agent cycle. */
  blocked?: boolean;
  blockedReason?: string;
  chips?: string[];
}

const RISK_TONE = { low: "ok", medium: "warn", high: "warn" } as const;

function Checkbox({
  checked,
  indeterminate,
  disabled,
}: {
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-[5px] border",
        checked || indeterminate
          ? "border-emerald-700 bg-emerald-700 text-white dark:border-emerald-500 dark:bg-emerald-500 dark:text-slate-950"
          : "border-border bg-background",
        disabled && "opacity-45",
      )}
    >
      {indeterminate ? (
        <Minus className="h-3 w-3" aria-hidden />
      ) : checked ? (
        <Check className="h-3 w-3" aria-hidden />
      ) : null}
    </span>
  );
}

export function CapabilityPicker({
  items,
  selection,
  onChange,
  modeLabel,
  groupLabelFor,
  lockedNote,
}: {
  items: CapabilityItem[];
  selection: AgentSelection;
  onChange: (next: AgentSelection) => void;
  modeLabel: string;
  groupLabelFor: (groupId: string) => string;
  lockedNote?: string;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  // 默认全折叠：工具几十个、技能上百个，铺开的话真正的选择项被挤到看不见。
  // 记的是「用户点开过哪些」，所以搜索时强制展开——搜了就是想看结果。
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const allIds = useMemo(() => items.filter((item) => !item.blocked).map((item) => item.id), [items]);
  const lockedIds = useMemo(
    () => items.filter((item) => item.locked && !item.blocked).map((item) => item.id),
    [items],
  );
  const enabled = useMemo(
    () => resolveSelection(selection, allIds, lockedIds),
    [selection, allIds, lockedIds],
  );

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return items;
    return items.filter(
      (item) =>
        item.label.toLowerCase().includes(needle) ||
        item.description.toLowerCase().includes(needle) ||
        item.id.toLowerCase().includes(needle) ||
        (item.chips ?? []).some((chip) => chip.toLowerCase().includes(needle)),
    );
  }, [items, query]);

  const groups = useMemo(() => {
    const map = new Map<string, { id: string; label: string; items: CapabilityItem[] }>();
    for (const item of visible) {
      const bucket = map.get(item.group);
      if (bucket) bucket.items.push(item);
      else map.set(item.group, { id: item.group, label: groupLabelFor(item.group), items: [item] });
    }
    return [...map.values()].map((group) => ({
      ...group,
      items: [...group.items].sort((a, b) => Number(enabled.has(b.id)) - Number(enabled.has(a.id))),
    }));
  }, [visible, groupLabelFor, enabled]);

  const readOnly = selection.mode === "all";

  const toggleItem = (id: string) => {
    const item = items.find((entry) => entry.id === id);
    if (readOnly || !item || item.locked || item.blocked) return;
    const next = new Set(selection.entries);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange({ mode: selection.mode, entries: [...next] });
  };

  const setGroup = (groupItems: CapabilityItem[], selected: boolean) => {
    if (readOnly) return;
    const next = new Set(selection.entries);
    for (const item of groupItems) {
      if (item.locked || item.blocked) continue;
      const shouldStore = selection.mode === "exclude" ? !selected : selected;
      if (shouldStore) next.add(item.id);
      else next.delete(item.id);
    }
    onChange({ mode: selection.mode, entries: [...next] });
  };

  const toggleGroupCollapsed = (groupId: string) => {
    const next = new Set(expandedGroups);
    if (next.has(groupId)) next.delete(groupId);
    else next.add(groupId);
    setExpandedGroups(next);
  };

  return (
    <div className="min-w-0 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <ModeSelector
          value={selection.mode}
          onChange={(mode: SelectionMode) => onChange({ mode, entries: [] })}
          ariaLabel={modeLabel}
        />
        <span className="text-[12px] text-muted-foreground">
          {t("settings.agents.capability.enabledCount", "已启用", { count: enabled.size })}
          {" / "}
          {allIds.length}
        </span>
        <div className="relative ml-auto w-full sm:w-44">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={modeLabel}
            aria-label={modeLabel}
            className="h-8 pl-8 text-[13px]"
          />
        </div>
      </div>

      {readOnly ? (
        <p className="rounded-control bg-muted/50 px-3 py-2 text-[12px] text-muted-foreground">
          {t(
            "settings.agents.capability.allModeHint",
            "当前为「全部」，下方仅供查看。切换到「仅所选」即可逐项勾选。",
          )}
        </p>
      ) : (
        <p className="text-[12px] leading-5 text-muted-foreground">
          {selection.mode === "exclude"
            ? t("settings.agents.workspace.selectionExcludeHint", "Checked items are enabled; unchecked items are excluded. Changing the selection mode clears its entries.")
            : t("settings.agents.workspace.selectionIncludeHint", "Only checked items are enabled. Changing the selection mode clears its entries.")}
        </p>
      )}

      <div className="overflow-hidden rounded-control border border-border">
        {groups.length === 0 ? (
          <p className="px-3 py-8 text-center text-[12px] text-muted-foreground">
            {t("settings.agents.capability.empty", "没有匹配项")}
          </p>
        ) : (
          groups.map((group) => {
            const selectable = group.items.filter((item) => !item.locked && !item.blocked);
            const selectedCount = group.items.filter((item) => enabled.has(item.id)).length;
            const availableCount = group.items.filter((item) => !item.blocked).length;
            const allSelected = selectable.length > 0 && selectable.every((item) => enabled.has(item.id));
            const isCollapsed = !query && !expandedGroups.has(group.id);

            return (
              <div key={group.id} className="border-b border-border/45 last:border-b-0">
                <div className="flex items-center gap-2 bg-muted/35 px-3 py-2">
                  <button
                    type="button"
                    aria-expanded={!isCollapsed}
                    onClick={() => toggleGroupCollapsed(group.id)}
                    className="flex min-w-0 flex-1 items-center gap-1.5 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700/60"
                  >
                    <ChevronRight
                      className={cn(
                        "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-150 ease-[cubic-bezier(.2,.8,.2,1)] motion-reduce:transition-none",
                        !isCollapsed && "rotate-90",
                      )}
                      aria-hidden
                    />
                    <span className="truncate text-[12px] font-semibold text-foreground/85">
                      {group.label}
                    </span>
                    <span className="shrink-0 text-[11px] text-muted-foreground">
                      {selectedCount}/{availableCount}
                    </span>
                  </button>
                  {!readOnly && selectable.length > 0 ? (
                    <button
                      type="button"
                      onClick={() => setGroup(selectable, !allSelected)}
                      className="shrink-0 rounded px-2 py-1 text-[11px] text-emerald-800 transition-colors duration-150 ease-[cubic-bezier(.2,.8,.2,1)] hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700/60 motion-reduce:transition-none dark:text-emerald-300"
                    >
                      {allSelected
                        ? t("settings.agents.capability.clearGroup", "全不选")
                        : t("settings.agents.capability.selectGroup", "全选")}
                    </button>
                  ) : null}
                </div>

                {!isCollapsed ? (
                  <ul>
                    {group.items.map((item) => {
                      const checked = enabled.has(item.id);
                      const interactive = !readOnly && !item.locked && !item.blocked;
                      return (
                        <li key={item.id}>
                          <button
                            type="button"
                            role="checkbox"
                            aria-checked={checked}
                            disabled={!interactive}
                            onClick={() => interactive && toggleItem(item.id)}
                            title={item.blocked ? item.blockedReason : undefined}
                            className={cn(
                              "flex w-full min-w-0 items-start gap-2.5 px-3 py-3 text-left transition-colors duration-150 ease-[cubic-bezier(.2,.8,.2,1)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-700/60 motion-reduce:transition-none",
                              interactive ? "hover:bg-muted/50" : "cursor-default",
                              item.blocked && "opacity-50",
                            )}
                          >
                            <span className="pt-px">
                              <Checkbox
                                checked={checked}
                                disabled={!interactive}
                              />
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="flex flex-wrap items-center gap-1.5">
                                <span className="truncate text-[13px] font-medium text-foreground/90">
                                  {item.label}
                                </span>
                                {item.locked ? (
                                  <Badge tone="muted">
                                    <Lock className="h-2.5 w-2.5" aria-hidden />
                                    {t("settings.agents.capability.locked", "框架依赖")}
                                  </Badge>
                                ) : null}
                                {item.blocked ? (
                                  <Badge tone="warn">
                                    <X className="h-2.5 w-2.5" aria-hidden />
                                    {item.blockedReason}
                                  </Badge>
                                ) : null}
                                {item.risk && !item.locked ? (
                                  <Badge tone={RISK_TONE[item.risk]}>
                                    {t(TOOL_RISK_LABEL_KEY[item.risk], item.risk)}
                                  </Badge>
                                ) : null}
                                {(item.chips ?? []).map((chip) => (
                                  <Badge key={chip} tone="neutral">
                                    {chip}
                                  </Badge>
                                ))}
                              </span>
                              <span className="mt-1 block break-words text-[12px] leading-5 text-muted-foreground">
                                {item.description}
                              </span>
                              <code className="mt-1 block break-all text-[10px] text-muted-foreground/70">{item.id}</code>
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      {lockedNote ? (
        <p className="text-[11px] text-muted-foreground">{lockedNote}</p>
      ) : null}
    </div>
  );
}
