import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DRAFT_STORAGE_KEY,
  clearDraft,
  listDrafts,
  readDraft,
  writeDraft,
} from "@/lib/writing-draft-cache";
import type { ArticleSnapshotPayload } from "@/lib/writing-api";

function snapshot(body: string): ArticleSnapshotPayload {
  return {
    title: "T",
    writing_type: "",
    status: "draft",
    summary: "",
    brief: {},
    body,
    outline: [],
    materials: [],
    tags: [],
  };
}

function draft(body = "hello") {
  return {
    workspaceKey: "D:/proj/a",
    articleId: "a1",
    baseRevision: 3,
    localEditSeq: 5,
    snapshot: snapshot(body),
  } satisfies Parameters<typeof writeDraft>[0];
}

describe("writing-draft-cache", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns null when no draft was stored", () => {
    expect(readDraft("D:/proj/a", "a1")).toBeNull();
  });

  it("round-trips a draft through writeDraft/readDraft", () => {
    expect(writeDraft(draft())).toBe(true);
    const stored = readDraft("D:/proj/a", "a1");
    expect(stored).not.toBeNull();
    expect(stored!.articleId).toBe("a1");
    expect(stored!.baseRevision).toBe(3);
    expect(stored!.localEditSeq).toBe(5);
    expect(stored!.snapshot.body).toBe("hello");
    expect(typeof stored!.cachedAt).toBe("string");
    expect(Number.isNaN(Date.parse(stored!.cachedAt))).toBe(false);
  });

  it("keeps workspaces isolated under the same article id", () => {
    writeDraft(draft());
    writeDraft({ ...draft("other"), workspaceKey: "D:/proj/b" });
    expect(readDraft("D:/proj/a", "a1")!.snapshot.body).toBe("hello");
    expect(readDraft("D:/proj/b", "a1")!.snapshot.body).toBe("other");
    expect(readDraft("D:/proj/c", "a1")).toBeNull();
  });

  it("lists only the requested workspace's drafts", () => {
    writeDraft(draft());
    writeDraft({ ...draft("second"), articleId: "a2" });
    writeDraft({ ...draft("elsewhere"), workspaceKey: "D:/proj/b" });
    const drafts = listDrafts("D:/proj/a");
    expect(drafts.map((d) => d.articleId).sort()).toEqual(["a1", "a2"]);
  });

  it("clears a single draft without touching siblings", () => {
    writeDraft(draft());
    writeDraft({ ...draft("second"), articleId: "a2" });
    clearDraft("D:/proj/a", "a1");
    expect(readDraft("D:/proj/a", "a1")).toBeNull();
    expect(readDraft("D:/proj/a", "a2")!.snapshot.body).toBe("second");
  });

  it("survives corrupted JSON without wiping other storage entries", () => {
    window.localStorage.setItem(DRAFT_STORAGE_KEY, "{not-json");
    window.localStorage.setItem("unrelated.key", "keep-me");
    expect(readDraft("D:/proj/a", "a1")).toBeNull();
    expect(listDrafts("D:/proj/a")).toEqual([]);
    expect(window.localStorage.getItem("unrelated.key")).toBe("keep-me");
    expect(window.localStorage.getItem(DRAFT_STORAGE_KEY)).toBe("{not-json");
  });

  it("rejects structurally invalid stored drafts", () => {
    window.localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({
      "D:/proj/a": { a1: { workspaceKey: "D:/proj/a", articleId: "a1" } },
    }));
    expect(readDraft("D:/proj/a", "a1")).toBeNull();
  });

  it("returns false when setItem throws (quota or private mode)", () => {
    const setItem = vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(writeDraft(draft())).toBe(false);
    setItem.mockRestore();
  });

  it("keeps the highest localEditSeq semantics for the caller", () => {
    writeDraft(draft("v1"));
    writeDraft({ ...draft("v2"), localEditSeq: 6 });
    expect(readDraft("D:/proj/a", "a1")!.snapshot.body).toBe("v2");
    expect(readDraft("D:/proj/a", "a1")!.localEditSeq).toBe(6);
  });
});
