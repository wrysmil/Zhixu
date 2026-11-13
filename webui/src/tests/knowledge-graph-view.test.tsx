import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GraphView } from "@/components/workspace/knowledge/GraphView";
import i18n from "@/i18n";
import type { KnowledgeGraphNode } from "@/lib/api";

beforeEach(() => {
  void i18n.changeLanguage("en");
});

describe("GraphView", () => {
  it("separates keyboard selection from explicit expansion", async () => {
    const onSelect = vi.fn();
    const onExpand = vi.fn();
    render(<GraphView nodes={[
      { id: "a", name: "Alpha", type: "concept", mentionCount: 1 },
      { id: "b", name: "Beta", type: "concept", mentionCount: 1 },
      { id: "c", name: "Gamma", type: "concept", mentionCount: 1 },
    ]} edges={[{ source: "a", target: "b", relation: "uses", weight: 1 }]}
      centerId="a" onSelect={onSelect} onExpand={onExpand} />);
    const beta = await screen.findByRole("button", { name: "Beta · concept" });
    beta.focus();
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith("b");
    expect(onExpand).not.toHaveBeenCalled();
    expect(beta).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Alpha · concept" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Gamma · concept" })).toHaveAttribute("aria-pressed", "false");
    await userEvent.keyboard(" ");
    expect(onSelect).toHaveBeenCalledTimes(2);
    await userEvent.click(screen.getByRole("button", { name: i18n.t("knowledge.experience.graphExpand") }));
    expect(onExpand).toHaveBeenLastCalledWith("b");
  });

  it("preserves zoom and node identity on equal graph refreshes", async () => {
    const nodes = [{ id: "a", name: "Alpha", type: "concept", mentionCount: 1 }];
    const { rerender } = render(<GraphView nodes={nodes} edges={[]} centerId="a" />);
    const node = await screen.findByRole("button", { name: "Alpha · concept" });
    const svg = screen.getByRole("group", { name: "Neighborhood" });
    fireEvent.wheel(svg, { deltaY: 100 });
    const zoom = svg.getAttribute("viewBox");
    rerender(<GraphView nodes={nodes.map(n => ({ ...n }))} edges={[]} centerId="a" />);
    await waitFor(() => expect(svg).toHaveAttribute("viewBox", zoom));
    expect(screen.getByRole("button", { name: "Alpha · concept" })).toBe(node);
  });

  it("draws directed edges with real coordinates and a flow overlay", async () => {
    render(
      <GraphView
        nodes={[
          { id: "a", name: "A", type: "concept", mentionCount: 1 },
          { id: "b", name: "B", type: "concept", mentionCount: 1 },
        ]}
        edges={[{ source: "a", target: "b", relation: "依赖", weight: 5 }]}
        centerId="a"
      />,
    );
    await waitFor(() => {
      expect(document.querySelectorAll("line.text-primary").length).toBe(1);
    });
    const line = document.querySelector("line.text-primary")!;
    expect(Number(line.getAttribute("x1"))).not.toBeNaN();
    expect(Number(line.getAttribute("x2"))).not.toBeNaN();
    expect(line.getAttribute("stroke")).toBe("currentColor");
    // 有向关系额外绘制一条流向虚线，用于表达「A 指向 B」。
    expect(document.querySelectorAll("line.graph-flow-line").length).toBe(1);
  });

  it("caps nodes at the shared graph limit", async () => {
    const nodes: KnowledgeGraphNode[] = Array.from({ length: 80 }, (_, index) => ({
      id: `e${index}`,
      name: `node-${index}`,
      type: "concept",
      mentionCount: 1,
    }));
    render(<GraphView nodes={nodes} edges={[]} centerId="e0" />);
    await waitFor(() => {
      expect(document.querySelectorAll("[data-force-node]").length).toBe(60);
    });
  });
});
