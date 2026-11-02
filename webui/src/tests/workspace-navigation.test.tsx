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

const RAIL_ITEMS = [
  "Assistant",
  "Agents",
  "Memory",
  "Apps",
  "Skills",
  "Automations",
  "Teams",
  "Identity",
  "Settings",
];

describe("WorkspaceNavigation", () => {
  it("renders a single named navigation landmark with every destination on the rail", () => {
    renderNav();
    const landmarks = screen.getAllByRole("navigation");
    expect(landmarks).toHaveLength(1);
    expect(landmarks[0]).toHaveAccessibleName("Sidebar navigation");

    for (const label of RAIL_ITEMS) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "More" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Home" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Creative" })).not.toBeInTheDocument();
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
  ] as const)("navigates to %s straight from the rail", (label, view) => {
    const { onNavigate } = renderNav();
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(onNavigate).toHaveBeenCalledWith({ view });
  });

  it("marks the active view item with aria-current=page", () => {
    renderNav({ activeView: "agents" });
    expect(
      screen.getByRole("button", { name: "Agents" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("button", { name: "Assistant" }),
    ).not.toHaveAttribute("aria-current");
  });

  it.each(["identity", "teams", "apps", "skills", "automations"] as const)(
    "marks the matching rail item as current on the %s view",
    (activeView) => {
      renderNav({ activeView });
      const label = {
        identity: "Identity",
        teams: "Teams",
        apps: "Apps",
        skills: "Skills",
        automations: "Automations",
      }[activeView];
      expect(screen.getByRole("button", { name: label })).toHaveAttribute(
        "aria-current",
        "page",
      );
      expect(screen.getByRole("button", { name: "Assistant" })).not.toHaveAttribute(
        "aria-current",
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

    fireEvent.click(item);
    expect(onNavigate).toHaveBeenCalledWith({ view: activeView });
  });

  it("marks Settings as current for the settings view", () => {
    renderNav({ activeView: "settings" });
    expect(screen.getByRole("button", { name: "Settings" })).toHaveAttribute("aria-current", "page");
  });

  it("keeps every entry reachable when the rail is collapsed", () => {
    renderNav({ collapsed: true });
    for (const label of RAIL_ITEMS) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("keeps the chat-context column interactive on every menu and inert only when collapsed", () => {
    const chatColumn = <div data-testid="chat-slot">chat column</div>;
    const { rerender } = render(
      <WorkspaceNavigation
        activeView="memory"
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

    rerender(
      <WorkspaceNavigation
        activeView="memory"
        collapsed
        preview
        onNavigate={vi.fn()}
        onToggleCollapsed={vi.fn()}
        chatNavigation={chatColumn}
      />,
    );
    expect(screen.getByTestId("chat-slot")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-chat-column")).toHaveAttribute("inert");
    expect(screen.getByTestId("sidebar-chat-column")).toHaveAttribute("aria-hidden", "true");
  });

  it("keeps chat-local state and focus while switching menus", () => {
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
    expect(screen.getByRole("textbox", { name: "Session filter" })).toHaveValue("remember this filter");
    expect(input).toHaveFocus();
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
    rerender(<WorkspaceNavigation {...props} activeView="memory" newChatActive={false} />);
    expect(screen.getByTestId("sidebar-rail-brand")).toBe(brand);
    expect(screen.getByTestId("sidebar-rail-toggle")).toBe(toggle);
    expect(toggle).toBeEnabled();
    expect(screen.getByTestId("actions-selection-highlight")).not.toHaveAttribute("data-active-id", "new-chat");
  });

  it("shows the preview badge only in preview mode and calls the collapse toggle", () => {
    const preview = renderNav({ preview: true, activeView: "agents" });
    expect(
      preview.container.querySelector("[data-preview-badge]"),
    ).not.toBeNull();
    preview.unmount();

    const live = renderNav({ preview: false });
    expect(live.container.querySelector("[data-preview-badge]")).toBeNull();
    fireEvent.click(within(live.container).getByRole("button", { name: "Collapse sidebar" }));
    expect(live.onToggleCollapsed).toHaveBeenCalledTimes(1);
    live.unmount();

    const capability = renderNav({ preview: false, activeView: "agents" });
    const toggle = within(capability.container).getByTestId("sidebar-rail-toggle");
    expect(toggle).toBeEnabled();
    fireEvent.click(toggle);
    expect(capability.onToggleCollapsed).toHaveBeenCalledTimes(1);
  });
});
