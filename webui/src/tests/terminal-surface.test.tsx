import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  TerminalSurface,
  type TerminalEmulator,
} from "@/components/workbench/TerminalSurface";
import { getTerminalStore } from "@/hooks/useNanobotStream";
import { NanobotClient } from "@/lib/nanobot-client";
import type { TerminalEvent } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

/**
 * Fake xterm instance: records ``write`` payloads and the PTY-only surface
 * (``onData``/``onResize``/``fit``/``size``) so tests can assert passthrough,
 * resize frames and line-mode fallback without a real renderer in jsdom.
 */
interface RecordedTerminal extends TerminalEmulator {
  writes: string[];
  dataHandler: ((data: string) => void) | null;
  resizeHandler: ((size: { cols: number; rows: number }) => void) | null;
  dataSubscription: { dispose: ReturnType<typeof vi.fn> };
  resizeSubscription: { dispose: ReturnType<typeof vi.fn> };
  fit: ReturnType<typeof vi.fn>;
}

function makeFakeTerminal(): RecordedTerminal {
  const dataSubscription = { dispose: vi.fn() };
  const resizeSubscription = { dispose: vi.fn() };
  const instance: RecordedTerminal = {
    writes: [],
    dataHandler: null,
    resizeHandler: null,
    dataSubscription,
    resizeSubscription,
    fit: vi.fn(),
    open: vi.fn(),
    write(data: string) {
      instance.writes.push(data);
    },
    scrollToBottom: vi.fn(),
    dispose: vi.fn(),
    onData(callback) {
      instance.dataHandler = callback;
      return dataSubscription;
    },
    onResize(callback) {
      instance.resizeHandler = callback;
      return resizeSubscription;
    },
    size: { cols: 120, rows: 40 },
  };
  return instance;
}

interface FakeClient {
  terminalStart: ReturnType<typeof vi.fn>;
  terminalWrite: ReturnType<typeof vi.fn>;
  terminalResize: ReturnType<typeof vi.fn>;
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
    terminalResize: vi.fn(),
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
      // update through ``act`` so the surface re-renders before the next assert.
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
  pty?: boolean,
) {
  return {
    event: "terminal_started",
    type: "terminal_started",
    terminal_id: id,
    shell: "/bin/bash",
    cwd: "/tmp/project",
    status,
    ...(message === undefined ? {} : { message }),
    ...(pty === undefined ? {} : { pty }),
  } as unknown as TerminalEvent;
}

