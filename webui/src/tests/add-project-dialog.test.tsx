import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AddProjectDialog } from "@/components/AddProjectDialog";
import { ChatList } from "@/components/ChatList";
import type { ChatSummary } from "@/lib/types";

const { mocks } = vi.hoisted(() => ({
  mocks: {
    requestMutation: vi.fn(),
    fetchWorkspaces: vi.fn(),
    getToken: vi.fn(() => "token-1"),
  },
}));

vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({
    client: { requestMutation: mocks.requestMutation },
    token: "token-1",
    getToken: mocks.getToken,
    modelName: "test-model",
    ingressLimits: { maxUploadBytes: 1 },
  }),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchWorkspaces: mocks.fetchWorkspaces,
}));

function workspacesPayload(canPickFolder: boolean) {
  return {
    schema_version: 1,
    default_access_mode: "restricted",
    default_scope: { project_path: "", project_name: "", access_mode: "restricted", restrict_to_workspace: true },
    controls: { can_change_project: true, can_use_full_access: true, can_pick_folder: canPickFolder },
  };
}

function renderDialog(onConfirm = vi.fn()) {
  const onOpenChange = vi.fn();
  render(
    <AddProjectDialog open onOpenChange={onOpenChange} onConfirm={onConfirm} />,
  );
  return { onConfirm, onOpenChange };
}

const PICKER_BUTTON = "Add a folder from this computer";

/**
 * The picker stays disabled until the gateway answers whether it can pick a
 * folder, so the click has to wait for that answer: clicking a disabled button
 * is a no-op and the pick never happens.
 */
async function clickPicker(): Promise<void> {
  const picker = await screen.findByRole("button", { name: PICKER_BUTTON });
  await waitFor(() => expect(picker).toBeEnabled());
  fireEvent.click(picker);
}

beforeEach(() => {
  mocks.requestMutation.mockReset();
  mocks.fetchWorkspaces.mockReset();
  mocks.getToken.mockReset();
  mocks.getToken.mockReturnValue("token-1");
  mocks.fetchWorkspaces.mockResolvedValue(workspacesPayload(true));
  delete (window as { mira?: unknown }).mira;
});

describe("AddProjectDialog", () => {
  it("disables the folder picker and explains why when the gateway cannot pick folders", async () => {
    mocks.fetchWorkspaces.mockResolvedValue(workspacesPayload(false));
    const { onConfirm } = renderDialog();

    const picker = await screen.findByRole("button", { name: PICKER_BUTTON });
    await waitFor(() => expect(picker).toBeDisabled());
    expect(
      screen.getByText("Open the WebUI on the gateway machine, or use the desktop app"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Add")).not.toBeInTheDocument();

    fireEvent.click(picker);
    expect(mocks.requestMutation).not.toHaveBeenCalled();

    const create = screen.getByRole("button", { name: "Create project" });
    expect(create).toBeDisabled();
    fireEvent.click(create);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("falls back to the gateway picker and submits the picked folder with an inferred name", async () => {
    mocks.requestMutation.mockResolvedValue({ path: "/Users/me/Photos/" });
    const { onConfirm } = renderDialog();

    await clickPicker();
    await waitFor(() =>
      expect(mocks.requestMutation).toHaveBeenCalledWith(
        "workspace.pick_folder",
        {},
        300_000,
      ),
    );

    const picked = await screen.findByTitle("/Users/me/Photos/");
    expect(picked).toHaveTextContent("/Users/me/Photos/");
    expect(screen.getByText("Change folder")).toBeInTheDocument();
    expect(screen.queryByText("Could not open the folder picker")).not.toBeInTheDocument();

    const nameInput = screen.getByLabelText("Project name");
    expect(nameInput).toHaveValue("Photos");
    expect(screen.getByText("Source folder")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(onConfirm).toHaveBeenCalledWith("/Users/me/Photos", "Photos");
  });

  it("keeps a manually typed project name over the inferred one", async () => {
    mocks.requestMutation.mockResolvedValue({ path: "/Users/me/photos" });
    const { onConfirm } = renderDialog();

    await clickPicker();
    await waitFor(() => expect(screen.getByTitle("/Users/me/photos")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Family" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(onConfirm).toHaveBeenCalledWith("/Users/me/photos", "Family");
  });

  it("stays silent when the user cancels the native picker", async () => {
    mocks.requestMutation.mockResolvedValue({ path: null });
    const { onConfirm } = renderDialog();

    await clickPicker();
    await waitFor(() => expect(mocks.requestMutation).toHaveBeenCalled());

    expect(screen.getByRole("button", { name: PICKER_BUTTON })).toBeEnabled();
    expect(screen.getByText("Add a folder from this computer")).toBeInTheDocument();
    expect(screen.queryByText("Could not open the folder picker")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("reports a busy picker without clearing the already chosen folder", async () => {
    const pickFolder = vi.fn()
      .mockResolvedValueOnce("/Users/me/photos")
      .mockRejectedValueOnce(Object.assign(new Error("busy"), { status: 409 }));
    (window as { mira?: unknown }).mira = { pickFolder };
    const { onConfirm } = renderDialog();

    await clickPicker();
    await waitFor(() => expect(screen.getByTitle("/Users/me/photos")).toBeInTheDocument());

    await clickPicker();
    await waitFor(() =>
      expect(screen.getByText("A folder picker is already open")).toBeInTheDocument(),
    );

    expect(mocks.requestMutation).not.toHaveBeenCalled();
    expect(screen.getByTitle("/Users/me/photos")).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("resets the picked folder and name when the dialog is closed and reopened", async () => {
    mocks.requestMutation.mockResolvedValue({ path: "/Users/me/photos" });
    const onOpenChange = vi.fn();
    const onConfirm = vi.fn();
    const { rerender } = render(
      <AddProjectDialog open onOpenChange={onOpenChange} onConfirm={onConfirm} />,
    );

    await clickPicker();
    await waitFor(() => expect(screen.getByTitle("/Users/me/photos")).toBeInTheDocument());

    rerender(
      <AddProjectDialog open={false} onOpenChange={onOpenChange} onConfirm={onConfirm} />,
    );
    rerender(
      <AddProjectDialog open onOpenChange={onOpenChange} onConfirm={onConfirm} />,
    );

    expect(screen.queryByTitle("/Users/me/photos")).not.toBeInTheDocument();
    expect(screen.getByText("Add a folder from this computer")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
  });
});

describe("sidebar add-project entry", () => {
  it("opens the modal from the Projects header and creates the picked folder as a project", async () => {
    mocks.requestMutation.mockResolvedValue({ path: "/Users/me/photos" });
    const onNewChatInProject = vi.fn();

    render(
      <ChatList
        sessions={[
          {
            key: "websocket:alpha",
            channel: "websocket",
            chatId: "alpha",
            title: "Alpha task",
            createdAt: "2026-05-20T10:00:00Z",
            updatedAt: "2026-05-20T10:00:00Z",
            preview: "",
          } satisfies ChatSummary,
        ]}
        activeKey="websocket:alpha"
        onSelect={vi.fn()}
        onRequestDelete={vi.fn()}
        onTogglePin={vi.fn()}
        onRequestRename={vi.fn()}
        onToggleArchive={vi.fn()}
        onNewChatInProject={onNewChatInProject}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Add project" }));
    expect(await screen.findByRole("dialog", { name: "Create project" })).toBeInTheDocument();

    await clickPicker();
    await waitFor(() => expect(screen.getByTitle("/Users/me/photos")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(onNewChatInProject).toHaveBeenCalledWith("/Users/me/photos", "photos");
  });
});
