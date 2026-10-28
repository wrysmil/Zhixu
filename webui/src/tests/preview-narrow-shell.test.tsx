import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import PreviewApp from "@/preview/PreviewApp";

type Listener = () => void;

/**
 * Drives the `(max-width: 767px)` query the preview shell listens to. Returns a
 * helper that flips the simulated viewport and fires every registered listener.
 */
function stubNarrowViewport(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners = new Set<Listener>();
  const media = {
    get matches() {
      return matches;
    },
    addEventListener: (_: "change", listener: Listener) => listeners.add(listener),
    removeEventListener: (_: "change", listener: Listener) => listeners.delete(listener),
  };
  const original = window.matchMedia;
  vi.stubGlobal("matchMedia", (query: string) => {
    if (query.includes("max-width: 767px")) return media as unknown as MediaQueryList;
    return original.call(window, query);
  });
  return async (nextMatches: boolean) => {
    matches = nextMatches;
    for (const listener of listeners) listener();
    await Promise.resolve();
  };
}

describe("PreviewApp narrow shell", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.location.hash = "";
  });

  it("collapses the chat column and overlays it when the viewport turns narrow", async () => {
    const resize = stubNarrowViewport(false);
    render(<PreviewApp />);

    const search = screen.getByRole("textbox", { name: /search/i });
    expect(search).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close sidebar" })).not.toBeInTheDocument();

    await resize(true);

    await waitFor(() => {
      expect(screen.queryByRole("textbox", { name: /search/i })).not.toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /sidebar|收起|collapse/i }));
    expect(screen.getByRole("button", { name: "Close sidebar" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close sidebar" }));
    expect(screen.queryByRole("textbox", { name: /search/i })).not.toBeInTheDocument();
  });

  it("closes the overlay after navigating on a narrow viewport", async () => {
    stubNarrowViewport(true);
    render(<PreviewApp />);

    fireEvent.click(screen.getByRole("button", { name: /sidebar|收起|collapse/i }));
    expect(screen.getByRole("button", { name: "Close sidebar" })).toBeInTheDocument();

    fireEvent.click(
      within(screen.getAllByRole("navigation")[0]).getAllByRole("button", {
        name: /Memory|记忆/i,
      })[0],
    );
    expect(screen.queryByRole("button", { name: "Close sidebar" })).not.toBeInTheDocument();
  });

  it("restores the desktop session filter and collapse preference after switching views", () => {
    stubNarrowViewport(false);
    render(<PreviewApp />);
    const search = screen.getByRole("textbox", { name: /search/i });
    fireEvent.change(search, { target: { value: "论文" } });
    fireEvent.click(screen.getByRole("button", { name: "Memory" }));
    expect(screen.queryByRole("textbox", { name: /search/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Assistant" }));
    expect(screen.getByRole("textbox", { name: /search/i })).toHaveValue("论文");
    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    fireEvent.click(screen.getByRole("button", { name: "Memory" }));
    fireEvent.click(screen.getByRole("button", { name: "Assistant" }));
    expect(screen.queryByRole("textbox", { name: /search/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Toggle sidebar" })).toBeEnabled();
  });

  it("restores the selected preview session when returning to Assistant", async () => {
    stubNarrowViewport(false);
    window.location.hash = "#/chat/demo-2";
    render(<PreviewApp />);
    fireEvent.click(screen.getByRole("button", { name: "Memory" }));
    fireEvent.click(screen.getByRole("button", { name: "Assistant" }));
    await waitFor(() => expect(window.location.hash).toContain("demo-2"));
  });
});
