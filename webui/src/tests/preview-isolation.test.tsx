import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import PreviewApp from "@/preview/PreviewApp";

// The preview must never talk to the backend. Making the business transports
// throw means any accidental fetch/WebSocket call surfaces as a render failure
// instead of silently hitting the network.
function forbidTransports() {
  const originalFetch = globalThis.fetch;
  const originalWebSocket = (globalThis as { WebSocket?: unknown }).WebSocket;
  const throwNoNetwork = () => {
    throw new Error("preview must not issue business requests");
  };
  Object.defineProperty(globalThis, "fetch", {
    value: throwNoNetwork,
    configurable: true,
  });
  Object.defineProperty(globalThis, "WebSocket", {
    value: throwNoNetwork,
    configurable: true,
  });
  return () => {
    Object.defineProperty(globalThis, "fetch", {
      value: originalFetch,
      configurable: true,
    });
    Object.defineProperty(globalThis, "WebSocket", {
      value: originalWebSocket,
      configurable: true,
    });
  };
}

describe("PreviewApp isolation", () => {
  let restore: (() => void) | null = null;

  beforeEach(() => {
    restore = forbidTransports();
  });

  afterEach(() => {
    restore?.();
    restore = null;
  });

  it("opens the assistant with transports disabled and no home entry", () => {
    expect(() => render(<PreviewApp />)).not.toThrow();
    expect(screen.getByRole("navigation")).toBeInTheDocument();
    expect(screen.getByText("知序")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Home" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message input" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose project" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add project" })).toBeInTheDocument();
  });

  it("switches the preview theme locally without using business transports", () => {
    render(<PreviewApp />);
    fireEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(document.documentElement).toHaveClass("dark");
    // Memory moved from the More popover to the primary rail; the point of this
    // test is that navigation never touches the transports, not where it lives.
    fireEvent.click(screen.getByRole("button", { name: "Memory" }));
    expect(document.documentElement).toHaveClass("dark");
    fireEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("keeps the navigation current when changing sections inside preview settings", () => {
    // Memory and Identity moved out of settings into standalone views; the
    // settings preview now starts on the Models section, which still exists.
    window.history.replaceState(null, "", "/?preview=1#/settings?section=models");
    try {
      render(<PreviewApp />);
      expect(screen.getByRole("button", { name: "Settings" })).toHaveAttribute("aria-current", "page");
      const settingsNav = screen.getByRole("navigation", { name: "Settings sections" });
      fireEvent.click(within(settingsNav).getByRole("button", { name: "外观" }));
      expect(window.location.hash).toBe("#/settings?section=appearance");
      expect(screen.getByRole("button", { name: "Settings" })).toHaveAttribute("aria-current", "page");
    } finally {
      window.history.replaceState(null, "", "/");
    }
  });

  it("shares settings and sidebar appearance without reading or saving real preferences", () => {
    localStorage.setItem("zhixu-webui.appearance", "real-user-preferences");
    const before = localStorage.getItem("zhixu-webui.appearance");
    const writes = vi.spyOn(localStorage, "setItem");
    const reads = vi.spyOn(localStorage, "getItem");
    window.history.replaceState(null, "", "/?preview=1#/settings?section=appearance");
    try {
      render(<PreviewApp />);
      expect(screen.getByRole("combobox", { name: "Theme" })).toHaveValue("zhixu");
      expect(screen.getByText("Preview only. Appearance changes stay in this session and do not change your saved preferences.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
      expect(screen.getByRole("button", { name: "Switch to light theme" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));
      expect(screen.getByRole("radio", { name: "Light" })).toBeChecked();
      fireEvent.change(screen.getByRole("textbox", { name: "Accent HEX" }), { target: { value: "#0169CC" } });
      expect(reads.mock.calls.some(([key]) => key === "zhixu-webui.appearance")).toBe(false);
      expect(writes.mock.calls.some(([key]) => key === "zhixu-webui.appearance" || key === "zhixu-webui.theme")).toBe(false);
    } finally {
      reads.mockRestore();
      writes.mockRestore();
      expect(localStorage.getItem("zhixu-webui.appearance")).toBe(before);
      localStorage.removeItem("zhixu-webui.appearance");
      window.history.replaceState(null, "", "/");
    }
  });
});
