import { act, render, screen, waitFor, within } from "@testing-library/react";
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
  fetchKnowledgeDoc,
  fetchKnowledgeDocContent,
  fetchKnowledgeDocs,
  fetchKnowledgeKbs,
  fetchKnowledgeQueryHistory,
  clearKnowledgeQueryHistory,
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
    fetchKnowledgeDoc: vi.fn(),
    fetchKnowledgeDocContent: vi.fn(),
    fetchKnowledgeQueryHistory: vi.fn(),
    clearKnowledgeQueryHistory: vi.fn(),
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

async function openKb(name = "Product manual") {
  await userEvent.click(await screen.findByRole("button", { name }));
  await screen.findByRole("tab", { name: "Documents" });
}

async function openRetrievalBar() {
  await screen.findByTestId("knowledge-retrieval-bar");
}

describe("knowledge page", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    requestMutation.mockReset().mockResolvedValue({});
    vi.mocked(fetchKnowledgeKbs).mockReset().mockResolvedValue({ enabled: true, kbs: KBS });
    vi.mocked(fetchKnowledgeQueryHistory).mockReset().mockResolvedValue({
      enabled: true,
      items: [],
      total: 0,
    });
    vi.mocked(clearKnowledgeQueryHistory).mockReset().mockResolvedValue({ ok: true, removed: 0 });
    vi.mocked(fetchKnowledgeDocs).mockReset().mockResolvedValue({
      enabled: true,
      documents: DOCS,
      total: DOCS.length,
    });
    vi.mocked(fetchKnowledgeDoc).mockReset().mockResolvedValue({
      enabled: true,
      document: DOCS[0],
      chunks: [],
      chunkCount: DOCS[0].chunkCount,
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

  it("filters library names and restores search and focus when returning", async () => {
    renderPage();
    await screen.findByText("Product manual");
    await userEvent.type(screen.getByRole("textbox", { name: i18n.t("knowledge.experience.searchLibraries") }), "Product");
    expect(screen.queryByText("Team notes")).not.toBeInTheDocument();
    await openKb();
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.backToLibraries") }));
    expect(screen.getByRole("textbox", { name: i18n.t("knowledge.experience.searchLibraries") })).toHaveValue("Product");
    expect(screen.getByRole("button", { name: "Product manual" })).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.clearSearch") }));
    expect(screen.getByText("Team notes")).toBeInTheDocument();
  });

  it("ignores late document lists after switching libraries", async () => {
    let resolveOld!: (value: { enabled: boolean; documents: KnowledgeDoc[]; total: number }) => void;
    vi.mocked(fetchKnowledgeDocs).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    renderPage();
    await openKb();
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.backToLibraries") }));
    vi.mocked(fetchKnowledgeDocs).mockResolvedValue({ enabled: true, documents: [{ ...DOCS[0], id: "new", kbId: "kb_2", title: "Team document" }], total: 1 });
    await openKb("Team notes");
    expect(await screen.findByText("Team document")).toBeInTheDocument();
    await act(async () => resolveOld({ enabled: true, documents: DOCS, total: 2 }));
    expect(screen.queryByText("Deploy guide")).not.toBeInTheDocument();
  });

  it("keeps a failed upload draft and retries without picking again", async () => {
    vi.mocked(addKnowledgeDoc).mockRejectedValueOnce(new Error("upload offline"));
    renderPage();
    await openKb();
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.addMaterial") }));
    await userEvent.upload(screen.getByLabelText("Upload"), new File(["draft"], "draft.md"));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("upload offline");
    expect(within(dialog).getByText("draft.md")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: i18n.t("knowledge.experience.retryUpload") }));
    await waitFor(() => expect(addKnowledgeDoc).toHaveBeenCalledTimes(2));
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

  it("opens documents by title and keeps failure details readable", async () => {
    renderPage();
    await openKb();
    await userEvent.click(await screen.findByRole("button", { name: "Deploy guide" }));
    expect(await screen.findByTestId("knowledge-chunk-preview")).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    vi.mocked(fetchKnowledgeDoc).mockResolvedValueOnce({ enabled: true, document: DOCS[1], chunks: [], chunkCount: 0 });
    await userEvent.click(screen.getByRole("button", { name: "Rollback notes" }));
    expect(await within(screen.getByRole("dialog")).findByText("parse failed")).toBeInTheDocument();
  });

  it("does not reload an old library when a mutation completes after navigation", async () => {
    let finish!: (value: { ok: boolean; queued: boolean }) => void;
    vi.mocked(reindexKnowledgeDoc).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    renderPage();
    await openKb();
    const row = (await screen.findByText("Deploy guide")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Reindex" }));
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.backToLibraries") }));
    vi.mocked(fetchKnowledgeDocs).mockResolvedValue({ enabled: true, documents: [{ ...DOCS[0], id: "new", kbId: "kb_2", title: "Team document" }], total: 1 });
    await openKb("Team notes");
    await screen.findByText("Team document");
    vi.mocked(fetchKnowledgeDocs).mockResolvedValue({ enabled: true, documents: DOCS, total: 2 });
    await act(async () => finish({ ok: true, queued: true }));
    await waitFor(() => expect(screen.getByRole("button", { name: i18n.t("knowledge.experience.addMaterial") })).toBeEnabled());
    expect(screen.getByText("Team document")).toBeInTheDocument();
    expect(screen.queryByText("Deploy guide")).not.toBeInTheDocument();
  });

  it("uses server pages of 20 documents", async () => {
    vi.mocked(fetchKnowledgeDocs).mockResolvedValue({ enabled: true, documents: DOCS, total: 24 });
    renderPage();
    await openKb();
    await screen.findByText("Deploy guide");
    expect(fetchKnowledgeDocs).toHaveBeenLastCalledWith("tok", { kbId: "kb_1", workspaceIds: ["ws1"], limit: 20, offset: 0 });
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.next") }));
    await waitFor(() => expect(fetchKnowledgeDocs).toHaveBeenLastCalledWith("tok", { kbId: "kb_1", workspaceIds: ["ws1"], limit: 20, offset: 20 }));
  });

  it("ignores old search responses after returning and switching libraries", async () => {
    let resolveSearch!: (value: KnowledgeSearchResult) => void;
    vi.mocked(searchKnowledge).mockImplementationOnce(() => new Promise(resolve => { resolveSearch = resolve; }));
    renderPage();
    await openKb();
    await openRetrievalBar();
    await userEvent.type(screen.getByLabelText("Query"), "rollback");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.backToLibraries") }));
    await openKb("Team notes");
    await openRetrievalBar();
    await act(async () => resolveSearch(HIT_RESULT));
    expect(screen.queryByText("Rollback checklist")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Query")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
  });

  it("prevents duplicate uploads and returns to the list after acceptance", async () => {
    let accept!: (value: { document: KnowledgeDoc }) => void;
    vi.mocked(addKnowledgeDoc).mockImplementationOnce(() => new Promise(resolve => { accept = resolve; }));
    renderPage();
    await openKb();
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.addMaterial") }));
    const file = new File(["draft"], "draft.md");
    await userEvent.upload(screen.getByLabelText("Upload"), file);
    await waitFor(() => expect(addKnowledgeDoc).toHaveBeenCalledTimes(1));
    expect(screen.getByLabelText("Upload")).toBeDisabled();
    await act(async () => accept({ document: { ...DOCS[0], status: "pending" } }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: i18n.t("knowledge.experience.addMaterial") })).toBeEnabled());
    expect(addKnowledgeDoc).toHaveBeenCalledTimes(1);
  });

  it("lists knowledge bases with visibility, doc count and enabled state", async () => {
    renderPage();

    const list = await screen.findByTestId("knowledge-kb-list");
    const first = (await within(list).findByText("Product manual")).closest("li")!;
    expect(within(first).getByText("Workspace")).toBeInTheDocument();
    expect(within(first).getByText("2 docs")).toBeInTheDocument();
    expect(within(first).getByText("Enabled")).toBeInTheDocument();

    const second = (await within(list).findByText("Team notes")).closest("li")!;
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

    await userEvent.click(within(row).getByRole("button", { name: i18n.t("knowledge.experience.more") }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete knowledge base" }));
    expect(deleteKnowledgeKb).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(deleteKnowledgeKb).toHaveBeenCalledWith(transport, "kb_2"));
    await waitFor(() => expect(fetchKnowledgeKbs).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Team notes")).toBeInTheDocument();
  });

  it("renders documents and uploads a picked file as a data url", async () => {
    renderPage();
    await openKb();
    await screen.findByText("Deploy guide");

    const row = screen.getByText("Deploy guide").closest("tr")!;
    expect(within(row).getByText("Ready")).toBeInTheDocument();
    expect(within(row).getByText("12")).toBeInTheDocument();
    expect(within(row).getByText("deploy.pdf")).toBeInTheDocument();
    expect(screen.getByText("Rollback notes")).toBeInTheDocument();
    expect(screen.getByText("Error: parse failed")).toBeInTheDocument();

    const file = new File(["# rollback"], "rollback.md", { type: "text/markdown" });
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.addMaterial") }));
    await userEvent.upload(screen.getByLabelText("Upload"), file);

    await waitFor(() => expect(addKnowledgeDoc).toHaveBeenCalledTimes(1));
    const [, payload] = vi.mocked(addKnowledgeDoc).mock.calls[0];
    expect(payload).toMatchObject({ kbId: "kb_1", name: "rollback.md", workspaceId: "ws1" });
    expect(payload.dataUrl).toMatch(/^data:text\/markdown;base64,/);
    await waitFor(() => expect(fetchKnowledgeDocs).toHaveBeenCalledTimes(2));
  });

  it("adds a server path document", async () => {
    renderPage();
    await openKb();
    await screen.findByText("Deploy guide");

    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.addMaterial") }));
    await userEvent.click(screen.getByRole("tab", { name: i18n.t("knowledge.experience.serverPath") }));
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
    await openKb();
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

  it("previews document chunks with heading, page and content", async () => {
    vi.mocked(fetchKnowledgeDoc).mockResolvedValueOnce({
      enabled: true,
      document: DOCS[0],
      chunks: [
        {
          id: "chk_1", docId: "doc_1", kbId: "kb_1", index: 0,
          heading: "Deploy > Rollback", page: 3, locator: "p3",
          content: "Rollback checklist", charCount: 18,
        },
        {
          id: "chk_2", docId: "doc_1", kbId: "kb_1", index: 1,
          heading: "", page: null, locator: "",
          content: "Second chunk", charCount: 12,
        },
      ],
      chunkCount: 12,
    });
    renderPage();
    await openKb();
    await screen.findByText("Deploy guide");
    await userEvent.click(screen.getAllByRole("button", { name: "View chunks" })[0]);

    expect(fetchKnowledgeDoc).toHaveBeenCalledWith("tok", {
      id: "doc_1",
      chunkLimit: 10,
      chunkOffset: 0,
      chunkQuery: undefined,
    });
    const dialog = await screen.findByTestId("knowledge-chunk-preview");
    expect(within(dialog).getByText("Rollback checklist")).toBeInTheDocument();
    expect(within(dialog).getByText("Deploy > Rollback")).toBeInTheDocument();
    expect(within(dialog).getByText("1–2 / 12")).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("knowledge-chunk-preview")).not.toBeInTheDocument();
  });

  it("shows the original source inside the same dialog", async () => {
    vi.mocked(fetchKnowledgeDocContent).mockResolvedValue({
      enabled: true,
      kind: "text",
      name: "deploy.pdf",
      size: 2048,
      truncated: false,
      content: "Original rollback paragraph",
      pages: [],
      totalPages: 0,
      blocks: [],
    });
    renderPage();
    await openKb();
    await screen.findByText("Deploy guide");
    await userEvent.click(screen.getAllByRole("button", { name: "View chunks" })[0]);

    const dialog = await screen.findByTestId("knowledge-chunk-preview");
    expect(dialog).toHaveAttribute("role", "dialog");
    await userEvent.click(within(dialog).getByRole("tab", { name: "Source" }));

    expect(fetchKnowledgeDocContent).toHaveBeenCalledWith("tok", { id: "doc_1" });
    expect(await within(dialog).findByText("Original rollback paragraph")).toBeInTheDocument();
  });

  it("pages, searches and collapses chunks in the dialog", async () => {
    const chunk = {
      id: "chk_1", docId: "doc_1", kbId: "kb_1", index: 0,
      heading: "", page: null, locator: "",
      content: "Rollback checklist", charCount: 18,
    };
    vi.mocked(fetchKnowledgeDoc).mockResolvedValue({
      enabled: true,
      document: DOCS[0],
      chunks: [chunk],
      chunkCount: 30,
      chunkTotal: 30,
      chunkOffset: 0,
      chunkLimit: 10,
    });
    renderPage();
    await openKb();
    await screen.findByText("Deploy guide");
    await userEvent.click(screen.getAllByRole("button", { name: "View chunks" })[0]);
    const dialog = await screen.findByTestId("knowledge-chunk-preview");

    expect(within(dialog).getByText("1–1 / 30")).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(fetchKnowledgeDoc).toHaveBeenLastCalledWith("tok", {
        id: "doc_1",
        chunkLimit: 10,
        chunkOffset: 10,
        chunkQuery: undefined,
      }),
    );

    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "Search chunks" }),
      "rollback",
    );
    await waitFor(
      () =>
        expect(fetchKnowledgeDoc).toHaveBeenLastCalledWith("tok", {
          id: "doc_1",
          chunkLimit: 10,
          chunkOffset: 0,
          chunkQuery: "rollback",
        }),
      { timeout: 2000 },
    );

    await userEvent.click(within(dialog).getByRole("button", { name: "Collapse chunks" }));
    expect(within(dialog).queryByText("Rollback checklist")).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Expand chunks" }));
    expect(within(dialog).getByText("Rollback checklist")).toBeInTheDocument();
  });

  it("keeps the chunk preview open and shows load failures", async () => {
    vi.mocked(fetchKnowledgeDoc).mockRejectedValueOnce(new Error("boom"));
    renderPage();
    await openKb();
    await screen.findByText("Deploy guide");
    await userEvent.click(screen.getAllByRole("button", { name: "View chunks" })[0]);

    const dialog = await screen.findByTestId("knowledge-chunk-preview");
    expect(await within(dialog).findByText("Could not load chunks: boom")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("clears stale search results and refreshes kb counts after doc mutations", async () => {
    vi.mocked(searchKnowledge).mockResolvedValue(HIT_RESULT);
    renderPage();
    await openKb();
    await screen.findByText("Deploy guide");
    await openRetrievalBar();

    await userEvent.type(screen.getByLabelText("Query"), "rollback");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Rollback checklist")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
    const row = within(screen.getByRole("table")).getByText("Deploy guide").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Delete document" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(deleteKnowledgeDoc).toHaveBeenCalledWith(transport, "doc_1"));
    await waitFor(() => expect(fetchKnowledgeKbs).toHaveBeenCalledTimes(2));
    await openRetrievalBar();
    expect(screen.queryByText("Rollback checklist")).not.toBeInTheDocument();
  });

  it("renders the trace canvas, stage details and history", async () => {
    const traced: KnowledgeSearchResult = {
      ...HIT_RESULT,
      durationMs: 42,
      trace: {
        query: "rollback",
        mode: "semantic",
        limit: 12,
        totalMs: 42,
        stages: [
          {
            kind: "query",
            status: "ok",
            durationMs: 1,
            metrics: { tokens: 2, variants: 1 },
            detail: { tokens: ["rollback"] },
          },
          {
            kind: "rerank",
            status: "degraded",
            durationMs: 30,
            metrics: { candidatesIn: 5, candidatesKept: 1 },
            detail: {
              outcome: "model_unavailable",
              scores: [
                { chunkId: "chk_1", before: 0.5, after: 0.9, delta: 0.4 },
              ],
            },
          },
        ],
      },
    };
    vi.mocked(searchKnowledge).mockResolvedValue(traced);
    vi.mocked(fetchKnowledgeQueryHistory).mockResolvedValue({
      enabled: true,
      items: [
        {
          id: "qry_1",
          kbId: "kb_1",
          query: "旧查询",
          mode: "hybrid",
          limit: 8,
          hitCount: 2,
          durationMs: 20,
          createdAt: "2026-11-13T00:00:00+00:00",
        },
      ],
      total: 1,
    });
    renderPage();
    await openKb();
    await openRetrievalBar();
    await userEvent.click(screen.getByRole("button", { name: "Search history" }));
    await screen.findByText("旧查询");

    await userEvent.type(screen.getByLabelText("Query"), "rollback");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    const canvas = await screen.findByTestId("retrieval-trace-canvas");
    expect(within(canvas).getByText("Query preparation")).toBeInTheDocument();
    expect(within(canvas).getByText("42ms total")).toBeInTheDocument();

    await userEvent.click(within(canvas).getByRole("button", { name: /Rerank/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Rerank score changes")).toBeInTheDocument();
    expect(within(dialog).getByText("0.9")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));

    await userEvent.click(
      within(canvas).getByRole("button", { name: "Collapse pipeline" }),
    );
    expect(within(canvas).queryByText("Query preparation")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Search history" }));
    await userEvent.click(screen.getByText("旧查询"));
    await waitFor(() =>
      expect(searchKnowledge).toHaveBeenLastCalledWith(
        "tok",
        expect.objectContaining({ query: "旧查询", mode: "hybrid", limit: 8 }),
      ),
    );
  });

  it("searches with mode and limit and shows mode, routes, degraded and handles", async () => {
    vi.mocked(searchKnowledge).mockResolvedValue(HIT_RESULT);
    renderPage();
    await openKb();
    await openRetrievalBar();

    await userEvent.type(screen.getByLabelText("Query"), "rollback");
    await userEvent.selectOptions(screen.getByLabelText("Mode"), "semantic");
    await userEvent.selectOptions(screen.getByLabelText("Limit"), "12");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      expect(searchKnowledge).toHaveBeenCalledWith("tok", {
        query: "rollback",
        mode: "semantic",
        limit: 12,
        kbId: "kb_1",
        workspaceIds: ["ws1"],
        trace: true,
        record: true,
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
    await openKb();
    await openRetrievalBar();

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
    await openKb();
    await openRetrievalBar();

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
