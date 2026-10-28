import { useState } from "react";
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

const RAIL_ITEMS = ["Assistant", "Creative", "Agents", "Memory", "More", "Settings"];
const GROUPED_ITEMS = [
  "Apps",
  "Skills",
  "Automations",
  "Teams",
  "Identity",
];

function openMore() {
  fireEvent.click(screen.getByRole("button", { name: "More" }));
}

describe("WorkspaceNavigation", () => {
  it("renders a single named navigation landmark with the top-level entries", () => {
    renderNav();
    const landmarks = screen.getAllByRole("navigation");
    expect(landmarks).toHaveLength(1);
    expect(landmarks[0]).toHaveAccessibleName("Sidebar navigation");

    for (const label of RAIL_ITEMS) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    for (const label of GROUPED_ITEMS) {
      expect(screen.queryByRole("button", { name: label })).not.toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "Home" })).not.toBeInTheDocument();
  });

  it("opens the More menu with grouped legacy routes", () => {
    renderNav();
    openMore();

    const menu = screen.getByRole("dialog");
    expect(within(menu).getByText("Workspace")).toBeInTheDocument();
    expect(within(menu).getByText("AI configuration")).toBeInTheDocument();
    expect(within(menu).getByText("Personal")).toBeInTheDocument();
    for (const label of GROUPED_ITEMS) {
      expect(within(menu).getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("navigates with the matching route when a top-level entry is clicked", () => {
    const { onNavigate } = renderNav();
    fireEvent.click(screen.getByRole("button", { name: "Assistant" }));
    expect(onNavigate).toHaveBeenCalledWith({ view: "chat" });
  });

  it.each([
    ["Teams", "teams"],
    ["Identity", "identity"],
    ["Apps", "apps"],
    ["Skills", "skills"],
    ["Automations", "automations"],
  ] as const)("navigates to %s from the More menu", (label, view) => {
    const { onNavigate } = renderNav();
    openMore();
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(onNavigate).toHaveBeenCalledWith({ view });
  });

  it("marks the active view item with aria-current=page", () => {
    renderNav({ activeView: "creative" });
    expect(
      screen.getByRole("button", { name: "Creative" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("button", { name: "Assistant" }),
    ).not.toHaveAttribute("aria-current");
    expect(
      screen.getByRole("button", { name: "More" }),
    ).not.toHaveAttribute("aria-current");
  });

  it.each(["identity", "teams", "apps", "skills", "automations"] as const)(
    "marks More as current on the %s view and the entry inside the menu",
    (activeView) => {
      renderNav({ activeView });
      const more = screen.getByRole("button", { name: "More" });
      expect(more).toHaveAttribute("aria-current", "page");

      openMore();
      const menu = screen.getByRole("dialog");
      const label = {
        identity: "Identity",
        teams: "Teams",
        apps: "Apps",
        skills: "Skills",
        automations: "Automations",
      }[activeView];
      expect(within(menu).getByRole("button", { name: label })).toHaveAttribute(
        "aria-current",
        "page",
      );
    },
  );

  it.each([
    ["Agents", "agents"],
    ["Memory", "memory"],
  ] as const)("keeps core capability %s visible on the rail", (label, activeView) => {
    const { onNavigate } = renderNav({ activeView });
    const item = screen.getByRole("button", { name: label });
    expect(item).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "More" })).not.toHaveAttribute("aria-current");

    fireEvent.click(item);
    expect(onNavigate).toHaveBeenCalledWith({ view: activeView });
  });

  it("marks Settings as current for the settings view", () => {
    renderNav({ activeView: "settings" });
    expect(screen.getByRole("button", { name: "Settings" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "More" })).not.toHaveAttribute("aria-current");
  });

  it("keeps grouped entries reachable when the rail is collapsed", () => {
    renderNav({ collapsed: true });
    for (const label of RAIL_ITEMS) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    openMore();
    for (const label of GROUPED_ITEMS) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("keeps the chat-context column mounted but inert away from the assistant", () => {
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
    expect(screen.getByTestId("chat-slot")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-chat-column")).toHaveAttribute("inert");
    expect(screen.getByTestId("sidebar-chat-column")).toHaveAttribute("aria-hidden", "true");

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
    expect(screen.getByTestId("sidebar-chat-column")).not.toHaveAttribute("inert");
    expect(screen.getByTestId("sidebar-chat-column")).not.toHaveAttribute("aria-hidden");
  });

  it("preserves chat-local state and moves hidden focus to the active rail item", () => {
    function ChatColumn() {
      const [query, setQuery] = useState("");
      return <input aria-label="Session filter" value={query} onChange={(e) => setQuery(e.target.value)} />;
    }
    const props: WorkspaceNavigationProps = {
      activeView: "chat", collapsed: false, preview: false,
      onNavigate: vi.fn(), onToggleCollapsed: vi.fn(), chatNavigation: <ChatColumn />,
    };
    const { rerender } = render(<WorkspaceNavigation {...props} />);
    const input = screen.getByRole("textbox", { name: "Session filter" });
    fireEvent.change(input, { target: { value: "remember this filter" } });
    input.focus();
    rerender(<WorkspaceNavigation {...props} activeView="memory" />);
    expect(screen.queryByRole("textbox", { name: "Session filter" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Memory" })).toHaveFocus();
    rerender(<WorkspaceNavigation {...props} />);
    expect(screen.getByRole("textbox", { name: "Session filter" })).toHaveValue("remember this filter");
  });

  it("keeps one rail brand and toggle across routes without targeting hidden new-chat actions", () => {
    const props: WorkspaceNavigationProps = {
      activeView: "chat", collapsed: false, preview: false, newChatActive: true,
      onNavigate: vi.fn(), onToggleCollapsed: vi.fn(), chatNavigation: <button>New chat slot</button>,
    };
    const { rerender } = render(<WorkspaceNavigation {...props} />);
    const brand = screen.getByTestId("sidebar-rail-brand");
    const toggle = screen.getByTestId("sidebar-rail-toggle");
    expect(toggle).toBeEnabled();
    rerender(<WorkspaceNavigation {...props} activeView="creative" />);
    expect(screen.getByTestId("sidebar-rail-brand")).toBe(brand);
    expect(screen.getByTestId("sidebar-rail-toggle")).toBe(toggle);
    expect(toggle).toBeDisabled();
    expect(screen.getByTestId("actions-selection-highlight")).not.toHaveAttribute("data-active-id", "new-chat");
  });

  it("shows the preview badge only in preview mode and calls the collapse toggle", () => {
    const preview = renderNav({ preview: true, activeView: "creative" });
    expect(
      preview.container.querySelector("[data-preview-badge]"),
    ).not.toBeNull();
    preview.unmount();

    const live = renderNav({ preview: false });
    expect(live.container.querySelector("[data-preview-badge]")).toBeNull();
    fireEvent.click(within(live.container).getByRole("button", { name: "Collapse sidebar" }));
    expect(live.onToggleCollapsed).toHaveBeenCalledTimes(1);
    live.unmount();

    const creative = renderNav({ preview: false, activeView: "creative" });
    const toggle = within(creative.container).getByTestId("sidebar-rail-toggle");
    expect(toggle).toBeDisabled();
    fireEvent.click(toggle);
    expect(creative.onToggleCollapsed).not.toHaveBeenCalled();
  });
});
