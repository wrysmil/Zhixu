import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/toaster";
import i18n from "@/i18n";
import { notifySaved, notifySaveFailed } from "@/lib/save-feedback";
import { notify, TOAST_EVENT } from "@/lib/toast-events";

describe("global toaster", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders a published message inside a polite live region", () => {
    render(<Toaster />);

    act(() => {
      notify({ message: "Saved", durationMs: 1000 });
    });

    expect(screen.getByText("Saved")).toBeInTheDocument();
    const region = document.querySelector('[aria-live="polite"]');
    expect(region).not.toBeNull();
    expect(region).toHaveAttribute("aria-relevant", "additions text");
  });

  it("removes a toast once its duration elapses", () => {
    render(<Toaster />);

    act(() => {
      notify({ message: "Saved", durationMs: 1000 });
    });
    expect(screen.getByText("Saved")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("keeps at most three toasts on screen", () => {
    render(<Toaster />);

    act(() => {
      for (let index = 1; index <= 4; index += 1) {
        notify({ message: `toast-${index}`, durationMs: 1000 });
      }
    });

    expect(screen.queryByText("toast-1")).not.toBeInTheDocument();
    expect(screen.getByText("toast-2")).toBeInTheDocument();
    expect(screen.getByText("toast-4")).toBeInTheDocument();
  });

  it("stays inline-only when the save still needs an engine restart", () => {
    render(<Toaster />);

    act(() => {
      notifySaved({ restartRequired: true });
    });
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();

    act(() => {
      notifySaved({ restartRequired: false });
    });
    expect(screen.getByText("Saved")).toBeInTheDocument();
  });

  it("reports a failed save as an error toast", () => {
    render(<Toaster />);

    act(() => {
      notifySaveFailed("boom");
    });

    expect(screen.getByText("Save failed: boom")).toBeInTheDocument();
    expect(document.querySelector('[data-toast-tone="error"]')).not.toBeNull();
  });

  it("keeps a stable event name for cross-module publishers", () => {
    expect(TOAST_EVENT).toBe("zhixu:toast");
  });
});
