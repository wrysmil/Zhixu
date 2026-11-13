import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeSettingsPanel } from "@/components/workspace/knowledge/KnowledgeSettingsPanel";
import {
  fetchKnowledgeSettings,
  updateKnowledgeSettings,
  type KnowledgeRunSettings,
} from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchKnowledgeSettings: vi.fn(),
    updateKnowledgeSettings: vi.fn(),
  };
});

const SETTINGS: KnowledgeRunSettings = {
  retrieval: {
    vectorWeight: 0.7,
    keywordWeight: 0.3,
    rrfK: 60,
    defaultLimit: 8,
    maxLimit: 20,
    mmrLambda: 0.7,
    vectorThreshold: 0.15,
    keywordThreshold: 0.3,
    expandOnLowRecall: true,
    expansionLimit: 5,
  },
  rerank: { enabled: false, backend: "remote", model: "", threshold: 0.3, maxCandidates: 200 },
  chunking: { chunkSize: 800, chunkOverlap: 120, tokenizerMode: "jieba", paragraphMode: "blank_line" },
  scope: { retrieval: "live", rerank: "live", chunking: "requires_reindex" },
};

const transport = { requestMutation: vi.fn() } as never;

beforeEach(() => {
  vi.mocked(fetchKnowledgeSettings).mockReset();
  vi.mocked(updateKnowledgeSettings).mockReset();
  vi.mocked(fetchKnowledgeSettings).mockResolvedValue({ enabled: true, settings: SETTINGS });
});

describe("knowledge settings panel", () => {
  it("loads effective values and keeps save disabled until edited", async () => {
    render(<KnowledgeSettingsPanel token="token" transport={transport} />);
    expect(await screen.findByLabelText("vector weight")).toHaveValue("0.7");
    expect(screen.getByLabelText("keyword weight")).toHaveValue("0.3");
    expect(screen.getByLabelText("rrf k")).toHaveValue(60);
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    expect(screen.getByText("800")).toBeInTheDocument();
  });

  it("saves edited retrieval and rerank values with immediate effect", async () => {
    const user = userEvent.setup();
    vi.mocked(updateKnowledgeSettings).mockResolvedValue({
      enabled: true,
      changed: ["retrieval.vector_weight", "rerank.enabled"],
      applied: true,
      requiresReindex: false,
      settings: {
        ...SETTINGS,
        retrieval: { ...SETTINGS.retrieval, vectorWeight: 0.6 },
        rerank: { ...SETTINGS.rerank, enabled: true },
      },
    });
    render(<KnowledgeSettingsPanel token="token" transport={transport} />);
    await screen.findByLabelText("vector weight");

    await user.clear(screen.getByLabelText("rrf k"));
    await user.type(screen.getByLabelText("rrf k"), "80");
    await user.click(screen.getByLabelText("rerank enabled"));
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(updateKnowledgeSettings).toHaveBeenCalledTimes(1));
    const [, payload] = vi.mocked(updateKnowledgeSettings).mock.calls[0];
    expect(payload.retrieval?.rrfK).toBe(80);
    expect(payload.rerank?.enabled).toBe(true);
    expect(await screen.findByText("已保存，立即生效")).toBeInTheDocument();
  });

  it("restores the saved draft when undoing", async () => {
    const user = userEvent.setup();
    render(<KnowledgeSettingsPanel token="token" transport={transport} />);
    const weight = await screen.findByLabelText("vector weight");
    fireEvent.change(weight, { target: { value: "0.2" } });
    expect(weight).toHaveValue("0.2");
    await user.click(screen.getByRole("button", { name: "撤销" }));
    expect(screen.getByLabelText("vector weight")).toHaveValue("0.7");
  });

  it("shows the failure instead of a blank panel", async () => {
    vi.mocked(fetchKnowledgeSettings).mockRejectedValue(new Error("settings unavailable"));
    render(<KnowledgeSettingsPanel token="token" transport={transport} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("settings unavailable");
  });
});
