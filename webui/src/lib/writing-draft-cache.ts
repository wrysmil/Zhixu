import type { ArticleSnapshotPayload } from "./writing-api";

/**
 * Local recovery draft cache (spec A-07).
 *
 * Keyed by ``{ [workspaceKey]: { [articleId]: draft } }`` inside a single
 * localStorage slot. ``workspaceKey`` here is the front-end project path
 * (``activeWorkspaceScope.project_path``) — deliberately NOT the server-side
 * ``sha256[:16]`` workspace key, which never reaches the browser (contract §2).
 * Every access is wrapped in try/catch: a broken or unavailable storage must
 * degrade to ``null``/``false`` and never throw into the save pipeline.
 */
export interface LocalArticleDraft {
  workspaceKey: string;
  articleId: string;
  baseRevision: number;
  localEditSeq: number;
  snapshot: ArticleSnapshotPayload;
  cachedAt: string;
}

export const DRAFT_STORAGE_KEY = "zhixu.writing.draft.v1";

type DraftStore = Record<string, Record<string, LocalArticleDraft>>;

function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function isDraft(value: unknown): value is LocalArticleDraft {
  if (!value || typeof value !== "object") return false;
  const d = value as Partial<LocalArticleDraft> & { cachedAt?: unknown };
  return (
    typeof d.workspaceKey === "string"
    && typeof d.articleId === "string"
    && typeof d.baseRevision === "number"
    && Number.isFinite(d.baseRevision)
    && typeof d.localEditSeq === "number"
    && Number.isFinite(d.localEditSeq)
    && typeof d.cachedAt === "string"
    && !!d.snapshot
    && typeof d.snapshot === "object"
    && typeof (d.snapshot as ArticleSnapshotPayload).title === "string"
    && typeof (d.snapshot as ArticleSnapshotPayload).body === "string"
    && Array.isArray((d.snapshot as ArticleSnapshotPayload).outline)
    && Array.isArray((d.snapshot as ArticleSnapshotPayload).materials)
    && Array.isArray((d.snapshot as ArticleSnapshotPayload).tags)
  );
}

function readStore(): DraftStore | null {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(DRAFT_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const out: DraftStore = {};
    for (const [workspaceKey, bucket] of Object.entries(parsed as Record<string, unknown>)) {
      if (!bucket || typeof bucket !== "object" || Array.isArray(bucket)) continue;
      const drafts: Record<string, LocalArticleDraft> = {};
      for (const [articleId, draft] of Object.entries(bucket as Record<string, unknown>)) {
        if (isDraft(draft)) drafts[articleId] = draft;
      }
      if (Object.keys(drafts).length > 0) out[workspaceKey] = drafts;
    }
    return out;
  } catch {
    // Corrupted payload: never rewrite the slot here, other article buckets
    // in the same JSON document must survive untouched.
    return null;
  }
}

function writeStore(store: DraftStore): boolean {
  const sink = storage();
  if (!sink) return false;
  try {
    sink.setItem(DRAFT_STORAGE_KEY, JSON.stringify(store));
    return true;
  } catch {
    // Quota exceeded / private mode: caller must surface "恢复保障不可用"
    // instead of failing the save itself.
    return false;
  }
}

export function readDraft(workspaceKey: string, articleId: string): LocalArticleDraft | null {
  const store = readStore();
  return store?.[workspaceKey]?.[articleId] ?? null;
}

export function writeDraft(draft: Omit<LocalArticleDraft, "cachedAt">): boolean {
  const store = readStore() ?? {};
  const next: DraftStore = {
    ...store,
    [draft.workspaceKey]: {
      ...(store[draft.workspaceKey] ?? {}),
      [draft.articleId]: {
        ...draft,
        snapshot: {
          ...draft.snapshot,
          outline: [...draft.snapshot.outline],
          materials: draft.snapshot.materials.map((material) => ({ ...material })),
          tags: [...draft.snapshot.tags],
        },
        cachedAt: new Date().toISOString(),
      },
    },
  };
  return writeStore(next);
}

export function clearDraft(workspaceKey: string, articleId: string): void {
  const store = readStore();
  if (!store?.[workspaceKey]?.[articleId]) return;
  const bucket = { ...store[workspaceKey] };
  delete bucket[articleId];
  const next: DraftStore = { ...store };
  if (Object.keys(bucket).length > 0) next[workspaceKey] = bucket;
  else delete next[workspaceKey];
  writeStore(next);
}

export function listDrafts(workspaceKey: string): LocalArticleDraft[] {
  const store = readStore();
  const bucket = store?.[workspaceKey];
  if (!bucket) return [];
  return Object.values(bucket);
}
