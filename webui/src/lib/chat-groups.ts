import { deriveTitle } from "@/lib/format";
import type { ChatSummary, SidebarProjectEntry, SidebarSortMode } from "@/lib/types";
import { normalizeWorkspacePath, projectNameFromPath, sameWorkspacePath } from "@/lib/workspace";

export const COLLAPSED_CHATS_VISIBLE_COUNT = 8;
export const COLLAPSED_PROJECT_VISIBLE_COUNT = 6;

export interface SessionGroup {
  id: string;
  label: string;
  sessions: ChatSummary[];
  kind?: "project";
  projectPath?: string;
  projectKey?: string;
  updatedAt?: string | null;
}

export interface ChatGroupLabels {
  pinned: string;
  all: string;
  today: string;
  yesterday: string;
  earlier: string;
  archived: string;
  projects: string;
  fallbackTitle: string;
}

export interface ChatGroupingOptions {
  pinnedKeys: string[];
  archivedKeys: string[];
  titleOverrides: Record<string, string>;
  projectNameOverrides: Record<string, string>;
  pinnedProjectKeys?: string[];
  hiddenProjectKeys?: string[];
  projectEntries?: SidebarProjectEntry[];
  sessionOrder: string[];
  showArchived: boolean;
  sort: SidebarSortMode;
  defaultWorkspacePath?: string | null;
}

export function groupSessions(
  sessions: ChatSummary[],
  labels: ChatGroupLabels,
  options: ChatGroupingOptions,
): SessionGroup[] {
  return groupSessionsByProject(sessions, labels, options);
}

export function limitGroups(
  groups: SessionGroup[],
  limit: number,
  activeKey: string | null,
  collapsedGroups: Record<string, boolean>,
): SessionGroup[] {
  let remaining = Math.max(0, limit);
  let activeVisible = !activeKey;
  const out: SessionGroup[] = [];

  for (const group of groups) {
    if (isCollapsedProject(group, collapsedGroups)) {
      out.push({ ...group, sessions: [] });
      continue;
    }
    const visible = remaining > 0
      ? group.sessions.slice(0, remaining)
      : [];
    remaining -= visible.length;
    if (activeKey && visible.some((session) => session.key === activeKey)) {
      activeVisible = true;
    }
    if (visible.length > 0 || group.kind === "project") {
      out.push({ ...group, sessions: visible });
    }
  }

  if (activeVisible || !activeKey) return out;

  for (const group of groups) {
    if (isCollapsedProject(group, collapsedGroups)) continue;
    const active = group.sessions.find((session) => session.key === activeKey);
    if (!active) continue;
    const existing = out.find((item) => item.id === group.id);
    if (existing) {
      existing.sessions = [...existing.sessions, active];
    } else {
      out.push({ ...group, sessions: [active] });
    }
    return out;
  }

  return out;
}

export function isCollapsedProject(
  group: SessionGroup,
  collapsedGroups: Record<string, boolean>,
): boolean {
  return group.kind === "project" && Boolean(collapsedGroups[group.id]);
}

export function isFoldableChatsGroup(group: SessionGroup): boolean {
  return group.kind === "project"
    || group.id === "workspace:chats"
    || group.id === "date:all";
}

// Project groups keep their full-collapse state under group.id, so list folding
// needs its own key to avoid collapsing the whole project when unfolding rows.
export function groupFoldKey(group: SessionGroup): string {
  return group.kind === "project" ? `${group.id}#fold` : group.id;
}

function foldedVisibleCount(group: SessionGroup): number {
  return group.kind === "project"
    ? COLLAPSED_PROJECT_VISIBLE_COUNT
    : COLLAPSED_CHATS_VISIBLE_COUNT;
}

export function isFoldedChatsGroup(
  group: SessionGroup,
  collapsedGroups: Record<string, boolean>,
): boolean {
  return (
    isFoldableChatsGroup(group)
    && group.sessions.length > foldedVisibleCount(group)
    && collapsedGroups[groupFoldKey(group)] !== false
  );
}

