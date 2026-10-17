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

const RAIL_ITEMS = ["Assistant", "Creative", "More", "Settings"];
const GROUPED_ITEMS = [
  "Apps",
  "Skills",
  "Automations",
  "Agents",
  "Teams",
  "Memory",
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
    ["Agents", "agents"],
    ["Teams", "teams"],
    ["Memory", "memory"],
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

  it.each(["memory", "identity", "teams", "apps", "skills", "automations", "agents"] as const)(
    "marks More as current on the %s view and the entry inside the menu",
    (activeView) => {
      renderNav({ activeView });
      const more = screen.getByRole("button", { name: "More" });
      expect(more).toHaveAttribute("aria-current", "page");

      openMore();
      const menu = screen.getByRole("dialog");
      const label = {
        memory: "Memory",
        identity: "Identity",
        teams: "Teams",
        apps: "Apps",
        skills: "Skills",
        automations: "Automations",
        agents: "Agents",
      }[activeView];
      expect(within(menu).getByRole("button", { name: label })).toHaveAttribute(
        "aria-current",
        "page",
      );
    },
  );

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
