import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PreviewSettings } from "@/preview/PreviewSettings";

describe("Preview memory", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("searches and filters clearly labelled sample memories without business requests", () => {
    const fetchMock = vi.fn(() => { throw new Error("Offline preview must not request business data"); });
    vi.stubGlobal("fetch", fetchMock);
    render(<PreviewSettings initialSection="memory" />);

    expect(screen.getByText("示例记忆 · 仅在当前页面演示")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "示例记忆列表" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索示例记忆" }), {
      target: { value: "回复" },
    });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(within(list).getByText("回复先给结论，需要时再展开说明。")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "筛选记忆分类" }), {
      target: { value: "fact" },
    });
    expect(screen.getByText("没有找到匹配的示例")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(within(screen.getByRole("list", { name: "示例记忆列表" })).getAllByRole("listitem")).toHaveLength(5);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("switches to conversation records and keeps the memory switch local", () => {
    const fetchMock = vi.fn(() => { throw new Error("Offline preview must not request business data"); });
    vi.stubGlobal("fetch", fetchMock);
    render(<PreviewSettings initialSection="memory" />);

    const toggle = screen.getByRole("switch", { name: "自动记住有用的信息（示例）" });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "false");

    fireEvent.click(screen.getByRole("tab", { name: "对话记录" }));
    expect(screen.getByRole("tab", { name: "对话记录" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("整理每周阅读清单")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索示例记忆" }), {
      target: { value: "不存在的内容" },
    });
    expect(screen.getByText("没有找到匹配的示例")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
