import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  HomeStartPanel,
  resolveStartPanelShortcut,
  type StartPanelFocus,
} from "@/components/home/HomeStartPanel";
import { fetchFilePreview, fetchWorkspaceFiles } from "@/lib/api";
import type { FilePreviewPayload, SessionDirectoryPayload } from "@/lib/types";

const directoryPayload: SessionDirectoryPayload = {
  path: "",
  display_path: "workspace",
  project_path: "/tmp/project",
  parent_path: null,
  truncated: false,
  entries: [
    { name: "README.md", path: "README.md", display_path: "README.md", is_dir: false, size: 12 },
    { name: "src", path: "src", display_path: "src", is_dir: true, size: 0 },
  ],
};

const previewPayload: FilePreviewPayload = {
  path: "/tmp/project/README.md",
  display_path: "README.md",
  project_path: "/tmp/project",
  language: "markdown",
  content: "# Hello from preview",
  size: 21,
  truncated: false,
};

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function keyEvent(overrides: {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
}) {
  const preventDefault = vi.fn();
  return {
    event: { ...overrides, preventDefault, defaultPrevented: false } as unknown as globalThis.KeyboardEvent,
    preventDefault,
  };
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("no_default_workspace=1")) {
        return jsonResponse({ error: "no_default_workspace" }, false, 404);
      }
      if (url.includes("/file-preview")) {
        return jsonResponse(previewPayload);
      }
      return jsonResponse(directoryPayload);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchWorkspaceFiles", () => {
  it("requests the workspace files endpoint with scope=default and no path for the root", async () => {
    await fetchWorkspaceFiles("tok");
    expect(fetch).toHaveBeenCalledWith(
      "/api/workspace/files?scope=default",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
        credentials: "same-origin",
      }),
    );
  });

  it("passes a percent-encoded relative path when given", async () => {
    await fetchWorkspaceFiles("tok", "src/app.ts");
    expect(fetch).toHaveBeenCalledWith(
      "/api/workspace/files?scope=default&path=src%2Fapp.ts",
      expect.anything(),
    );
  });

  it("returns the session-directory-shaped payload", async () => {
    const payload = await fetchWorkspaceFiles("tok");
    expect(payload.entries.map((entry) => entry.name)).toEqual(["README.md", "src"]);
  });
});

describe("fetchFilePreview scope pass-through", () => {
  it("appends the scope parameter when one is given (home/workspace anchor)", async () => {
    await fetchFilePreview("tok", "websocket:home", "README.md", "", "default");
    expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/websocket%3Ahome/file-preview?path=README.md&scope=default",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
        credentials: "same-origin",
      }),
    );
  });

  it("omits the scope parameter when none is given (session anchor)", async () => {
    await fetchFilePreview("tok", "websocket:chat-1", "notes/ready.md");
    expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/websocket%3Achat-1/file-preview?path=notes%2Fready.md",
      expect.anything(),
    );
  });
});

describe("resolveStartPanelShortcut", () => {
  it("maps meta+p to files only in the home (empty-state) view", () => {
    const { event } = keyEvent({ key: "p", metaKey: true });
    expect(resolveStartPanelShortcut(event, true)).toBe("files");
    expect(resolveStartPanelShortcut(event, false)).toBeNull();
  });

  it("maps ctrl+t to browser as well", () => {
    const { event } = keyEvent({ key: "t", ctrlKey: true });
    expect(resolveStartPanelShortcut(event, true)).toBe("browser");
  });

  it("ignores modified or unrelated keys", () => {
    expect(resolveStartPanelShortcut(keyEvent({ key: "p", metaKey: true, shiftKey: true }).event, true)).toBeNull();
    expect(resolveStartPanelShortcut(keyEvent({ key: "t", metaKey: true, altKey: true }).event, true)).toBeNull();
    expect(resolveStartPanelShortcut(keyEvent({ key: "k", metaKey: true }).event, true)).toBeNull();
    expect(resolveStartPanelShortcut(keyEvent({ key: "p" }).event, true)).toBeNull();
  });
});

