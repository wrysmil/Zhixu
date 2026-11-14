import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GraphPanel } from "@/components/workspace/knowledge/GraphPanel";
import i18n from "@/i18n";
import type { KnowledgeGraphEdge, KnowledgeGraphNode, KnowledgeGraphEntity, KnowledgeGraphEntityDetail, KnowledgeGraphStats } from "@/lib/api";

const ENTITIES = ["Alpha", "Beta"].map((name, index) => ({
  id: `e${index}`, name, type: "concept", description: `${name} description`, mentionCount: 1,
}));
function detailFor(index: number): KnowledgeGraphEntityDetail {
  return { entity: ENTITIES[index], neighbors: [], mentions: [{
    docId: "doc", docTitle: `${ENTITIES[index].name} source`, chunkId: "chunk", chunkIndex: 0, heading: "",
  }] };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(r => { resolve = r; });
  return { promise, resolve };
}

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
    entity: vi.fn<(id: string) => Promise<KnowledgeGraphEntityDetail>>(async () => ({ entity: entities[0], mentions: [], neighbors: [] })),
    subgraph: vi.fn(async (): Promise<{ nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[] }> => ({ nodes: [], edges: [] })),
    extract: vi.fn(async () => ({ queued: 1, docs: [] })),
    rebuild: vi.fn(async () => ({ queued: 1 })),
  };
}

function renderPanel(api: ReturnType<typeof fakeApi>, kbId = "kb") {
  return render(
    <GraphPanel kbId={kbId} token="t" client={{} as never} api={api} />,
  );
}

/** 实体胶囊：名称 + 提及次数。 */
async function entityPill(index: number): Promise<HTMLElement> {
  const pills = await screen.findAllByTestId("graph-entity-card");
  return pills[index];
}

beforeEach(() => {
  void i18n.changeLanguage("en");
});

