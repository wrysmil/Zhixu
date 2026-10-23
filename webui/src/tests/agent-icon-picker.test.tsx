import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AgentGlyph } from "@/components/ui/agent-glyph";
import { AgentIconPicker } from "@/components/ui/agent-icon-picker";
import { DEFAULT_AGENT_ICON } from "@/lib/agents/icons";

function PickerHarness() {
  const [value, setValue] = useState("");
  return (
    <>
      <AgentIconPicker value={value} onChange={setValue} />
      <output data-testid="picked-icon">{value}</output>
    </>
  );
}

describe("AgentGlyph", () => {
  it("renders lucide tokens, emoji, image paths and the default fallback", () => {
    const { container, rerender } = render(<AgentGlyph icon="lucide:rocket" />);
    expect(container.querySelector("svg")).not.toBeNull();

    rerender(<AgentGlyph icon="🦊" />);
    expect(container).toHaveTextContent("🦊");

    rerender(<AgentGlyph icon="/icons/custom.svg" />);
    expect(container.querySelector("img")).toHaveAttribute("src", "/icons/custom.svg");

    rerender(<AgentGlyph icon="" />);
    expect(container.querySelector("img")).toHaveAttribute("src", DEFAULT_AGENT_ICON);
  });
});

describe("AgentIconPicker", () => {
  it("stores lucide icon tokens and still offers emoji", async () => {
    const user = userEvent.setup();
    render(<PickerHarness />);

    await user.click(screen.getByRole("button", { name: "Pick an icon" }));
    await user.click(screen.getByRole("button", { name: "rocket" }));
    expect(screen.getByTestId("picked-icon")).toHaveTextContent("lucide:rocket");

    await user.click(screen.getByRole("button", { name: "Pick an icon" }));
    await user.click(screen.getByRole("tab", { name: "Emoji" }));
    await user.click(screen.getByRole("button", { name: "🦊" }));
    expect(screen.getByTestId("picked-icon")).toHaveTextContent("🦊");
  });
});