function outputEvent(
  id: string,
  seq: number,
  data: string,
  stream: "stdout" | "stderr" = "stdout",
) {
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

function errorEvent(id: string, detail: string) {
  return { event: "error", terminal_id: id, detail } as unknown as TerminalEvent;
}

const TERMINAL_ID = "term-1";

function setup(options: { terminalId?: string; sessionKey?: string | null } = {}) {
  const terminalId = options.terminalId ?? TERMINAL_ID;
  const client = makeFakeClient();
  const instances: RecordedTerminal[] = [];
  const renderSurface = () =>
    render(
      <ClientProvider client={client as never} token="tok">
        <TerminalSurface
          terminalId={terminalId}
          sessionKey={options.sessionKey ?? null}
          createTerminalInstance={() => {
            const instance = makeFakeTerminal();
            instances.push(instance);
            return instance;
          }}
        />
      </ClientProvider>,
    );
  let utils = renderSurface();
  const latest = (): RecordedTerminal | undefined => instances.at(-1);
  const store = () => getTerminalStore(client as unknown as NanobotClient);
  return {
    client,
    instances,
    latest,
    store: store(),
    remount: () => {
      utils.unmount();
      utils = renderSurface();
    },
    unmount: () => utils.unmount(),
  };
}

describe("TerminalSurface (PTY interactive mode)", () => {
  it("streams raw onData straight to terminalWrite with no echo and no input box", () => {
    const { client, latest, store } = setup();
    client.emit(startedEvent(TERMINAL_ID, "ok", undefined, true));

    const surface = screen.getByTestId(`terminal-surface-${TERMINAL_ID}`);
    expect(surface).toBeInTheDocument();
    // PTY 模式没有行式输入框（stdin 由 xterm 直通）。
    expect(screen.queryByTestId("terminal-input")).not.toBeInTheDocument();

    const emulator = latest();
    expect(emulator?.dataHandler).toBeTypeOf("function");
    act(() => emulator!.dataHandler!("ls\r"));
    expect(client.terminalWrite).toHaveBeenCalledWith(TERMINAL_ID, "ls\r");
    // 回显由 PTY 负责：本地不 appendEcho，也不产生任何 echo 行。
    expect(emulator?.writes.join("")).not.toContain("ls");
    expect(store.getState().byId[TERMINAL_ID]?.lines).toHaveLength(0);
  });

  it("fits the emulator and sends terminal_resize frames on mount and on resize", () => {
    const { client, latest } = setup();
    client.emit(startedEvent(TERMINAL_ID, "ok", undefined, true));

    const emulator = latest();
    expect(emulator?.fit).toHaveBeenCalled();
    expect(client.terminalResize).toHaveBeenCalledWith(TERMINAL_ID, 120, 40);

    act(() => emulator!.resizeHandler!({ cols: 100, rows: 30 }));
    expect(client.terminalResize).toHaveBeenCalledWith(TERMINAL_ID, 100, 30);
  });

  it("unsubscribes data/resize handlers when the PTY terminal exits", () => {
    const { client, latest } = setup();
    client.emit(startedEvent(TERMINAL_ID, "ok", undefined, true));
    const emulator = latest();
    expect(emulator?.dataHandler).toBeTypeOf("function");

    client.emit(exitEvent(TERMINAL_ID, 0));
    expect(emulator?.dataSubscription.dispose).toHaveBeenCalled();
    expect(emulator?.resizeSubscription.dispose).toHaveBeenCalled();
  });
});

describe("TerminalSurface (line mode, migrated from TerminalSection)", () => {
  it("creates no emulator until terminal_started lands and shows the starting hint before that", () => {
    const { client, instances, store } = setup();
    // dock 宿主（Task 9）通过 beginCreate 先登记 starting 记录。
    act(() => store.beginCreate(TERMINAL_ID, null));
    expect(screen.getByText(/Starting terminal/)).toBeInTheDocument();
    expect(instances).toHaveLength(0);

    client.emit(startedEvent(TERMINAL_ID));
    expect(instances).toHaveLength(1);
  });

  it("submits a line with enter, echoes it locally and keeps command history", () => {
    const { client, latest } = setup();
    client.emit(startedEvent(TERMINAL_ID));

    const input = screen.getByTestId("terminal-input");
    fireEvent.change(input, { target: { value: "ls -la" } });
    fireEvent.submit(input);

    expect(client.terminalWrite).toHaveBeenCalledWith(TERMINAL_ID, "ls -la\n");
    expect(latest()?.writes.join("")).toContain("ls -la");
    expect(input).toHaveValue("");

    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(input).toHaveValue("ls -la");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input).toHaveValue("");

    // 行式模式绝不接 stdin、绝不发 resize 帧。
    expect(latest()?.dataHandler).toBeNull();
    expect(client.terminalResize).not.toHaveBeenCalled();
  });

  it("replays buffered output into a fresh emulator and drops the trimmed oldest lines", () => {
    const { client, latest, remount } = setup();
    client.emit(startedEvent(TERMINAL_ID));
    client.emit(outputEvent(TERMINAL_ID, 1, "hello\n"));
    expect(latest()?.writes.join("")).toContain("hello\n");
    // 别的终端 id 的块绝不能写进本 surface。
    client.emit(outputEvent("term-other", 1, "not-mine\n"));
    expect(latest()?.writes.join("")).not.toContain("not-mine");

    for (let seq = 2; seq <= 41; seq += 1) {
      client.emit(outputEvent(TERMINAL_ID, seq, `${String(seq).padStart(4, "0")}-${"x".repeat(6_000)}\n`));
    }
    remount();
    const replayed = latest()?.writes.join("") ?? "";
    expect(replayed).toContain("0041-");
    expect(replayed).not.toContain("0001-");
  });

  it("shows the exit code on terminal_exit", () => {
    const { client } = setup();
    client.emit(startedEvent(TERMINAL_ID));
    client.emit(exitEvent(TERMINAL_ID, 3));
    expect(screen.getByText(/Exited with code 3/)).toBeInTheDocument();
  });

  it("shows the session expired copy when exit_code is null", () => {
    const { client } = setup();
    client.emit(startedEvent(TERMINAL_ID));
    client.emit(exitEvent(TERMINAL_ID, null));
    expect(screen.getByText(/Session expired/)).toBeInTheDocument();
  });

  it("rehydrates a surface from the terminal_list_result the mount probe returns", () => {
    const { client } = setup({ terminalId: "remote-9" });
    client.emit({
      event: "terminal_list_result",
      terminals: [
        { terminal_id: "remote-9", status: "running", shell: "/bin/bash", cwd: "/tmp/a" },
      ],
    } as unknown as TerminalEvent);
    expect(screen.getByTestId("terminal-output-remote-9")).toBeInTheDocument();
    expect(screen.getByTestId("terminal-input")).toBeInTheDocument();
  });

  it("does not kill the terminal when the surface unmounts", () => {
    const { client, unmount } = setup();
    client.emit(startedEvent(TERMINAL_ID));
    unmount();
    expect(client.terminalKill).not.toHaveBeenCalled();
  });

  it("shows the pick-a-project empty state when the server has no usable session", () => {
    const { client, store } = setup();
    act(() => store.beginCreate(TERMINAL_ID, null));
    client.emit(errorEvent(TERMINAL_ID, "terminal_session_unavailable"));
    expect(screen.getByText(/Available after you pick a project/)).toBeInTheDocument();
  });

  it("surfaces an invalid_terminal_data error on a running terminal", () => {
    const { client } = setup();
    client.emit(startedEvent(TERMINAL_ID));
    client.emit(errorEvent(TERMINAL_ID, "invalid_terminal_data"));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The command could not be sent to this terminal.",
    );
  });

  it("surfaces an unknown_terminal error on a running terminal", () => {
    const { client } = setup();
    client.emit(startedEvent(TERMINAL_ID));
    client.emit(errorEvent(TERMINAL_ID, "unknown_terminal"));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This terminal is no longer available. Open a new one.",
    );
  });

  it("shows the limit copy when terminal_started reports limit_reached with a message", () => {
    const { client } = setup();
    client.emit(startedEvent(TERMINAL_ID, "limit_reached", "terminal limit reached (8)"));
    expect(screen.getByRole("alert")).toHaveTextContent(/Terminal limit reached/);
  });

  it("maps shell_unavailable to a stable label", () => {
    const { client } = setup();
    client.emit(startedEvent(TERMINAL_ID, "error", "shell_unavailable"));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This shell is not installed on the host.",
    );
  });

  it("maps terminal_start_failed to the same shell fallback label", () => {
    const { client } = setup();
    client.emit(startedEvent(TERMINAL_ID, "error", "terminal_start_failed"));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This shell is not installed on the host.",
    );
  });
});