describe("GraphPanel", () => {
  it("re-centres the graph on the node you select", async () => {
    const api = fakeApi({}, ENTITIES);
    api.entity.mockImplementation(async id => detailFor(id === "e0" ? 0 : 1));
    api.subgraph.mockResolvedValue({ nodes: ENTITIES, edges: [
      { source: "e0", target: "e1", relation: "uses", weight: 1 },
    ] });
    renderPanel(api);
    const beta = await screen.findByRole("button", { name: "Beta · concept" });
    beta.focus();
    await userEvent.keyboard("{Enter}");
    await screen.findByRole("button", { name: /Beta source/ });
    expect(beta).toHaveAttribute("aria-pressed", "true");
    // 选中节点把图重新以它为中心（默认 2 跳）。
    await waitFor(() => expect(api.subgraph).toHaveBeenLastCalledWith({ entityId: "e1", hops: 2, limit: 50 }));
    const subgraphCalls = api.subgraph.mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.graphExpand") }));
    await waitFor(() => expect(api.subgraph).toHaveBeenCalledTimes(subgraphCalls + 1));
    expect(api.subgraph).toHaveBeenLastCalledWith({ entityId: "e1", hops: 2, limit: 50 });
  });

  it("returns focus to the entity trigger after closing the detail", async () => {
    const api = fakeApi({}, ENTITIES);
    api.entity.mockImplementation(async id => detailFor(id === "e0" ? 0 : 1));
    api.subgraph.mockResolvedValue({ nodes: ENTITIES, edges: [] });
    renderPanel(api);
    const trigger = await entityPill(0);
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(trigger).toHaveFocus();
  });

  it("ignores a late graph response from an earlier selection", async () => {
    const api = fakeApi({}, ENTITIES);
    const old = deferred<{ nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[] }>();
    api.entity.mockImplementation(async id => detailFor(id === "e0" ? 0 : 1));
    api.subgraph.mockImplementationOnce(() => old.promise)
      .mockResolvedValueOnce({ nodes: [ENTITIES[1]], edges: [] });
    renderPanel(api);
    await userEvent.click(await entityPill(1));
    await screen.findByRole("button", { name: "Beta · concept" });
    await act(async () => { old.resolve({ nodes: [ENTITIES[0]], edges: [] }); });
    expect(screen.queryByRole("button", { name: "Alpha · concept" })).not.toBeInTheDocument();
  });

  it("closes detail, restores the entity trigger focus and ignores pending replies", async () => {
    const api = fakeApi({}, ENTITIES);
    const old = deferred<KnowledgeGraphEntityDetail>();
    api.entity.mockImplementation(() => old.promise);
    renderPanel(api);
    const trigger = await entityPill(0);
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(trigger).toHaveFocus();
    await act(async () => { old.resolve(detailFor(0)); });
    expect(screen.queryByRole("heading", { name: "Alpha" })).not.toBeInTheDocument();
  });

  it("ignores an old entity response and opens the current evidence", async () => {
    const api = fakeApi({}, ENTITIES);
    const old = deferred<KnowledgeGraphEntityDetail>();
    api.entity.mockImplementation((id?: string) => id === "e0" ? old.promise : Promise.resolve(detailFor(1)));
    const onOpenChunk = vi.fn();
    render(<GraphPanel kbId="kb" token="t" client={{} as never} api={api} onOpenChunk={onOpenChunk} />);
    await userEvent.click(await entityPill(1));
    await screen.findByRole("heading", { name: "Beta" });
    await act(async () => { old.resolve(detailFor(0)); });
    expect(screen.queryByRole("heading", { name: "Alpha" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Beta source/ }));
    expect(onOpenChunk).toHaveBeenCalledWith("doc", "chunk");
  });

  it("retries only failed entity detail and clears the local error", async () => {
    const api = fakeApi({}, ENTITIES);
    api.entity.mockRejectedValueOnce(new Error("detail unavailable")).mockResolvedValueOnce(detailFor(0));
    renderPanel(api);
    expect(await screen.findByRole("alert")).toHaveTextContent("detail unavailable");
    const statsCalls = api.stats.mock.calls.length;
    const entityCalls = api.entities.mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.graphRetryDetail") }));
    await screen.findByRole("heading", { name: "Alpha" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(api.stats).toHaveBeenCalledTimes(statsCalls);
    expect(api.entities).toHaveBeenCalledTimes(entityCalls);
  });

  it("keeps detail when the subgraph fails and retries that graph only", async () => {
    const api = fakeApi({}, ENTITIES);
    api.entity.mockResolvedValue(detailFor(0));
    api.subgraph.mockRejectedValueOnce(new Error("graph unavailable")).mockResolvedValueOnce({ nodes: [], edges: [] });
    renderPanel(api);
    await screen.findByRole("heading", { name: "Alpha" });
    expect(await screen.findByRole("alert")).toHaveTextContent("graph unavailable");
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.graphRetryGraph") }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(api.entity).toHaveBeenCalledTimes(1);
    expect(api.subgraph).toHaveBeenCalledTimes(2);
  });

  it("invalidates pending detail when the library changes", async () => {
    const api = fakeApi({}, ENTITIES);
    const old = deferred<KnowledgeGraphEntityDetail>();
    // 切换知识库后的请求永远挂起，旧响应即使迟到也不得渲染。
    api.entity.mockImplementationOnce(() => old.promise)
      .mockImplementation(() => new Promise<KnowledgeGraphEntityDetail>(() => {}));
    const { rerender } = renderPanel(api);
    await userEvent.click(await entityPill(0));
    rerender(<GraphPanel kbId="other" token="t" client={{} as never} api={api} />);
    await act(async () => { old.resolve(detailFor(0)); });
    // 切换知识库后，旧库的详情证据不得再出现。
    expect(screen.queryByRole("button", { name: /Alpha source/ })).not.toBeInTheDocument();
  });

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
    expect((await screen.findAllByText("web-api")).length).toBeGreaterThan(0);
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
