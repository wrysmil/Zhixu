import { act, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CodeBlock } from "@/components/CodeBlock";
import { TerminalSurface } from "@/components/workbench/TerminalSurface";

const mocks = vi.hoisted(() => ({ instances: [] as Array<{ options: Record<string, unknown>; dispose: ReturnType<typeof vi.fn> }>, fit: vi.fn() }));
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    options: Record<string, unknown>;
    dispose = vi.fn();
    cols = 80;
    rows = 24;
    constructor(options: Record<string, unknown>) {
      this.options = { ...options, theme: { ...(options.theme as object), red: "#ff1234" } };
      mocks.instances.push(this);
    }
    open() {}
    write() {}
    scrollToBottom() {}
    loadAddon() {}
    onData() { return { dispose() {} }; }
    onResize() { return { dispose() {} }; }
  },
}));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit = mocks.fit; } }));
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ client: { terminalResize() {} } }) }));
let pty = true;
vi.mock("@/hooks/useZhixuStream", () => ({
  useTerminalStore: () => ({ store: {}, terminals: { byId: { term: { pty, status: "running", lines: [] } } } }),
}));

afterEach(() => {
  document.documentElement.removeAttribute("style");
  mocks.instances.length = 0;
  mocks.fit.mockClear();
});

describe("standalone renderer appearance adapters", () => {
  it.each([true, false])("applies root colors and font to real xterm wrapper (PTY=%s)", (mode) => {
    pty = mode;
    document.documentElement.style.setProperty("--background", "210 25% 12%");
    document.documentElement.style.setProperty("--foreground", "30 20% 92%");
    document.documentElement.style.setProperty("--primary", "210 70% 60%");
    document.documentElement.style.setProperty("--font-code", '"Consolas", monospace');
    const view = render(<TerminalSurface terminalId="term" />);
    const instance = mocks.instances[0]!;
    expect(instance.options.fontFamily).toBe('"Consolas", monospace');
    expect(instance.options.theme).toMatchObject({ background: "hsl(210 25% 12%)", foreground: "hsl(30 20% 92%)", cursor: "hsl(210 70% 60%)", red: "#ff1234" });

    document.documentElement.style.setProperty("--font-code", '"Cascadia Code", monospace');
    document.documentElement.style.setProperty("--accent", "210 40% 25%");
    const fits = mocks.fit.mock.calls.length;
    act(() => window.dispatchEvent(new Event("zhixu-webui.appearance-applied")));
    expect(instance.options.fontFamily).toBe('"Cascadia Code", monospace');
    expect(instance.options.theme).toMatchObject({ selectionBackground: "hsl(210 40% 25%)", red: "#ff1234" });
    expect(mocks.fit.mock.calls.length).toBe(fits + 1);
    expect(mocks.instances).toHaveLength(1);
    view.unmount();
    document.documentElement.style.setProperty("--font-code", "monospace");
    window.dispatchEvent(new Event("zhixu-webui.appearance-applied"));
    expect(instance.options.fontFamily).toBe('"Cascadia Code", monospace');
  });

  it("keeps syntax-highlighted code on the shared code font", async () => {
    const view = render(<CodeBlock language="typescript" code="const answer = 42;" />);
    await waitFor(() => expect(view.container.querySelector("pre")?.style.fontFamily).toBe("var(--font-code)"));
    expect(view.container.querySelector("code")).toHaveTextContent("const answer = 42;");
  });
});
