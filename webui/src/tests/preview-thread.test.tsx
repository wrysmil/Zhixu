import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { PreviewThread } from "@/preview/PreviewThread";

function forbidTransports() {
  const originalFetch = globalThis.fetch;
  Object.defineProperty(globalThis, "fetch", {
    value: () => {
      throw new Error("preview thread must not issue business requests");
    },
    configurable: true,
  });
  return () => {
    Object.defineProperty(globalThis, "fetch", {
      value: originalFetch,
      configurable: true,
    });
  };
}

describe("PreviewThread", () => {
  it("renders the seeded demo conversation without touching the network", async () => {
    const restore = forbidTransports();
    try {
      render(<PreviewThread chatKey="demo-1" />);
      expect(
        screen.getByText("帮我把这周的记忆整理成一篇周回顾。"),
      ).toBeInTheDocument();
      expect(await screen.findByText("本周回顾")).toBeInTheDocument();
    } finally {
      restore();
    }
  });

  it("appends the user turn and a fixed demo reply on send", () => {
    const restore = forbidTransports();
    try {
      render(<PreviewThread />);
      const input = screen.getByRole("textbox", { name: "Message input" });
      fireEvent.change(input, { target: { value: "再追加一条演示消息" } });
      fireEvent.click(screen.getByRole("button", { name: "Send message" }));

      expect(screen.getByText("再追加一条演示消息")).toBeInTheDocument();
      expect(
        screen.getByText((content) => content.includes("（演示回复）")),
      ).toBeInTheDocument();
    } finally {
      restore();
    }
  });
});
