import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GraphPanel } from "@/components/workspace/knowledge/GraphPanel";
import i18n from "@/i18n";
import type { KnowledgeGraphEntity, KnowledgeGraphStats } from "@/lib/api";

function fakeApi(
  stats: Partial<KnowledgeGraphStats>,
  entities: KnowledgeGraphEntity[] = [],
) {
  return {
    stats: vi.fn(async () => ({
      enabled: true,
      backend: "neo4j",
      status: "ready",
      reason: "",
      entities: entities.length,
      relations: 0,
      docsReady: 0,
      docsPending: 0,
      docsFailed: 0,
      docsSkipped: 0,
      truncatedDocs: 0,
      lastExtractedAt: null,
      ...stats,
    }) as KnowledgeGraphStats),
    entities: vi.fn(async () => ({ entities, total: entities.length })),
    entity: vi.fn(async () => ({ entity: entities[0], mentions: [], neighbors: [] })),
    subgraph: vi.fn(async () => ({ nodes: [], edges: [] })),
    extract: vi.fn(async () => ({ queued: 1, docs: [] })),
    rebuild: vi.fn(async () => ({ queued: 1 })),
  };
}

function renderPanel(api: ReturnType<typeof fakeApi>, kbId = "kb") {
  return render(
    <GraphPanel kbId={kbId} token="t" client={{} as never} api={api} />,
  );
}

beforeEach(() => {
  void i18n.changeLanguage("en");
});

describe("GraphPanel", () => {
  it("disables build and rebuild when the graph is off", async () => {
    const api = fakeApi({ enabled: false, status: "disabled", reason: "disabled" });
    renderPanel(api);
    expect((await screen.findAllByText(/Graph is off/)).length).toBeGreaterThan(0);
    expect(screen.getByTestId("graph-build")).toBeDisabled();
    expect(screen.getByTestId("graph-rebuild")).toBeDisabled();
  });

  it("renders the entity list", async () => {
    const api = fakeApi({ enabled: true, status: "ready" }, [
      {
        id: "e1",
        name: "web-api",
        type: "service",
        description: "gateway",
        mentionCount: 3,
      },
    ]);
    renderPanel(api);
    expect(await screen.findByText("web-api")).toBeTruthy();
    expect(api.entities).toHaveBeenCalledWith({ kbId: "kb", q: "", type: "" });
  });

  it("asks for confirmation before rebuilding", async () => {
    const api = fakeApi({ enabled: true, status: "ready" });
    renderPanel(api);
    await userEvent.click(await screen.findByTestId("graph-rebuild"));
    expect(api.rebuild).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId("graph-confirm"));
    await waitFor(() =>
      expect(api.rebuild).toHaveBeenCalledWith({
        kbId: "kb",
        purge: false,
        confirm: true,
      }),
    );
  });

  it("renders the degraded state with a retry", async () => {
    const api = fakeApi({
      enabled: true,
      status: "degraded",
      reason: "neo4j_unavailable",
    });
    renderPanel(api);
    expect(
      (await screen.findAllByText(/Backend unavailable/)).length,
    ).toBeGreaterThan(0);
    expect(await screen.findByText(/neo4j_unavailable/)).toBeTruthy();
  });
});
