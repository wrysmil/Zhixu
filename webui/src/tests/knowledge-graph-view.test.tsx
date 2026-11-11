import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { GraphView } from "@/components/workspace/knowledge/GraphView";
import i18n from "@/i18n";
import type { KnowledgeGraphNode } from "@/lib/api";

beforeEach(() => {
  void i18n.changeLanguage("en");
});

describe("GraphView", () => {
  it("draws edges with real coordinates", async () => {
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
      expect(document.querySelectorAll("line").length).toBe(1);
    });
    const line = document.querySelector("line")!;
    expect(Number(line.getAttribute("x1"))).not.toBeNaN();
    expect(Number(line.getAttribute("x2"))).not.toBeNaN();
    expect(line.getAttribute("stroke")).toBe("currentColor");
  });

  it("caps nodes and shows a truncation notice", async () => {
    const nodes: KnowledgeGraphNode[] = Array.from({ length: 80 }, (_, index) => ({
      id: `e${index}`,
      name: `node-${index}`,
      type: "concept",
      mentionCount: 1,
    }));
    render(<GraphView nodes={nodes} edges={[]} centerId="e0" />);
    expect(await screen.findByText(/Truncated/)).toBeTruthy();
    await waitFor(() => {
      expect(document.querySelectorAll("[data-graph-node]").length).toBe(50);
    });
  });
});
