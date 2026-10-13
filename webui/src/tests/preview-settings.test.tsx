import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { PreviewSettings } from "@/preview/PreviewSettings";
import { PreviewCapabilities } from "@/preview/PreviewCapabilities";

function forbidFetch() {
  const original = globalThis.fetch;
  Object.defineProperty(globalThis, "fetch", {
    value: () => {
      throw new Error("preview settings must not issue business requests");
    },
    configurable: true,
  });
  return () => {
    Object.defineProperty(globalThis, "fetch", { value: original, configurable: true });
  };
}

describe("PreviewSettings", () => {
  it("switches sections and gives a local save feedback without the network", () => {
    const restore = forbidFetch();
    try {
      render(<PreviewSettings />);
      // Overview is the default section.
      expect(screen.getByText("当前模型")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "模型" }));
      expect(screen.getByText("API 密钥")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "保存" }));
      expect(screen.getByText("已保存（示例）")).toBeInTheDocument();
    } finally {
      restore();
    }
  });
});

describe("PreviewCapabilities", () => {
  it("renders capability cards with destructive actions disabled in preview", () => {
    const restore = forbidFetch();
    try {
      render(<PreviewCapabilities kind="skills" />);
      expect(screen.getByText("cron")).toBeInTheDocument();
      const install = screen.getByRole("button", { name: "安装（预览已禁用）" });
      expect(install).toBeDisabled();
    } finally {
      restore();
    }
  });
});