/**
 * Transport-layer checks on the real NanobotClient with a scripted socket
 * (migrated from terminal-section.test.tsx + contract v2 shell/resize frames):
 * the outbound frames match contract v2 §4.2 and the inbound events
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

  it("sends contract-shaped terminal_start frames with optional session key and shell", () => {
    client.terminalStart("abcd-123_XY", "websocket:chat-1");
    expect(socket.lastFrame()).toEqual({
      type: "terminal_start",
      terminal_id: "abcd-123_XY",
      session_key: "websocket:chat-1",
    });
    client.terminalStart("term-two");
    expect(socket.lastFrame()).toEqual({ type: "terminal_start", terminal_id: "term-two" });
    // 契约 v2：shell 只在选择时出现。
    client.terminalStart("term-three", "websocket:chat-1", "zsh");
    expect(socket.lastFrame()).toEqual({
      type: "terminal_start",
      terminal_id: "term-three",
      session_key: "websocket:chat-1",
      shell: "zsh",
    });
    client.terminalStart("term-four", null, null);
    expect(socket.lastFrame()).toEqual({ type: "terminal_start", terminal_id: "term-four" });
  });

  it("rejects malformed ids and oversized writes locally without touching the socket", () => {
    const before = socket.sent.length;
    client.terminalWrite("bad id!!", "ls\n");
    client.terminalWrite("term-1", "");
    client.terminalWrite("term-1", "x".repeat(64_001));
    expect(socket.sent.length).toBe(before);
  });

  it("sends terminal_resize frames and guards the 500/200 integer bounds", () => {
    client.terminalResize("term-1", 120, 40);
    expect(socket.lastFrame()).toEqual({
      type: "terminal_resize",
      terminal_id: "term-1",
      cols: 120,
      rows: 40,
    });
    client.terminalResize("term-1", 500, 200);
    expect(socket.lastFrame()).toMatchObject({ cols: 500, rows: 200 });

    const before = socket.sent.length;
    client.terminalResize("term-1", 501, 40);
    client.terminalResize("term-1", 120, 201);
    client.terminalResize("term-1", 0, 40);
    client.terminalResize("term-1", 120, 0);
    client.terminalResize("term-1", 80.5, 24);
    client.terminalResize("bad id!!", 120, 40);
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
    // Wire frames carry both ``event`` and ``type`` keys (WU-A dual-key payloads).
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
