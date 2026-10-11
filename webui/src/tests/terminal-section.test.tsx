import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAX_OPEN_TERMINALS,
  TerminalSection,
  type TerminalEmulator,
} from "@/components/workbench/TerminalSection";
import { NanobotClient } from "@/lib/nanobot-client";
import type { TerminalEvent } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

/**
 * Fake xterm instance: records ``write`` payloads so tests can assert
 * per-terminal routing and local echo without a real renderer in jsdom.
 */
interface RecordedTerminal extends TerminalEmulator {
  writes: string[];
}

function makeFakeTerminal(): RecordedTerminal {
  return {
    writes: [],
    open: vi.fn(),
    write(data: string) {
      this.writes.push(data);
    },
    scrollToBottom: vi.fn(),
    dispose: vi.fn(),
  };
}

interface FakeClient {
  terminalStart: ReturnType<typeof vi.fn>;
  terminalWrite: ReturnType<typeof vi.fn>;
  terminalKill: ReturnType<typeof vi.fn>;
  terminalList: ReturnType<typeof vi.fn>;
  onTerminalEvent: ReturnType<typeof vi.fn>;
  emit: (event: TerminalEvent) => void;
}

function makeFakeClient(): FakeClient {
  const handlers = new Set<(event: TerminalEvent) => void>();
  return {
    terminalStart: vi.fn(),
    terminalWrite: vi.fn(),
    terminalKill: vi.fn(),
    terminalList: vi.fn(),
    onTerminalEvent: vi.fn((handler: (event: TerminalEvent) => void) => {
      handlers.add(handler);
      return () => {
        handlers.delete(handler);
      };
    }),
    emit(event) {
      // Terminal events land synchronously from the WS layer; flush the store
      // update through ``act`` so the panel re-renders before the next assert.
      act(() => {
        for (const handler of [...handlers]) handler(event);
      });
    },
  };
}

function startedEvent(
  id: string,
  status: "ok" | "already_running" | "limit_reached" | "error" = "ok",
  message?: string,
) {
  return {
    event: "terminal_started",
    type: "terminal_started",
    terminal_id: id,
    shell: "/bin/bash",
    cwd: "/tmp/project",
    status,
    ...(message === undefined ? {} : { message }),
  } as unknown as TerminalEvent;
}

function outputEvent(id: string, seq: number, data: string, stream: "stdout" | "stderr" = "stdout") {
  return {
    event: "terminal_output",
    type: "terminal_output",
    terminal_id: id,
    seq,
    stream,
    data,
  } as unknown as TerminalEvent;
}

function exitEvent(id: string, exitCode: number | null) {
  return {
    event: "terminal_exit",
    type: "terminal_exit",
    terminal_id: id,
    exit_code: exitCode,
  } as unknown as TerminalEvent;
}

function setup(options: { sessionKey?: string | null } = {}) {
  const client = makeFakeClient();
  const instances = new Map<string, RecordedTerminal[]>();
  let nextId = 0;
  const utils = render(
    <ClientProvider client={client as never} token="tok">
      <TerminalSection
        sessionKey={options.sessionKey}
        generateTerminalId={() => `term-${++nextId}`}
        createTerminalInstance={(tabId) => {
          const instance = makeFakeTerminal();
          const list = instances.get(tabId) ?? [];
          list.push(instance);
          instances.set(tabId, list);
          return instance;
        }}
      />
    </ClientProvider>,
  );
  const createTab = () => fireEvent.click(screen.getByTestId("terminal-new-button"));
  const latest = (tabId: string): RecordedTerminal | undefined => instances.get(tabId)?.at(-1);
  return { ...utils, client, instances, createTab, latest };
}

