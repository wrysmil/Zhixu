import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IdentityPage } from "@/components/workspace/IdentityPage";
import { Toaster } from "@/components/ui/toaster";
import i18n from "@/i18n";
import {
  fetchIdentityFile,
  getActivePersona,
  listIdentityFiles,
  reloadIdentity,
  saveIdentityFile,
  setActivePersona,
} from "@/lib/api";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchIdentityFile: vi.fn(),
    getActivePersona: vi.fn(),
    listIdentityFiles: vi.fn(),
    reloadIdentity: vi.fn(),
    saveIdentityFile: vi.fn(),
    setActivePersona: vi.fn(),
  };
});

const requestMutation = vi.fn();
const transport = { requestMutation } as never;

const SOUL_MD = ["# Soul", "我是 知序", "## 核心原则", "- 先做再说。"].join("\n");
const AGENTS_MD = ["# Agent Instructions", "先读后写。"].join("\n");

function fileEntry(
  name: string,
  group: "core" | "personas",
  extra: Record<string, unknown> = {},
) {
  return {
    name,
    group,
    exists: true,
    restricted: false,
    ...extra,
  };
}

function renderPage() {
  return render(
    <ClientProvider client={transport} token="tok">
      <IdentityPage />
      <Toaster />
    </ClientProvider>,
  );
}

describe("IdentityPage file editor", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    requestMutation.mockReset().mockResolvedValue({});
    vi.mocked(listIdentityFiles).mockReset().mockResolvedValue({
      files: [
        fileEntry("SOUL.md", "core"),
        fileEntry("AGENT.md", "core", { restricted: true }),
        fileEntry("AGENTS.md", "core"),
        fileEntry("personas/balanced.md", "personas", {
          name: "balanced.md",
          logicalPath: "personas/balanced.md",
        }),
      ],
      charLimit: 1500,
    });
    vi.mocked(fetchIdentityFile).mockReset().mockImplementation(async (_token, name) => ({
      name,
      content: name === "AGENTS.md" ? AGENTS_MD : SOUL_MD,
      exists: true,
    }));
    vi.mocked(saveIdentityFile).mockReset().mockResolvedValue({ saved: true });
    vi.mocked(reloadIdentity).mockReset().mockResolvedValue({ status: "ok" } as never);
    vi.mocked(getActivePersona).mockReset().mockResolvedValue({ active: "balanced", options: [] });
    vi.mocked(setActivePersona).mockReset().mockResolvedValue({ active: "balanced" });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("lists the identity files and loads the selected one", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("button", { name: /SOUL\.md/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /AGENTS\.md/ })).toBeInTheDocument();

    const editor = await screen.findByRole("textbox", { name: "Identity file content" });
    await waitFor(() => expect((editor as HTMLTextAreaElement).value).toBe(SOUL_MD));

    await user.click(screen.getByRole("button", { name: /AGENTS\.md/ }));
    await waitFor(() => expect((editor as HTMLTextAreaElement).value).toBe(AGENTS_MD));
  });

  it("does not write the file when nothing changed", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("button", { name: /SOUL\.md/ });
    const save = screen.getByRole("button", { name: /^Save$/ });
    expect(save).toBeDisabled();

    await user.click(save);
    expect(vi.mocked(saveIdentityFile)).not.toHaveBeenCalled();
  });

  it("asks for confirmation before saving a high-risk file", async () => {
    const user = userEvent.setup();
    renderPage();

    const editor = await screen.findByRole("textbox", { name: "Identity file content" });
    await waitFor(() => expect((editor as HTMLTextAreaElement).value).toBe(SOUL_MD));

    await user.type(editor, "\n新增一条原则。");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(vi.mocked(saveIdentityFile)).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /I understand the risk/ }));
    await waitFor(() =>
      expect(vi.mocked(saveIdentityFile)).toHaveBeenCalledWith(
        transport,
        expect.objectContaining({ name: "SOUL.md" }),
      ),
    );
  });

  it("saves a low-risk file without confirmation", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /balanced\.md/ }));
    const editor = await screen.findByRole("textbox", { name: "Identity file content" });
    await waitFor(() => expect((editor as HTMLTextAreaElement).value).toBe(SOUL_MD));

    await user.type(editor, " 再补一句。");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    await waitFor(() =>
      expect(vi.mocked(saveIdentityFile)).toHaveBeenCalledWith(
        transport,
        expect.objectContaining({ name: "balanced.md" }),
      ),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("activates a persona from the file list", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /balanced\.md/ }));
    await user.click(screen.getByRole("button", { name: /Use this persona/ }));

    await waitFor(() =>
      expect(vi.mocked(setActivePersona)).toHaveBeenCalledWith(transport, "balanced"),
    );
  });
});