function renderPanel(
  props: Partial<React.ComponentProps<typeof HomeStartPanel>> = {},
) {
  return render(
    <HomeStartPanel
      getToken={() => "tok"}
      focus={null}
      {...props}
    />,
  );
}

describe("HomeStartPanel", () => {
  it("renders the three data-driven cards with icon, title, description and shortcut", () => {
    renderPanel();
    expect(screen.getByRole("button", { name: /Workspace files/ })).toBeInTheDocument();
    expect(screen.getByText("Browse files in the session workspace")).toBeInTheDocument();
    expect(screen.getByText("⌘P")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /New terminal/ })).toBeInTheDocument();
    expect(screen.getByText("Run commands in the session workspace")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Browser/ })).toBeInTheDocument();
    expect(screen.getByText("Browse the web")).toBeInTheDocument();
    expect(screen.getByText("⌘T")).toBeInTheDocument();
  });

  it("expands the files card into the workspace file tree", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Workspace files/ }));
    expect(await screen.findByText("README.md")).toBeInTheDocument();
    expect(screen.getByText("src")).toBeInTheDocument();
  });

  it("previews a file entry through the scoped file-preview endpoint", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Workspace files/ }));
    fireEvent.click(await screen.findByRole("button", { name: /README\.md/ }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/file-preview?path=README.md&scope=default"),
      expect.anything(),
    ));
    expect(
      await screen.findByText("# Hello from preview", { exact: false }),
    ).toBeInTheDocument();
  });

  it("closes the inline preview and keeps the tree usable", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Workspace files/ }));
    fireEvent.click(await screen.findByRole("button", { name: /README\.md/ }));
    const close = await screen.findByRole("button", { name: /Close file preview/i });
    fireEvent.click(close);
    expect(screen.queryByText("# Hello from preview")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /README\.md/ })).toBeInTheDocument();
  });

  it("shows the pick-a-project empty state when the workspace 404s", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ error: "no_default_workspace" }, false, 404)),
    );
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Workspace files/ }));
    expect(
      await screen.findByText(/Available after you pick a project/i),
    ).toBeInTheDocument();
  });

  it("renders the terminal slot when provided", async () => {
    renderPanel({ terminalSlot: <div data-testid="terminal-slot" /> });
    fireEvent.click(screen.getByRole("button", { name: /New terminal/ }));
    expect(await screen.findByTestId("terminal-slot")).toBeInTheDocument();
  });

  it("shows a placeholder in the browser expansion when no slot is wired", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Browser/ }));
    expect(
      await screen.findByText(/embedded browser will appear here/i),
    ).toBeInTheDocument();
  });

  it("collapses to a rail and reopens via the focus request", async () => {
    const focus: StartPanelFocus = { section: "terminal", nonce: 1 };
    const { rerender } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Collapse/i }));
    expect(screen.queryByRole("button", { name: /New terminal/ })).not.toBeInTheDocument();

    rerender(
      <HomeStartPanel getToken={() => "tok"} focus={focus} />,
    );
    expect(
      await screen.findByText(/command console will appear here/i),
    ).toBeInTheDocument();
  });

  it("opens the requested section from a focus request", async () => {
    const focus: StartPanelFocus = { section: "files", nonce: 7 };
    renderPanel({ focus });
    expect(await screen.findByText("README.md")).toBeInTheDocument();
  });

  it("retries the workspace probe after an error", async () => {
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async () => {
      if (fail) return jsonResponse({}, false, 500);
      return jsonResponse(directoryPayload);
    }));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Workspace files/ }));
    const retry = await screen.findByRole("button", { name: /Could not load/i });
    fail = false;
    fireEvent.click(retry);
    expect(await screen.findByText("README.md")).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThanOrEqual(2));
  });

});