export function visibleSessionsForGroup(
  group: SessionGroup,
  activeKey: string | null,
  collapsedGroups: Record<string, boolean>,
): ChatSummary[] {
  if (!isFoldedChatsGroup(group, collapsedGroups)) {
    return group.sessions;
  }
  const visible = group.sessions.slice(0, foldedVisibleCount(group));
  if (!activeKey || visible.some((session) => session.key === activeKey)) {
    return visible;
  }
  const active = group.sessions.find((session) => session.key === activeKey);
  return active ? [...visible, active] : visible;
}

export function displayTitle(
  session: ChatSummary,
  titleOverrides: Record<string, string>,
  fallbackTitle: string,
): string {
  return (
    titleOverrides[session.key]?.trim()
    || session.title?.trim()
    || deriveTitle(session.preview, fallbackTitle)
  );
}

function groupSessionsByProject(
  sessions: ChatSummary[],
  labels: Pick<ChatGroupLabels, "all">,
  options: ChatGroupingOptions,
): SessionGroup[] {
  const archived = new Set(options.archivedKeys);
  const hiddenProjects = new Set(options.hiddenProjectKeys ?? []);
  const conversations: ChatSummary[] = [];
  const buckets = new Map<string, {
    path?: string;
    label: string;
    sessions: ChatSummary[];
    updatedAt: string | null;
  }>();

  for (const session of sessions) {
    if (archived.has(session.key) && !options.showArchived) {
      continue;
    }
    const scope = session.workspaceScope;
    const path = scope?.project_path || "";
    if (!path || sameWorkspacePath(path, options.defaultWorkspacePath)) {
      conversations.push(session);
      continue;
    }
    const key = normalizeWorkspacePath(path);
    if (hiddenProjects.has(key)) {
      conversations.push(session);
      continue;
    }
    const label = options.projectNameOverrides[key]?.trim()
      || scope?.project_name?.trim()
      || projectNameFromPath(path);
    const bucket = buckets.get(key) ?? {
      path,
      label,
      sessions: [],
      updatedAt: null,
    };
    bucket.sessions.push(session);
    const candidate = session.updatedAt ?? session.createdAt ?? null;
    if (isNewerDate(candidate, bucket.updatedAt)) {
      bucket.updatedAt = candidate;
    }
    buckets.set(key, bucket);
  }

  const pinned = new Set(options.pinnedKeys);
  const groups: SessionGroup[] = Array.from(buckets.entries()).map(([key, bucket]) => ({
    id: `project:${key}`,
    label: bucket.label,
    kind: "project" as const,
    projectPath: bucket.path,
    projectKey: key,
    updatedAt: bucket.updatedAt,
    sessions: sortProjectSessions(
      bucket.sessions,
      options.sort,
      options.titleOverrides,
      options.sessionOrder,
      pinned,
      archived,
    ),
  }));

  // Registered projects stay visible even before they have any session.
  const groupedKeys = new Set(groups.map((group) => group.projectKey ?? ""));
  for (const entry of options.projectEntries ?? []) {
    if (!entry.key || groupedKeys.has(entry.key) || hiddenProjects.has(entry.key)) {
      continue;
    }
    groupedKeys.add(entry.key);
    groups.push({
      id: `project:${entry.key}`,
      label: entry.name.trim()
        || options.projectNameOverrides[entry.key]?.trim()
        || projectNameFromPath(entry.path),
      kind: "project" as const,
      projectPath: entry.path,
      projectKey: entry.key,
      updatedAt: entry.added_at || null,
      sessions: [],
    });
  }

  if (conversations.length) {
    const chatsUpdatedAt = conversations.reduce<string | null>(
      (best, s) => {
        const candidate = s.updatedAt ?? s.createdAt ?? null;
        return isNewerDate(candidate, best) ? candidate : best;
      },
      null,
    );
    groups.push({
      id: "workspace:chats",
      label: labels.all,
      updatedAt: chatsUpdatedAt,
      sessions: sortProjectSessions(
        conversations,
        options.sort,
        options.titleOverrides,
        options.sessionOrder,
        pinned,
        archived,
      ),
    });
  }

  const pinnedProjects = new Set(options.pinnedProjectKeys ?? []);
  groups.sort((a, b) => {
    // Projects form one section above the topic list, matching the section header.
    const kindOrder = Number(a.kind !== "project") - Number(b.kind !== "project");
    if (kindOrder !== 0) return kindOrder;
    const pinOrder = Number(pinnedProjects.has(b.projectKey ?? ""))
      - Number(pinnedProjects.has(a.projectKey ?? ""));
    if (pinOrder !== 0) return pinOrder;
    const timeOrder = dateToTime(b.updatedAt) - dateToTime(a.updatedAt);
    if (timeOrder !== 0) return timeOrder;
    return a.label.localeCompare(b.label, "en", {
      numeric: true,
      sensitivity: "base",
    });
  });

  return groups;
}

