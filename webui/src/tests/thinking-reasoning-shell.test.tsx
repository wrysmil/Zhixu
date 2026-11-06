import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ThinkingReasoningShell } from "@/components/thread/activity/ThinkingReasoningShell";

function renderShell(expanded: boolean) {
  return render(
    <ThinkingReasoningShell
      active={false}
      expanded={expanded}
      label="Thought"
      viewportRef={() => undefined}
      contentRef={() => undefined}
      fadeTop={false}
      fadeBottom={false}
      onToggle={vi.fn()}
      onScroll={vi.fn()}
    >
      <button type="button">Hidden action</button>
    </ThinkingReasoningShell>,
  );
}

describe("ThinkingReasoningShell", () => {
  it("connects the disclosure to its controlled content", () => {
    renderShell(true);
    const button = screen.getByRole("button", { name: "Thought" });
    expect(button).toHaveAttribute("aria-controls");
    expect(document.getElementById(button.getAttribute("aria-controls")!)).toContainElement(screen.getByRole("button", { name: "Hidden action" }));
  });

  it("announces semantic state without elapsed time", () => {
    render(<ThinkingReasoningShell active expanded={false} hasDetails={false} label="Working for 12s" announcement="Working" viewportRef={null} contentRef={null} fadeTop={false} fadeBottom={false} onToggle={() => {}} onScroll={() => {}}>{null}</ThinkingReasoningShell>);
    expect(screen.getByRole("status")).toHaveTextContent(/^Working$/);
    expect(screen.getByText("Working for 12s")).toBeVisible();
  });

  it("exposes elapsed time outside the live region without announcing each tick", () => {
    const props = { active: true, expanded: false, hasDetails: false, label: "Working", announcement: "Working", viewportRef: null, contentRef: null, fadeTop: false, fadeBottom: false, onToggle: () => {}, onScroll: () => {} };
    const { rerender } = render(<ThinkingReasoningShell {...props} duration="12s">{null}</ThinkingReasoningShell>);
    const elapsed = screen.getByText("· 12s");
    expect(elapsed.closest('[aria-hidden="true"], [role="status"], [aria-live]')).toBeNull();
    rerender(<ThinkingReasoningShell {...props} duration="13s">{null}</ThinkingReasoningShell>);
    expect(screen.getByText("· 13s")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(/^Working$/);
  });

  it.each([
    { expanded: true, hasDetails: true },
    { expanded: false, hasDetails: true },
    { expanded: false, hasDetails: false },
  ])("keeps a visible running indicator with expanded=$expanded and hasDetails=$hasDetails", ({ expanded, hasDetails }) => {
    const props = {
      active: true,
      expanded,
      hasDetails,
      label: "Working for 1m 17s",
      viewportRef: () => undefined,
      contentRef: () => undefined,
      fadeTop: false,
      fadeBottom: false,
      onToggle: vi.fn(),
      onScroll: vi.fn(),
    };
    const { rerender } = render(
      <ThinkingReasoningShell {...props}>
        <span>Completed tool</span>
      </ThinkingReasoningShell>,
    );

    const indicator = screen.getByTestId("activity-running-indicator");
    expect(indicator).toBeVisible();
    expect(indicator).toHaveAttribute("aria-hidden", "true");
    if (!expanded || !hasDetails) expect(indicator).toHaveClass("animate-spin", "motion-reduce:animate-none");
    else expect(indicator).not.toHaveClass("animate-spin");
    expect(indicator.parentElement).toHaveTextContent("Working for 1m 17s");

    rerender(
      <ThinkingReasoningShell {...props} active={false} label="Worked for 1m 18s">
        <span>Completed tool</span>
      </ThinkingReasoningShell>,
    );

    expect(screen.queryByTestId("activity-running-indicator")).not.toBeInTheDocument();
    expect(screen.getByText("Worked for 1m 18s")).toBeVisible();
  });

  it("makes collapsed descendants inert as well as visually hidden", () => {
    const { rerender } = renderShell(false);
    const disclosure = screen.getByRole("button", { name: "Thought" });
    const collapsible = disclosure.nextElementSibling;

    expect(collapsible).toHaveAttribute("inert");
    expect(collapsible).toHaveAttribute("aria-hidden", "true");

    rerender(
      <ThinkingReasoningShell
        active={false}
        expanded
        label="Thought"
        viewportRef={() => undefined}
        contentRef={() => undefined}
        fadeTop={false}
        fadeBottom={false}
        onToggle={vi.fn()}
        onScroll={vi.fn()}
      >
        <button type="button">Hidden action</button>
      </ThinkingReasoningShell>,
    );

    expect(disclosure.nextElementSibling).not.toHaveAttribute("inert");
    expect(disclosure.nextElementSibling).toHaveAttribute("aria-hidden", "false");
  });
});
