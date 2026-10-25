import { describe, expect, it } from "vitest";

import {
  parseWorkspaceHash,
  workspaceRouteHash,
} from "@/workspace/routes";

describe("workspace route parsing", () => {
  it("opens a new assistant conversation for empty and legacy home links", () => {
    for (const hash of ["", "#/", "#/home", "#/unknown"]) {
      expect(parseWorkspaceHash(hash)).toMatchObject({ view: "chat", chatKey: null });
    }
    expect(workspaceRouteHash({ view: "home" })).toBe("#/new");
  });

  it("keeps the existing new chat route", () => {
    expect(parseWorkspaceHash("#/new")).toMatchObject({
      view: "chat",
      chatKey: null,
    });
  });

  it("preserves existing chat keys", () => {
    expect(parseWorkspaceHash("#/chat/websocket%3Aabc")).toMatchObject({
      view: "chat",
      chatKey: "websocket:abc",
    });
  });

  it("preserves temporary chat routes", () => {
    expect(parseWorkspaceHash("#/temporary/demo-id")).toMatchObject({
      view: "chat",
      chatKey: "websocket:demo-id",
      temporary: true,
    });
  });

  it("parses settings sections and capability routes", () => {
    expect(parseWorkspaceHash("#/settings?section=models")).toMatchObject({
      view: "settings",
      settingsSection: "models",
    });
    // 评估分区在侧边栏里可选，路由解析必须认得它，否则刷新后会被丢回概览。
    expect(parseWorkspaceHash("#/settings?section=eval")).toMatchObject({
      view: "settings",
      settingsSection: "eval",
    });
    // Memory and Identity moved out of settings into standalone views.
    expect(parseWorkspaceHash("#/memory")).toMatchObject({ view: "memory" });
    expect(parseWorkspaceHash("#/identity")).toMatchObject({ view: "identity" });
    expect(parseWorkspaceHash("#/agents")).toMatchObject({
      view: "agents",
      settingsSection: "agents",
    });
  });

  it("parses article routes and tolerates invalid encoding", () => {
    expect(parseWorkspaceHash("#/article/demo-1")).toMatchObject({
      view: "article",
      articleId: "demo-1",
    });
    expect(parseWorkspaceHash("#/article/%")).toMatchObject({ view: "chat", chatKey: null });
  });

  it("round trips new workspace routes", () => {
    expect(workspaceRouteHash({ view: "creative" })).toBe("#/creative");
    expect(workspaceRouteHash({ view: "article", articleId: "a/b" }))
      .toBe("#/article/a%2Fb");
    expect(workspaceRouteHash({ view: "memory" })).toBe("#/memory");
    expect(workspaceRouteHash({ view: "identity" })).toBe("#/identity");
  });
});
