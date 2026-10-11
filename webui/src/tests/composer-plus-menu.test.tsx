import {
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ComposerPlusMenu } from "@/components/thread/ComposerPlusMenu";
import type { AgentProfile } from "@/lib/agents/types";
import type { CliAppInfo, McpPresetInfo, SkillSummary } from "@/lib/types";

const skills = [
  {
    name: "github",
    description: "Use GitHub CLI.",
    source: "builtin",
    available: true,
  } as SkillSummary,
  {
    name: "drafted",
    description: "Disabled skill.",
    source: "workspace",
    available: true,
    enabled: false,
  } as SkillSummary,
];

const plugins = [
  { name: "claude", display_name: "Claude", description: "Coding agent", installed: true } as CliAppInfo,
  { name: "ghostty", display_name: "Ghostty", description: "Not installed", installed: false } as CliAppInfo,
];

const mcpPresets = [
  { name: "context7", display_name: "Context7", description: "Docs MCP", installed: true, configured: true } as McpPresetInfo,
];

const agents = [
  { id: "researcher", name: "Researcher", description: "Deep research", hidden: false } as AgentProfile,
];

function renderMenu(overrides: Partial<React.ComponentProps<typeof ComposerPlusMenu>> = {}) {
  const props = {
    isHero: false,
    skills,
    plugins,
    mcpPresets,
    agents,
    canWorkInProject: true,
    onPickFiles: vi.fn(),
    onWorkInProject: vi.fn(),
    onInsertToken: vi.fn(),
    ...commandProps(),
    ...overrides,
  };
  render(<ComposerPlusMenu {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  return props;
}

function commandProps() {
  return {
    hasSession: true,
    onRunCommand: vi.fn(),
    accessMode: "full" as const,
    canUseFullAccess: true,
    onSetAccessMode: vi.fn(),
    modelPresets: [
      { name: "gpt-x", model: "openai/gpt-x" },
      { name: "oai-mini", model: "openai/oai-mini" },
    ],
    currentModelPreset: "gpt-x",
  };
}

describe("ComposerPlusMenu", () => {
  it("shows the add, skills, plugins, mcp and agents sections", () => {
    renderMenu();
    expect(screen.getByRole("menuitem", { name: /Files and folders/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Work in a project/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Goal/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Plan mode/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Draw/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /github/ })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /drafted/ })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Claude/ })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /Ghostty/ })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Context7/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Researcher/ })).toBeInTheDocument();
  });

  it("inserts tokens for skills, plugins, mcp and agents", () => {
    const props = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /github/ }));
    expect(props.onInsertToken).toHaveBeenCalledWith("$github");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Claude/ }));
    expect(props.onInsertToken).toHaveBeenCalledWith("@claude");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Context7/ }));
    expect(props.onInsertToken).toHaveBeenCalledWith("@context7");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Researcher/ }));
    expect(props.onInsertToken).toHaveBeenCalledWith(
      'Run this with the agent "Researcher": ',
    );
  });

  it("routes file picking and project work to their handlers", () => {
    const props = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Files and folders/ }));
    expect(props.onPickFiles).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Work in a project/ }));
    expect(props.onWorkInProject).toHaveBeenCalled();
  });

  it("hides the project item and disables files when limits are hit", () => {
    renderMenu({ canWorkInProject: false, filesDisabled: true });
    expect(screen.queryByRole("menuitem", { name: /Work in a project/ })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Files and folders/ })).toBeDisabled();
  });

  it("shows empty hints when a section has no items", () => {
    renderMenu({ skills: [], plugins: [], mcpPresets: [], agents: [] });
    expect(screen.getByText("No skills installed")).toBeInTheDocument();
    expect(screen.getByText("No plugins installed")).toBeInTheDocument();
    expect(screen.getByText("No MCP servers configured")).toBeInTheDocument();
    expect(screen.getByText("No agents available")).toBeInTheDocument();
  });

  it("shows the commands group with compact, permission, model and feedback rows", () => {
    renderMenu();
    expect(screen.getByText("Commands")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Compact/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Permissions/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Choose the model/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Feedback/ })).toBeInTheDocument();
  });

  it("runs /compact through onRunCommand and keeps add-section behaviour", () => {
    const props = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Compact/ }));
    expect(props.onRunCommand).toHaveBeenCalledWith("/compact");
  });

  it("disables compact and feedback rows when there is no active session", () => {
    renderMenu({ hasSession: false });
    expect(screen.getByRole("menuitem", { name: /Compact/ })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: /Feedback/ })).toBeDisabled();
  });

  it("opens the permission popup with the current access mode checked", () => {
    const props = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Permissions/ }));
    const restricted = screen.getByRole("menuitemradio", { name: /Default Permission/ });
    const full = screen.getByRole("menuitemradio", { name: /Full Access/ });
    expect(restricted).toHaveAttribute("aria-checked", "false");
    expect(full).toHaveAttribute("aria-checked", "true");
    fireEvent.click(restricted);
    expect(props.onSetAccessMode).toHaveBeenCalledWith("restricted");
  });

  it("disables the full-access option when it is not allowed", () => {
    renderMenu({ canUseFullAccess: false });
    fireEvent.click(screen.getByRole("menuitem", { name: /Permissions/ }));
    expect(screen.getByRole("menuitemradio", { name: /Full Access/ })).toBeDisabled();
  });

  it("opens the model popup with the current preset checked and runs /model on select", () => {
    const props = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Choose the model/ }));
    expect(screen.getByRole("menuitemradio", { name: "gpt-x" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    fireEvent.click(screen.getByRole("menuitemradio", { name: "oai-mini" }));
    expect(props.onRunCommand).toHaveBeenCalledWith("/model oai-mini");
  });

  it("submits multiline feedback as a single-line /feedback command", () => {
    const props = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Feedback/ }));
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "hello\nworld" } });
    const send = screen.getByRole("button", { name: "Send" });
    fireEvent.click(send);
    expect(props.onRunCommand).toHaveBeenCalledWith("/feedback hello ⏎ world");
  });

  it("keeps the popup back button and closes popups without side effects", () => {
    const props = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Permissions/ }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("menuitem", { name: /Compact/ })).toBeInTheDocument();
    expect(props.onSetAccessMode).not.toHaveBeenCalled();
  });

  it("shows an active status dot on the goal row when a goal is active", () => {
    renderMenu({ goalActive: true });
    const goalRow = screen.getByRole("menuitem", { name: /Goal/ });
    expect(within(goalRow).getByTestId("status-dot")).toBeInTheDocument();
  });

  it("hides the status dot by default", () => {
    renderMenu();
    const goalRow = screen.getByRole("menuitem", { name: /Goal/ });
    expect(within(goalRow).queryByTestId("status-dot")).not.toBeInTheDocument();
  });
});
