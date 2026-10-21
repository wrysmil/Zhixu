import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";

import { AppearanceEditor } from "@/components/settings/appearance/AppearanceEditor";
import { ThemeProvider, useTheme } from "@/hooks/useTheme";

function Editor() {
  const appearance = useTheme({ persist: false });
  return <ThemeProvider theme={appearance.theme} appearance={appearance}><AppearanceEditor /></ThemeProvider>;
}

describe("appearance editor", () => {
  beforeEach(() => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  });

  it("keeps incomplete colors as drafts, shows an error on blur and applies valid HEX without losing focus", () => {
    render(<Editor />);
    const accent = screen.getByRole("textbox", { name: "Accent HEX" });
    fireEvent.focus(accent);
    fireEvent.change(accent, { target: { value: "#12" } });
    expect(accent).toHaveValue("#12");
    fireEvent.blur(accent);
    expect(accent).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Enter a HEX color, such as #0169CC or #09C.")).toBeInTheDocument();
    accent.focus();
    fireEvent.change(accent, { target: { value: "#0169cc" } });
    expect(accent).toHaveFocus();
    expect(screen.getByRole("combobox", { name: "Theme" })).toHaveValue("custom");
    fireEvent.blur(accent);
    expect(accent).toHaveValue("#0169CC");
    expect(accent).toHaveAttribute("aria-invalid", "false");
  });

  it("preserves custom colors and fonts across preset selection and isolates light and dark colors", () => {
    render(<Editor />);
    fireEvent.click(screen.getByRole("radio", { name: "Light" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Accent HEX" }), { target: { value: "#0169CC" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Code font" }), { target: { value: "consolas" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Theme" }), { target: { value: "sand" } });
    expect(screen.getByRole("combobox", { name: "Code font" })).toHaveValue("consolas");
    fireEvent.change(screen.getByRole("combobox", { name: "Theme" }), { target: { value: "custom" } });
    expect(screen.getByRole("textbox", { name: "Accent HEX" })).toHaveValue("#0169CC");
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Accent HEX" }), { target: { value: "#FFAA00" } });
    fireEvent.click(screen.getByRole("radio", { name: "Light" }));
    expect(screen.getByRole("textbox", { name: "Accent HEX" })).toHaveValue("#0169CC");
    fireEvent.click(screen.getByRole("button", { name: "Restore default appearance" }));
    expect(screen.getByRole("radio", { name: "Light" })).toBeChecked();
    expect(screen.getByRole("combobox", { name: "Theme" })).toHaveValue("zhixu");
    expect(screen.getByRole("combobox", { name: "Code font" })).toHaveValue("default");
  });

  it("warns on low contrast while retaining an accessible restore control", () => {
    render(<Editor />);
    fireEvent.change(screen.getByRole("textbox", { name: "Background HEX" }), { target: { value: "#FFFFFF" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Text HEX" }), { target: { value: "#FFFFFF" } });
    expect(screen.getByRole("status")).toHaveTextContent("Low contrast");
    expect(screen.getByRole("button", { name: "Restore default appearance" })).toBeEnabled();
  });

  it("accepts a complete custom family name including spaces, rejects punctuation and retains code fonts", async () => {
    const user = userEvent.setup();
    render(<Editor />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Code font" }), "consolas");
    await user.selectOptions(screen.getByRole("combobox", { name: "Interface font" }), "custom");
    const family = screen.getByRole("textbox", { name: "Interface font family name" });
    await user.clear(family);
    await user.type(family, "Noto Sans SC");
    expect(family).toHaveValue("Noto Sans SC");
    expect(document.documentElement.style.getPropertyValue("--font-ui")).toContain('"Noto Sans SC"');
    await user.type(family, "; color:red");
    await user.tab();
    expect(family).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Use a single font family name without style punctuation.");
    expect(document.documentElement.style.getPropertyValue("--font-ui")).toContain('"Noto Sans SC"');
    expect(screen.getByRole("combobox", { name: "Code font" })).toHaveValue("consolas");
  });

  it("resets a single color and updates the draft without clearing other choices", () => {
    render(<Editor />);
    const accent = screen.getByRole("textbox", { name: "Accent HEX" });
    const original = (accent as HTMLInputElement).value;
    fireEvent.change(accent, { target: { value: "#0169CC" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Background HEX" }), { target: { value: "#FFFFFF" } });
    fireEvent.click(screen.getByRole("button", { name: "Restore Accent theme value" }));
    expect(accent).toHaveValue(original);
    expect(screen.getByRole("textbox", { name: "Background HEX" })).toHaveValue("#FFFFFF");
  });

  it("exposes a storage failure while allowing appearance changes", () => {
    const controller = {
      preferences: { schemaVersion: 1 as const, mode: "light" as const, presetId: "zhixu" as const, custom: null,
        uiFont: { id: "system", family: "" }, codeFont: { id: "default", family: "" } },
      theme: "light" as const, storageError: true, setMode: vi.fn(), setPreset: vi.fn(), setColor: vi.fn(),
      resetColor: vi.fn(), setFont: vi.fn(), resetAppearance: vi.fn(),
    };
    render(<ThemeProvider theme="light" appearance={controller}><AppearanceEditor /></ThemeProvider>);
    expect(screen.getByRole("alert")).toHaveTextContent("Applied for this session, but could not save to this device.");
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    expect(controller.setMode).toHaveBeenCalledWith("dark");
  });
});
