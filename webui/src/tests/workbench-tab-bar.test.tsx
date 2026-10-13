import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  WorkbenchTabBar,
  type WorkbenchTabBarItem,
} from "@/components/workbench/WorkbenchTabBar";

const tabs: WorkbenchTabBarItem[] = [
  { tabKey: "tab:a", rowKey: "chat-a", title: "你好" },
  { tabKey: "tab:b", rowKey: "tab:b", title: "Curie" },
];

describe("WorkbenchTabBar", () => {
  it("renders one chip per tab and marks the active one", () => {
    render(
      <WorkbenchTabBar
        tabs={tabs}
        activeTabKey="tab:b"
        onSelectTab={() => {}}
        onNewChat={() => {}}
      />,
    );
    expect(screen.getByTestId("workbench-tab-bar")).toBeInTheDocument();
    expect(screen.getByText("你好")).toBeInTheDocument();
    expect(screen.getByText("Curie").closest("button")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("selects tabs by rowKey and creates chats via the plus button", () => {
    const onSelectTab = vi.fn();
    const onNewChat = vi.fn();
    render(
      <WorkbenchTabBar
        tabs={tabs}
        activeTabKey="tab:a"
        onSelectTab={onSelectTab}
        onNewChat={onNewChat}
      />,
    );
    fireEvent.click(screen.getByText("Curie"));
    expect(onSelectTab).toHaveBeenCalledWith("tab:b");
    fireEvent.click(screen.getByLabelText("New chat"));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("renders nothing without tabs", () => {
    const { container } = render(
      <WorkbenchTabBar
        tabs={[]}
        activeTabKey={null}
        onSelectTab={() => {}}
        onNewChat={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
