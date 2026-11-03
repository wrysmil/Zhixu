import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IdentityPage } from "@/components/workspace/IdentityPage";
import { Toaster } from "@/components/ui/toaster";
import i18n from "@/i18n";
import {
  fetchIdentityFile,
  getActivePersona,
  listIdentityPresets,
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
    listIdentityPresets: vi.fn(),
    reloadIdentity: vi.fn(),
    saveIdentityFile: vi.fn(),
    setActivePersona: vi.fn(),
  };
});

const requestMutation = vi.fn();
const transport = { requestMutation } as never;

const SOUL_MD = ["# Soul", "你的名字：小明", "## 你主要帮的事", "把一周的会议整理成周报"].join("\n");
const AGENTS_MD = ["# Workspace", "先读后写。"].join("\n");

function renderPage() {
  return render(
    <ClientProvider client={transport} token="tok">
      <IdentityPage />
      <Toaster />
    </ClientProvider>,
  );
}

describe("IdentityPage paged editor", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    requestMutation.mockReset().mockResolvedValue({});
    vi.mocked(listIdentityPresets).mockReset().mockResolvedValue({ presets: [] });
    vi.mocked(getActivePersona).mockReset().mockResolvedValue({ active: "", options: [] });
    vi.mocked(fetchIdentityFile).mockReset().mockImplementation(async (_token, name) => ({
      name,
      content: name === "AGENTS.md" ? AGENTS_MD : SOUL_MD,
      exists: true,
    }));
    vi.mocked(saveIdentityFile).mockReset().mockResolvedValue({ saved: true });
    vi.mocked(reloadIdentity).mockReset().mockResolvedValue({ reloaded: true } as never);
    vi.mocked(setActivePersona).mockReset().mockResolvedValue({ active: "" });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows one page at a time and switches through the step bar", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findAllByRole("tab")).toHaveLength(4);
    // Page 1 is the vibe gallery: five cards, no form fields yet.
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(5);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);

    await user.click(screen.getByRole("tab", { name: /Name & tone/ }));
    expect(screen.queryAllByRole("heading", { level: 3 })).toHaveLength(0);
    expect(screen.getAllByRole("textbox")).toHaveLength(2);

    // Next / Back walk the same pages as the step bar.
    await user.click(screen.getByRole("button", { name: /Next/ }));
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(screen.getByRole("tab", { name: /Rules & notes/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    await user.click(screen.getByRole("button", { name: /Next/ }));
    expect(screen.getByRole("button", { name: /Advanced: edit raw Markdown/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /Back/ }));
    expect(screen.getByRole("tab", { name: /Rules & notes/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("confirms a save with a transient status notice", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findAllByRole("heading", { level: 3 });

    expect(screen.queryByText("Identity saved — live now")).not.toBeInTheDocument();

    // Edit the form first — an untouched page must not rewrite the file.
    await user.click(screen.getByRole("tab", { name: /Name & tone/ }));
    const inputs = screen.getAllByRole("textbox");
    await user.clear(inputs[0]);
    await user.type(inputs[0], "知序");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    expect(await screen.findByText("Identity saved — live now")).toBeInTheDocument();
    expect(vi.mocked(saveIdentityFile)).toHaveBeenCalledWith(
      transport,
      expect.objectContaining({ name: "SOUL.md" }),
    );
  });

  it("does not rewrite the file when nothing changed", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findAllByRole("heading", { level: 3 });

    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    expect(await screen.findByText("Identity saved — live now")).toBeInTheDocument();
    expect(vi.mocked(saveIdentityFile)).not.toHaveBeenCalled();
  });

  it("shows the raw file in the advanced panel and can edit AGENTS.md", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findAllByRole("heading", { level: 3 });

    await user.click(screen.getByRole("tab", { name: /Advanced/ }));
    await user.click(screen.getByRole("button", { name: /Advanced: edit raw Markdown/ }));

    const editor = await screen.findByRole("textbox", { name: "Raw Markdown" });
    // The panel shows the file as written on disk, not a form skeleton.
    expect((editor as HTMLTextAreaElement).value).toBe(SOUL_MD);

    await user.click(screen.getByRole("button", { name: "AGENTS.md" }));
    expect((editor as HTMLTextAreaElement).value).toBe(AGENTS_MD);

    await user.clear(editor);
    await user.type(editor, "# Workspace\n先读后写，再问。");
    await user.click(screen.getByRole("button", { name: /^Save$/ }));

    expect(vi.mocked(saveIdentityFile)).toHaveBeenCalledWith(
      transport,
      expect.objectContaining({ name: "AGENTS.md", content: "# Workspace\n先读后写，再问。" }),
    );
  });
});
