import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FilePreviewPanel } from "@/components/FilePreviewPanel";
import { setAppLanguage } from "@/i18n";
import { fetchFilePreview } from "@/lib/api";

vi.mock("@/components/CodeBlock", () => ({
  CodeBlock: ({
    code,
    language,
    highlight,
  }: {
    code: string;
    language?: string;
    highlight?: boolean;
  }) => (
    <pre
      data-testid="mock-code-block"
      data-language={language}
      data-highlight={String(highlight)}
    >
      {code}
    </pre>
  ),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchFilePreview: vi.fn(),
  };
});

describe("FilePreviewPanel", () => {
  beforeEach(async () => {
    await setAppLanguage("en");
    vi.mocked(fetchFilePreview).mockReset();
  });

  it("resizes the external preview with keyboard and captured pointer, then stops on cancellation", async () => {
    vi.mocked(fetchFilePreview).mockResolvedValue({ path: "notes.md", display_path: "notes.md",
      language: "markdown", content: "# Notes", truncated: false });
    const change = vi.fn();
    render(<FilePreviewPanel sessionKey="websocket:chat-1" path="notes.md" token="tok"
      desktopWidth={544} maxDesktopWidth={800} onWidthChange={change} onClose={() => {}} />);
    await screen.findByTestId("mock-code-block");
    const separator = screen.getByRole("separator", { name: "Resize file preview" });
    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(change).toHaveBeenLastCalledWith(576);
    const panel = screen.getByTestId("file-preview-panel");
    vi.spyOn(panel, "getBoundingClientRect").mockReturnValue({ right: 1000 } as DOMRect);
    separator.setPointerCapture = vi.fn();
    separator.hasPointerCapture = vi.fn(() => true);
    separator.releasePointerCapture = vi.fn();
    fireEvent.pointerDown(separator, { pointerId: 5, button: 0, clientX: 456 });
    expect(separator.setPointerCapture).toHaveBeenCalledWith(5);
    fireEvent.pointerMove(separator, { pointerId: 5, clientX: 100 });
    expect(change).toHaveBeenLastCalledWith(800);
    fireEvent.pointerCancel(separator, { pointerId: 5 });
    const count = change.mock.calls.length;
    fireEvent.pointerMove(separator, { pointerId: 5, clientX: 500 });
    expect(change).toHaveBeenCalledTimes(count);
  });

  it("shows a compact breadcrumb with one file name and a visible close action", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    vi.mocked(fetchFilePreview).mockResolvedValue({
      path: "/Users/hr/workspace/quicksort.py",
      display_path: "quicksort.py",
      language: "python",
      content: "print('ok')",
      truncated: false,
    });

    render(
      <FilePreviewPanel
        sessionKey="websocket:chat-1"
        path="quicksort.py"
        token="tok"
        onClose={onClose}
      />,
    );

    const codeBlock = await screen.findByTestId("mock-code-block");
    expect(codeBlock).toHaveTextContent("print('ok')");
    expect(codeBlock).toHaveAttribute("data-language", "python");
    expect(codeBlock).toHaveAttribute("data-highlight", "true");
    expect(screen.getByTestId("file-preview-breadcrumb")).toHaveTextContent("...");
    expect(screen.getByTestId("file-preview-breadcrumb")).toHaveTextContent("workspace");
    expect(screen.getByTestId("file-preview-title")).toHaveTextContent("quicksort.py");
    expect(screen.getAllByText("quicksort.py")).toHaveLength(1);

    const closeButton = screen.getByRole("button", { name: "Close file preview" });
    expect(closeButton).toBeVisible();

    await user.click(closeButton);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("updates translated chrome without refetching the open file", async () => {
    vi.mocked(fetchFilePreview).mockResolvedValue({
      path: "/workspace/notes.md",
      display_path: "notes.md",
      language: "markdown",
      content: "# Notes",
      truncated: false,
    });

    render(
      <FilePreviewPanel
        sessionKey="websocket:chat-1"
        path="notes.md"
        token="tok"
        onClose={() => {}}
      />,
    );

    await screen.findByTestId("mock-code-block");
    expect(fetchFilePreview).toHaveBeenCalledTimes(1);

    await act(async () => {
      await setAppLanguage("zh-CN");
    });

    expect(fetchFilePreview).toHaveBeenCalledTimes(1);
  });
});
