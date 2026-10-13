import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { WorkspaceNavigation } from "@/components/workspace/WorkspaceNavigation";
import type { WorkspaceNavigationProps } from "@/components/workspace/contracts";

function renderNav(overrides: Partial<WorkspaceNavigationProps> = {}) {
  const props: WorkspaceNavigationProps = {
    activeView: "chat",
    collapsed: false,
    preview: false,
    onNavigate: vi.fn(),
    onToggleCollapsed: vi.fn(),
    ...overrides,
  };
  const utils = render(<WorkspaceNavigation {...props} />);
  return {
    ...utils,
    onNavigate: props.onNavigate as ReturnType<typeof vi.fn>,
    onToggleCollapsed: props.onToggleCollapsed as ReturnType<typeof vi.fn>,
  };
}

const NAV_ITEMS = [
  "Assistant",
  "Creative",
  "Agents",
  "Skills",
  "Automations",
  "Apps",
  "Settings",
];

describe("WorkspaceNavigation", () => {
  it("renders a single named navigation landmark with every nav entry", () => {
    renderNav();
    const landmarks = screen.getAllByRole("navigation");
    expect(landmarks).toHaveLength(1);
    expect(landmarks[0]).toHaveAccessibleName("Sidebar navigation");

    for (const label of NAV_ITEMS) {
      expect(
        screen.getByRole("button", { name: label }),
      ).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "Home" })).not.toBeInTheDocument();
  });

  it("marks the active view item with aria-current=page", () => {
    renderNav({ activeView: "creative" });
    expect(
      screen.getByRole("button", { name: "Creative" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("button", { name: "Assistant" }),
    ).not.toHaveAttribute("aria-current");
  });

  it("navigates with the matching route when an entry is clicked", () => {
    const { onNavigate } = renderNav();
    fireEvent.click(screen.getByRole("button", { name: "Assistant" }));
    expect(onNavigate).toHaveBeenCalledWith({ view: "chat" });
  });

  it("keeps nav entries reachable and collapses the rail when collapsed", () => {
    renderNav({ collapsed: true });
    // Collapsed items are still labelled for assistive tech via aria-label.
    for (const label of NAV_ITEMS) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("only shows the chat-context column on the assistant view", () => {
    const chatColumn = <div data-testid="chat-slot">chat column</div>;
    const { rerender } = render(
      <WorkspaceNavigation
        activeView="creative"
        collapsed={false}
        preview
        onNavigate={vi.fn()}
        onToggleCollapsed={vi.fn()}
        chatNavigation={chatColumn}
      />,
    );
    expect(screen.queryByTestId("chat-slot")).not.toBeInTheDocument();

    rerender(
      <WorkspaceNavigation
        activeView="chat"
        collapsed={false}
        preview
        onNavigate={vi.fn()}
        onToggleCollapsed={vi.fn()}
        chatNavigation={chatColumn}
      />,
    );
    expect(screen.getByTestId("chat-slot")).toBeInTheDocument();
  });

  it("shows the preview badge only in preview mode and calls the collapse toggle", () => {
    const preview = renderNav({ preview: true, activeView: "creative" });
    expect(
      preview.container.querySelector("[data-preview-badge]"),
    ).not.toBeNull();
    preview.unmount();

    const live = renderNav({ preview: false });
    expect(live.container.querySelector("[data-preview-badge]")).toBeNull();
    fireEvent.click(
      within(live.container).getByRole("button", { name: "Collapse sidebar" }),
    );
    expect(live.onToggleCollapsed).toHaveBeenCalledTimes(1);
  });
});