describe("TerminalSection", () => {
  it("asks the gateway for visible terminals on mount", () => {
    const { client } = setup();
    expect(client.terminalList).toHaveBeenCalledTimes(1);
  });

  it("creates a tab and sends terminal_start with the generated id and session key", () => {
    const { client, createTab } = setup({ sessionKey: "websocket:chat-1" });
    createTab();
    expect(client.terminalStart).toHaveBeenCalledWith("term-1", "websocket:chat-1");
    expect(screen.getByRole("tab", { name: /Terminal 1/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("terminal-output-term-1")).toBeInTheDocument();

    client.emit(startedEvent("term-1"));
    expect(screen.getByRole("tab", { name: /Terminal 1/ })).toBeInTheDocument();
  });

  it("omits the session key for the home panel so the server resolves the default chat", () => {
    const { client, createTab } = setup();
    createTab();
    expect(client.terminalStart).toHaveBeenCalledWith("term-1", null);
  });

  it("routes terminal_output chunks to the matching tab and replays buffered output on tab switch", () => {
    const { client, createTab, latest } = setup();
    createTab();
    client.emit(startedEvent("term-1"));

    createTab();
    client.emit(startedEvent("term-2"));
    // term-2 is active now; a term-1 chunk must not touch the active instance.
    client.emit(outputEvent("term-1", 1, "hello-1\n"));
    expect(latest("term-2")?.writes.join("")).not.toContain("hello-1");

    client.emit(outputEvent("term-2", 1, "hello-2\n"));
    expect(latest("term-2")?.writes.join("")).toContain("hello-2\n");

    // Switching back writes the buffered term-1 output into a fresh instance.
    fireEvent.click(screen.getByRole("tab", { name: /Terminal 1/ }));
    expect(latest("term-1")?.writes.join("")).toContain("hello-1\n");
  });

  it("shows the exit code on terminal_exit", () => {
    const { client, createTab } = setup();
    createTab();
    client.emit(startedEvent("term-1"));
    client.emit(exitEvent("term-1", 3));
    expect(screen.getByText(/Exited with code 3/)).toBeInTheDocument();
  });

  it("shows the session expired copy when exit_code is null", () => {
    const { client, createTab } = setup();
    createTab();
    client.emit(startedEvent("term-1"));
    client.emit(exitEvent("term-1", null));
    expect(screen.getByText(/Session expired/)).toBeInTheDocument();
  });

  it("disables the new-terminal button at the limit with the limit tooltip", () => {
    const { client, createTab } = setup();
    for (let index = 0; index < MAX_OPEN_TERMINALS; index += 1) {
      createTab();
      client.emit(startedEvent(`term-${index + 1}`));
    }
    const button = screen.getByTestId("terminal-new-button");
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleName(/Terminal limit reached/);
    expect(client.terminalStart).toHaveBeenCalledTimes(MAX_OPEN_TERMINALS);
  });

  it("submits a line with enter, echoes it locally and keeps command history", () => {
    const { client, createTab, latest } = setup();
    createTab();
    client.emit(startedEvent("term-1"));

    const input = screen.getByTestId("terminal-input");
    fireEvent.change(input, { target: { value: "ls -la" } });
    fireEvent.submit(input);

    expect(client.terminalWrite).toHaveBeenCalledWith("term-1", "ls -la\n");
    expect(latest("term-1")?.writes.join("")).toContain("ls -la");
    expect(input).toHaveValue("");

    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(input).toHaveValue("ls -la");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input).toHaveValue("");
  });

  it("closes a tab by sending terminal_kill and removing it from the tab bar", () => {
    const { client, createTab } = setup();
    createTab();
    client.emit(startedEvent("term-1"));
    fireEvent.click(screen.getByTestId("terminal-close-term-1"));
    expect(client.terminalKill).toHaveBeenCalledWith("term-1");
    expect(screen.queryByRole("tab", { name: /Terminal 1/ })).not.toBeInTheDocument();
  });

  it("does not resurrect a closed tab from a late terminal_output frame", () => {
    const { client, createTab } = setup();
    createTab();
    client.emit(startedEvent("term-1"));
    fireEvent.click(screen.getByTestId("terminal-close-term-1"));
    // The gateway flushes buffered output before terminal_exit; this straggler
    // frame must be dropped by the store's remove tombstone.
    client.emit(outputEvent("term-1", 9, "late output\n"));
    expect(screen.queryByRole("tab", { name: /Terminal 1/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("terminal-output-term-1")).not.toBeInTheDocument();
  });

  it("does not kill live terminals when the section unmounts", () => {
    const { client, createTab, unmount } = setup();
    createTab();
    client.emit(startedEvent("term-1"));
    unmount();
    expect(client.terminalKill).not.toHaveBeenCalled();
  });

  it("shows the pick-a-project empty state when the server has no usable session", () => {
    const { client, createTab } = setup();
    createTab();
    client.emit({
      event: "error",
      terminal_id: "term-1",
      detail: "terminal_session_unavailable",
    } as TerminalEvent);
    expect(screen.getByText(/Available after you pick a project/)).toBeInTheDocument();
  });

  it("surfaces an invalid_terminal_data error on a running terminal", () => {
    const { client, createTab } = setup();
    createTab();
    client.emit(startedEvent("term-1")); // status -> running
    client.emit({
      event: "error",
      terminal_id: "term-1",
      detail: "invalid_terminal_data",
    } as TerminalEvent);
    const alert = screen.getByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert.textContent).toBeTruthy();
  });

  it("surfaces an unknown_terminal error on a running terminal", () => {
    const { client, createTab } = setup();
    createTab();
    client.emit(startedEvent("term-1"));
    client.emit({
      event: "error",
      terminal_id: "term-1",
      detail: "unknown_terminal",
    } as TerminalEvent);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("shows the limit copy when terminal_started reports limit_reached with a message", () => {
    const { client, createTab } = setup();
    createTab();
    client.emit(startedEvent("term-1", "limit_reached", "terminal limit reached (8)"));
    expect(screen.getByRole("alert")).toHaveTextContent(/Terminal limit reached/);
  });

  it("trims the oldest buffered output when a terminal exceeds the buffer limit", () => {
    const { client, createTab, latest } = setup();
    createTab();
    client.emit(startedEvent("term-1"));
    for (let seq = 1; seq <= 40; seq += 1) {
      client.emit(outputEvent("term-1", seq, `${String(seq).padStart(4, "0")}-${"x".repeat(6_000)}\n`));
    }
    createTab();
    client.emit(startedEvent("term-2"));
    fireEvent.click(screen.getByRole("tab", { name: /Terminal 1/ }));
    const replayed = latest("term-1")?.writes.join("") ?? "";
    expect(replayed).toContain("0040-");
    expect(replayed).not.toContain("0001-");
  });

  it("renders the hint state before any terminal is created", () => {
    setup();
    expect(screen.getByTestId("terminal-section")).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("rehydrates tabs from the terminal_list_result the mount probe returns", () => {
    const { client } = setup();
    // No local tab exists yet, so three listed terminals can only come from the
    // gateway reply to the mount-time ``terminalList`` call.
    client.emit({
      event: "terminal_list_result",
      terminals: [
        { terminal_id: "remote-1", status: "running", shell: "/bin/bash", cwd: "/tmp/a" },
        { terminal_id: "remote-2", status: "running", shell: "/bin/bash", cwd: "/tmp/b" },
        { terminal_id: "remote-3", status: "exited", shell: "/bin/bash", cwd: "/tmp/c" },
      ],
    } as unknown as TerminalEvent);
    expect(screen.getAllByRole("tab")).toHaveLength(3);
    expect(screen.getByTestId("terminal-output-remote-3")).toBeInTheDocument();
  });
});

/**
 * Transport-layer checks on the real NanobotClient with a scripted socket:
 * the four outbound frames match contract §1.1 and the four inbound events
 * (plus terminal-scoped error frames) reach ``onTerminalEvent`` subscribers.
 */
class FakeSocket {
  static instances: FakeSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;

  readyState = FakeSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor() {
    FakeSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  fakeOpen() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  fakeMessage(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  lastFrame(): Record<string, unknown> {
    const raw = this.sent.at(-1);
    if (typeof raw !== "string") throw new Error("no frame sent");
    return JSON.parse(raw) as Record<string, unknown>;
  }
}

describe("NanobotClient terminal channel", () => {
  let client: NanobotClient;
  let socket: FakeSocket;

  beforeEach(() => {
    FakeSocket.instances = [];
    client = new NanobotClient({
      url: "ws://test",
      reconnect: false,
      socketFactory: () => new FakeSocket() as unknown as WebSocket,
    });
    client.connect();
    socket = FakeSocket.instances[FakeSocket.instances.length - 1];
    socket.fakeOpen();
  });

  afterEach(() => {
    client.close();
  });

  it("sends contract-shaped terminal_start frames with an optional session key", () => {
    client.terminalStart("abcd-123_XY", "websocket:chat-1");
    expect(socket.lastFrame()).toEqual({
      type: "terminal_start",
      terminal_id: "abcd-123_XY",
      session_key: "websocket:chat-1",
    });
    client.terminalStart("term-two");
    expect(socket.lastFrame()).toEqual({ type: "terminal_start", terminal_id: "term-two" });
  });

  it("rejects malformed ids and oversized writes locally without touching the socket", () => {
    const before = socket.sent.length;
    client.terminalWrite("bad id!!", "ls\n");
    client.terminalWrite("term-1", "");
    client.terminalWrite("term-1", "x".repeat(64_001));
    expect(socket.sent.length).toBe(before);
  });

  it("sends terminal_write / terminal_kill / terminal_list frames", () => {
    client.terminalWrite("term-1", "ls -la\n");
    expect(socket.lastFrame()).toEqual({ type: "terminal_write", terminal_id: "term-1", data: "ls -la\n" });
    client.terminalKill("term-1");
    expect(socket.lastFrame()).toEqual({ type: "terminal_kill", terminal_id: "term-1" });
    client.terminalList();
    expect(socket.lastFrame()).toEqual({ type: "terminal_list" });
  });

  it("fans terminal_* events to onTerminalEvent subscribers via the event key", () => {
    const seen: TerminalEvent[] = [];
    const unsubscribe = client.onTerminalEvent((event) => seen.push(event));
    // Wire frames carry both ``event`` and ``type`` (WU-A dual-key payloads).
    socket.fakeMessage({
      event: "terminal_output",
      type: "terminal_output",
      terminal_id: "term-1",
      seq: 2,
      stream: "stdout",
      data: "hi\n",
    });
    socket.fakeMessage({
      event: "terminal_exit",
      type: "terminal_exit",
      terminal_id: "term-1",
      exit_code: 0,
    });
    socket.fakeMessage({
      event: "terminal_list_result",
      type: "terminal_list_result",
      terminals: [{ terminal_id: "term-1", status: "exited", shell: "/bin/bash", cwd: "/tmp" }],
    });
    expect(seen.map((event) => event.event)).toEqual([
      "terminal_output",
      "terminal_exit",
      "terminal_list_result",
    ]);
    unsubscribe();
    socket.fakeMessage({ event: "error", terminal_id: "term-9", detail: "unknown_terminal" });
    expect(seen.some((event) => event.event === "error")).toBe(false);
  });

  it("routes error frames carrying a terminal_id to terminal subscribers only", () => {
    const seen: TerminalEvent[] = [];
    client.onTerminalEvent((event) => seen.push(event));
    socket.fakeMessage({
      event: "error",
      terminal_id: "term-1",
      detail: "terminal_session_unavailable",
    });
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ event: "error", detail: "terminal_session_unavailable" });
  });

  it("leaves non-terminal chat traffic on the per-chat pipeline", () => {
    const chatEvents: unknown[] = [];
    const terminalEvents: unknown[] = [];
    client.onChat("chat-1", (event) => chatEvents.push(event));
    client.onTerminalEvent((event) => terminalEvents.push(event));
    socket.fakeMessage({ event: "session_updated", chat_id: "chat-1" });
    socket.fakeMessage({ event: "error", chat_id: "chat-1", detail: "message_rejected" });
    expect(terminalEvents).toHaveLength(0);
    expect(chatEvents).toHaveLength(1);
  });
});
