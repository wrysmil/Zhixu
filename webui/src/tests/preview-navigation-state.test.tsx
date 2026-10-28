import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("@/preview/fixtures", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/preview/fixtures")>();
  return {
    ...original,
    previewSessions: Array.from({ length: 170 }, (_, index) => ({
      key: `overflow-${index}`, title: `Preview overflow ${index}`, preview: "",
    })),
  };
});

import PreviewApp from "@/preview/PreviewApp";

describe("PreviewApp persistent navigation state", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "#/");
  });

  it("keeps the selected overflow conversation row mounted while another page is visible", async () => {
    const original = window.matchMedia;
    vi.stubGlobal("matchMedia", (query: string) => ({
      ...original.call(window, query), matches: false,
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    window.history.replaceState(null, "", "#/chat/overflow-169");
    render(<PreviewApp />);
    const column = screen.getByTestId("sidebar-chat-column");
    const selected = await within(column).findByRole("button", { name: "Preview overflow 169", exact: true });
    const rows = Array.from(column.querySelectorAll("[data-chat-row]"));
    fireEvent.click(screen.getByRole("button", { name: "Creative" }));
    await waitFor(() => expect(window.location.hash).toBe("#/creative"));
    expect(selected).toBeInTheDocument();
    expect(within(column).getByRole("button", { name: "Preview overflow 169", exact: true, hidden: true })).toBe(selected);
    expect(Array.from(column.querySelectorAll("[data-chat-row]"))).toEqual(rows);
    fireEvent.click(screen.getByRole("button", { name: "Assistant" }));
    expect(within(column).getByRole("button", { name: "Preview overflow 169", exact: true })).toBe(selected);
  });
});
