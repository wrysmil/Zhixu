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
    expect(parseWorkspaceHash("#/settings?section=memory")).toMatchObject({
      view: "settings",
      settingsSection: "memory",
    });
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
  });
});