function sortProjectSessions(
  sessions: ChatSummary[],
  sort: SidebarSortMode,
  titleOverrides: Record<string, string>,
  sessionOrder: string[],
  pinned: Set<string>,
  archived: Set<string>,
): ChatSummary[] {
  return sortSessions(sessions, sort, titleOverrides, sessionOrder).sort((a, b) => {
    const pinOrder = Number(pinned.has(b.key)) - Number(pinned.has(a.key));
    if (pinOrder !== 0) return pinOrder;
    const archiveOrder = Number(archived.has(a.key)) - Number(archived.has(b.key));
    if (archiveOrder !== 0) return archiveOrder;
    return 0;
  });
}

export function sortSessions(
  sessions: ChatSummary[],
  sort: SidebarSortMode,
  titleOverrides: Record<string, string>,
  sessionOrder: string[],
): ChatSummary[] {
  const copy = [...sessions];
  const order = new Map(sessionOrder.map((key, index) => [key, index]));
  copy.sort((a, b) => {
    if (sort === "manual") {
      const aIndex = order.get(a.key);
      const bIndex = order.get(b.key);
      if (aIndex !== undefined && bIndex !== undefined) return aIndex - bIndex;
      if (aIndex === undefined && bIndex !== undefined) return -1;
      if (aIndex !== undefined && bIndex === undefined) return 1;
      return sessionTime(b, "updatedAt") - sessionTime(a, "updatedAt");
    }
    if (sort === "title_asc") {
      const titleOrder = titleForSort(a, titleOverrides).localeCompare(
        titleForSort(b, titleOverrides),
        "en",
        { numeric: true, sensitivity: "base" },
      );
      if (titleOrder !== 0) return titleOrder;
      return sessionTime(b, "updatedAt") - sessionTime(a, "updatedAt");
    }
    const aTime = sessionTime(a, sort === "created_desc" ? "createdAt" : "updatedAt");
    const bTime = sessionTime(b, sort === "created_desc" ? "createdAt" : "updatedAt");
    return bTime - aTime;
  });
  return copy;
}

function isNewerDate(a: string | null, b: string | null): boolean {
  return dateToTime(a) > dateToTime(b);
}

function dateToTime(value: string | null | undefined): number {
  const ts = Date.parse(value ?? "");
  return Number.isFinite(ts) ? ts : 0;
}

function titleForSort(
  session: ChatSummary,
  titleOverrides: Record<string, string>,
): string {
  return (
    titleOverrides[session.key]?.trim()
    || session.title?.trim()
    || deriveTitle(session.preview, "new chat")
  ).toLocaleLowerCase("en");
}

function sessionTime(session: ChatSummary, field: "createdAt" | "updatedAt"): number {
  const ts = Date.parse(session[field] ?? "");
  return Number.isFinite(ts) ? ts : 0;
}
