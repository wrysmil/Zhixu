import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BrowserSection } from "@/components/workbench/BrowserSection";

const IFRAME = "browser-iframe";

function addressInput() {
  return screen.getByRole("textbox", { name: /address/i });
}

function navigateTo(value: string) {
  const input = addressInput();
  fireEvent.change(input, { target: { value } });
  fireEvent.submit(input.closest("form")!);
}

afterEach(() => {
  vi.useRealTimers();
  delete (window as { mira?: unknown }).mira;
  vi.restoreAllMocks();
});

describe("BrowserSection", () => {
  it("loads a valid URL into the hardened iframe", () => {
    render(<BrowserSection />);
    navigateTo("https://example.com");
    const frame = screen.getByTestId(IFRAME);
    expect(frame).toHaveAttribute("src", expect.stringContaining("example.com"));
    // ``allow-same-origin`` must stay off: whitelist checks run only at submit
    // time, and a redirect can land the frame on the gateway origin, letting
    // embedded scripts read the parent page's token.
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-forms allow-popups");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
  });

  it("shows an inline error and keeps the previous src for an invalid URL", () => {
    render(<BrowserSection />);
    navigateTo("https://good.example");
    fireEvent.change(addressInput(), { target: { value: "http://192.168.1.1" } });
    fireEvent.submit(addressInput().closest("form")!);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByTestId(IFRAME)).toHaveAttribute(
      "src",
      expect.stringContaining("good.example"),
    );
  });

  it("walks the history stack with back and forward", () => {
    render(<BrowserSection />);
    navigateTo("https://a.example");
    navigateTo("https://b.example");
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(screen.getByTestId(IFRAME)).toHaveAttribute("src", expect.stringContaining("a.example"));
    fireEvent.click(screen.getByRole("button", { name: /forward/i }));
    expect(screen.getByTestId(IFRAME)).toHaveAttribute("src", expect.stringContaining("b.example"));
  });

  it("shows the embed-blocked fallback after ~4s without a load event", () => {
    vi.useFakeTimers();
    render(<BrowserSection />);
    navigateTo("https://slow.example");
    expect(screen.queryByText(/refuses/i)).not.toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(4100);
    });
    expect(screen.getByText(/refuses/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /open externally/i })).toBeInTheDocument();
  });

  it("does not show the fallback when the iframe loads in time", () => {
    vi.useFakeTimers();
    render(<BrowserSection />);
    navigateTo("https://fast.example");
    fireEvent.load(screen.getByTestId(IFRAME));
    act(() => {
      vi.advanceTimersByTime(4100);
    });
    expect(screen.queryByText(/refuses/i)).not.toBeInTheDocument();
  });

  it("opens externally via window.open when the desktop shell is absent", () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    vi.useFakeTimers();
    render(<BrowserSection />);
    navigateTo("https://blocked.example");
    act(() => {
      vi.advanceTimersByTime(4100);
    });
    fireEvent.click(screen.getByRole("button", { name: /open externally/i }));
    expect(openSpy).toHaveBeenCalledWith(
      expect.stringContaining("blocked.example"),
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("prefers the desktop shell openExternal when available", () => {
    const openExternal = vi.fn();
    (window as { mira?: { openExternal?: typeof openExternal } }).mira = { openExternal };
    render(<BrowserSection />);
    navigateTo("https://blocked.example");
    fireEvent.load(screen.getByTestId(IFRAME));
    fireEvent.click(screen.getByRole("button", { name: /open externally/i }));
    expect(openExternal).toHaveBeenCalledWith(expect.stringContaining("blocked.example"));
  });
});
