import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgePage } from "@/components/workspace/KnowledgePage";
import { WorkspaceNavigation } from "@/components/workspace/WorkspaceNavigation";
import i18n from "@/i18n";
import {
  addKnowledgeDoc,
  addKnowledgeDocFromPath,
  createKnowledgeKb,
  deleteKnowledgeDoc,
  deleteKnowledgeKb,
  fetchKnowledgeDocs,
  fetchKnowledgeKbs,
  fetchKnowledgeStats,
  reindexKnowledgeDoc,
  reindexKnowledgeKb,
  repairKnowledge,
  searchKnowledge,
  type KnowledgeDoc,
  type KnowledgeKb,
  type KnowledgeSearchResult,
  type KnowledgeStats,
} from "@/lib/api";
import { ClientProvider } from "@/providers/ClientProvider";
import { parseWorkspaceHash, workspaceRouteHash } from "@/workspace/routes";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchKnowledgeKbs: vi.fn(),
    fetchKnowledgeDocs: vi.fn(),
    fetchKnowledgeStats: vi.fn(),
    searchKnowledge: vi.fn(),
    createKnowledgeKb: vi.fn(),
    updateKnowledgeKb: vi.fn(),
    deleteKnowledgeKb: vi.fn(),
    reindexKnowledgeKb: vi.fn(),
    addKnowledgeDoc: vi.fn(),
    addKnowledgeDocFromPath: vi.fn(),
    deleteKnowledgeDoc: vi.fn(),
    reindexKnowledgeDoc: vi.fn(),
    repairKnowledge: vi.fn(),
  };
});

const requestMutation = vi.fn();
const transport = { requestMutation } as never;

const KBS: KnowledgeKb[] = [
  {
    id: "kb_1",
    name: "Product manual",
    description: "Customer facing docs",
    workspaceId: "ws1",
    visibility: "workspace",
    isEnabled: true,
    documentCount: 2,
  },
  {
    id: "kb_2",
    name: "Team notes",
    description: "",
    workspaceId: "ws1",
    visibility: "global",
    isEnabled: false,
    documentCount: 0,
  },
];

const DOCS: KnowledgeDoc[] = [
  {
    id: "doc_1",
    kbId: "kb_1",
    title: "Deploy guide",
    originalName: "deploy.pdf",
    status: "ready",
    chunkCount: 12,
    charCount: 4096,
    mime: "application/pdf",
  },
  {
    id: "doc_2",
    kbId: "kb_1",
    title: "Rollback notes",
    originalName: "rollback.md",
    status: "failed",
    chunkCount: 0,
    charCount: 0,
    error: "parse failed",
  },
];

const STATS: KnowledgeStats = {
  enabled: true,
  kbs: 2,
  documents: 2,
  chunks: 12,
  pending: 1,
  vector: 11,
};

const HIT_RESULT: KnowledgeSearchResult = {
  enabled: true,
  modeUsed: "semantic",
  degraded: ["vector_missing"],
  reason: null,
  routes: ["fts", "vector"],
  hits: [
    {
      chunkId: "chk_1",
      docId: "doc_1",
      kbId: "kb_1",
      score: 0.9123,
      content: "Rollback checklist",
      heading: "Deploy > Rollback",
      page: 3,
      locator: "p3",
    },
  ],
};

function renderPage() {
  return render(
    <ClientProvider client={transport} token="tok">
      <KnowledgePage workspaceIds={["ws1"]} />
    </ClientProvider>,
  );
}

describe("knowledge page", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    requestMutation.mockReset().mockResolvedValue({});
    vi.mocked(fetchKnowledgeKbs).mockReset().mockResolvedValue({ enabled: true, kbs: KBS });
    vi.mocked(fetchKnowledgeDocs).mockReset().mockResolvedValue({
      enabled: true,
      documents: DOCS,
      total: DOCS.length,
    });
    vi.mocked(fetchKnowledgeStats).mockReset().mockResolvedValue(STATS);
    vi.mocked(searchKnowledge).mockReset().mockResolvedValue({
      enabled: true,
      modeUsed: "hybrid",
      degraded: [],
      reason: null,
      routes: [],
      hits: [],
    });
    vi.mocked(createKnowledgeKb).mockReset().mockResolvedValue({ id: "kb_new" });
    vi.mocked(deleteKnowledgeKb).mockReset().mockResolvedValue({ ok: true, id: "kb_2" });
    vi.mocked(reindexKnowledgeKb).mockReset().mockResolvedValue({ ok: true, queued: 1 });
    vi.mocked(addKnowledgeDoc).mockReset().mockResolvedValue({ document: DOCS[0] });
    vi.mocked(addKnowledgeDocFromPath).mockReset().mockResolvedValue({ document: DOCS[0] });
    vi.mocked(deleteKnowledgeDoc).mockReset().mockResolvedValue({ ok: true, id: "doc_1" });
    vi.mocked(reindexKnowledgeDoc).mockReset().mockResolvedValue({ ok: true, queued: true });
    vi.mocked(repairKnowledge).mockReset().mockResolvedValue({ ok: true });
  });

  it("registers the #/knowledge route and the rail entry", () => {
    expect(parseWorkspaceHash("#/knowledge")).toMatchObject({ view: "knowledge" });
    expect(workspaceRouteHash({ view: "knowledge" })).toBe("#/knowledge");

    render(
      <WorkspaceNavigation
        activeView="knowledge"
        collapsed={false}
        preview={false}
        onNavigate={vi.fn()}
        onToggleCollapsed={vi.fn()}
      />,
    );
    const item = screen.getByRole("button", { name: "Knowledge" });
    expect(item).toHaveAttribute("aria-current", "page");
  });

  it("lists knowledge bases with visibility, doc count and enabled state", async () => {
    renderPage();

    const list = await screen.findByTestId("knowledge-kb-list");
    const first = within(list).getByText("Product manual").closest("li")!;
    expect(within(first).getByText("Workspace")).toBeInTheDocument();
    expect(within(first).getByText("2 docs")).toBeInTheDocument();
    expect(within(first).getByText("Enabled")).toBeInTheDocument();

    const second = within(list).getByText("Team notes").closest("li")!;
    expect(within(second).getByText("Global")).toBeInTheDocument();
    expect(within(second).getByText("0 docs")).toBeInTheDocument();
    expect(within(second).getByText("Disabled")).toBeInTheDocument();
  });

  it("creates a knowledge base and refreshes the list", async () => {
    renderPage();
    await screen.findByTestId("knowledge-kb-list");

    await userEvent.click(screen.getByRole("button", { name: "New knowledge base" }));
    await userEvent.type(screen.getByLabelText("Name"), "Handbook");
    await userEvent.type(screen.getByLabelText("Description"), "All things");
    await userEvent.selectOptions(screen.getByLabelText("Visibility"), "global");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(createKnowledgeKb).toHaveBeenCalledWith(transport, {
        name: "Handbook",
        description: "All things",
        visibility: "global",
        workspaceId: "ws1",
      });
    });
    await waitFor(() => expect(fetchKnowledgeKbs).toHaveBeenCalledTimes(2));
  });

  it("asks for confirmation before deleting a knowledge base", async () => {
    renderPage();
    const row = (await screen.findByText("Team notes")).closest("li")!;

    await userEvent.click(within(row).getByRole("button", { name: "Delete knowledge base" }));
    expect(deleteKnowledgeKb).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(deleteKnowledgeKb).toHaveBeenCalledWith(transport, "kb_2"));
    await waitFor(() => expect(fetchKnowledgeKbs).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Team notes")).toBeInTheDocument();
  });

  it("renders documents and uploads a picked file as a data url", async () => {
    renderPage();
    await screen.findByText("Deploy guide");

    const row = screen.getByText("Deploy guide").closest("tr")!;
    expect(within(row).getByText("Ready")).toBeInTheDocument();
    expect(within(row).getByText("12")).toBeInTheDocument();
    expect(within(row).getByText("deploy.pdf")).toBeInTheDocument();
    expect(screen.getByText("Rollback notes")).toBeInTheDocument();
    expect(screen.getByText("Error: parse failed")).toBeInTheDocument();

    const file = new File(["# rollback"], "rollback.md", { type: "text/markdown" });
    await userEvent.upload(screen.getByLabelText("Upload"), file);

    await waitFor(() => expect(addKnowledgeDoc).toHaveBeenCalledTimes(1));
    const [, payload] = vi.mocked(addKnowledgeDoc).mock.calls[0];
    expect(payload).toMatchObject({ kbId: "kb_1", name: "rollback.md", workspaceId: "ws1" });
    expect(payload.dataUrl).toMatch(/^data:text\/markdown;base64,/);
    await waitFor(() => expect(fetchKnowledgeDocs).toHaveBeenCalledTimes(2));
  });

  it("adds a server path document", async () => {
    renderPage();
    await screen.findByText("Deploy guide");

    await userEvent.type(screen.getByLabelText("Add server path"), "/tmp/report.pdf");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => {
      expect(addKnowledgeDocFromPath).toHaveBeenCalledWith(transport, {
        kbId: "kb_1",
        path: "/tmp/report.pdf",
        workspaceId: "ws1",
      });
    });
    await waitFor(() => expect(fetchKnowledgeDocs).toHaveBeenCalledTimes(2));
  });

  it("reindexes documents and bases and deletes documents after confirmation", async () => {
    renderPage();
    const row = (await screen.findByText("Deploy guide")).closest("tr")!;

    await userEvent.click(within(row).getByRole("button", { name: "Reindex" }));
    await waitFor(() => expect(reindexKnowledgeDoc).toHaveBeenCalledWith(transport, "doc_1"));

    await userEvent.click(screen.getByRole("button", { name: "Reindex all" }));
    await waitFor(() => expect(reindexKnowledgeKb).toHaveBeenCalledWith(transport, "kb_1"));

    await userEvent.click(within(row).getByRole("button", { name: "Delete document" }));
    expect(deleteKnowledgeDoc).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(deleteKnowledgeDoc).toHaveBeenCalledWith(transport, "doc_1"));
  });

  it("searches with mode and limit and shows mode, routes, degraded and handles", async () => {
    vi.mocked(searchKnowledge).mockResolvedValue(HIT_RESULT);
    renderPage();
    await screen.findByText("Deploy guide");

    await userEvent.type(screen.getByLabelText("Query"), "rollback");
    await userEvent.selectOptions(screen.getByLabelText("Mode"), "semantic");
    await userEvent.selectOptions(screen.getByLabelText("Limit"), "12");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      expect(searchKnowledge).toHaveBeenCalledWith("tok", {
        query: "rollback",
        mode: "semantic",
        limit: 12,
        kbIds: ["kb_1"],
        workspaceIds: ["ws1"],
      });
    });

    expect(await screen.findByText("Rollback checklist")).toBeInTheDocument();
    expect(screen.getByText("Mode used: semantic")).toBeInTheDocument();
    expect(screen.getByText("Routes: fts, vector")).toBeInTheDocument();
    expect(screen.getByText("Degraded: vector_missing")).toBeInTheDocument();
    expect(screen.getByText("Deploy > Rollback")).toBeInTheDocument();
    expect(screen.getByText("Page 3")).toBeInTheDocument();
    expect(screen.getByText("0.912")).toBeInTheDocument();
    expect(screen.getByText("chk_1")).toBeInTheDocument();
    expect(screen.getByText("doc_1")).toBeInTheDocument();
  });

  it("distinguishes empty results from filtered results", async () => {
    renderPage();
    await screen.findByText("Deploy guide");

    await userEvent.type(screen.getByLabelText("Query"), "nothing");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("No results for this query")).toBeInTheDocument();

    vi.mocked(searchKnowledge).mockResolvedValueOnce({
      enabled: true,
      modeUsed: "keyword",
      degraded: [],
      reason: "no_scope",
      routes: [],
      hits: [],
    });
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(
      await screen.findByText("No results in the selected scope (no_scope)"),
    ).toBeInTheDocument();
  });

  it("shows a search failure instead of an empty state", async () => {
    renderPage();
    await screen.findByText("Deploy guide");

    vi.mocked(searchKnowledge).mockRejectedValueOnce(new Error("gateway down"));
    await userEvent.type(screen.getByLabelText("Query"), "rollback");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Search failed: gateway down");
    expect(screen.queryByText("No results for this query")).not.toBeInTheDocument();
  });

  it("renders the stats strip", async () => {
    renderPage();

    const stats = await screen.findByTestId("knowledge-stats");
    expect(within(stats).getByTestId("knowledge-stat-kbs")).toHaveTextContent("2");
    expect(within(stats).getByTestId("knowledge-stat-docs")).toHaveTextContent("2");
    expect(within(stats).getByTestId("knowledge-stat-chunks")).toHaveTextContent("12");
    expect(within(stats).getByTestId("knowledge-stat-pending")).toHaveTextContent("1");
    expect(within(stats).getByTestId("knowledge-stat-vectors")).toHaveTextContent("11");
  });

  it("shows the disabled banner and skips document loading when knowledge is off", async () => {
    vi.mocked(fetchKnowledgeKbs).mockResolvedValue({ enabled: false, kbs: [] });
    vi.mocked(fetchKnowledgeStats).mockResolvedValue({
      enabled: false, kbs: 0, documents: 0, chunks: 0, pending: 0, vector: 0,
    });
    renderPage();

    expect(await screen.findByTestId("knowledge-disabled-banner")).toHaveTextContent(
      "Knowledge center is disabled",
    );
    expect(fetchKnowledgeDocs).not.toHaveBeenCalled();
    expect(screen.getByText("No knowledge bases yet")).toBeInTheDocument();
  });

  it("repairs the index only after the purge warning is confirmed", async () => {
    renderPage();
    await screen.findByTestId("knowledge-stats");

    await userEvent.click(screen.getByRole("button", { name: "Repair index" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Purge drifted vectors"));
    expect(screen.getByText(/Purging deletes drifted vectors/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Run repair" }));

    await waitFor(() => {
      expect(repairKnowledge).toHaveBeenCalledWith(transport, {
        fts: true,
        vectors: true,
        purge: true,
        workspaceId: "ws1",
      });
    });
    await waitFor(() => expect(fetchKnowledgeStats).toHaveBeenCalledTimes(2));
  });
});
