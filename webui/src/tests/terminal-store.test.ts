import { describe, expect, it } from "vitest";

import { createTerminalStore, type TerminalStore } from "@/hooks/useZhixuStream";
import type { TerminalEvent } from "@/lib/types";

function started(
  id: string,
  status: "ok" | "already_running" | "limit_reached" | "error" = "ok",
  message?: string,
): TerminalEvent {
  return {
    event: "terminal_started",
    terminal_id: id,
    shell: "/bin/bash",
    cwd: "/tmp/project",
    status,
    ...(message === undefined ? {} : { message }),
  } as unknown as TerminalEvent;
}

function output(id: string, seq: number, data: string): TerminalEvent {
  return {
    event: "terminal_output",
    terminal_id: id,
    seq,
    stream: "stdout",
    data,
  } as unknown as TerminalEvent;
}

function exit(id: string, exitCode: number | null): TerminalEvent {
  return { event: "terminal_exit", terminal_id: id, exit_code: exitCode } as unknown as TerminalEvent;
}

function detail(id: string, value: string): TerminalEvent {
  return { event: "error", terminal_id: id, detail: value } as TerminalEvent;
}

function openTerminal(store: TerminalStore, id = "term-1"): void {
  store.beginCreate(id, null);
  store.applyEvent(started(id));
}

describe("createTerminalStore ghost-tab tombstone", () => {
  it("drops a late terminal_output for a removed id instead of resurrecting the tab", () => {
    const store = createTerminalStore();
    openTerminal(store, "term-1");
    store.remove("term-1");
    // The gateway's _finalize_exit flushes buffered output before terminal_exit;
    // that straggler frame must not re-create the record the user just closed.
    store.applyEvent(output("term-1", 9, "late output\n"));
    expect(store.getState().byId["term-1"]).toBeUndefined();
    expect(store.getState().order).not.toContain("term-1");
  });

  it("drops a late terminal_exit for a removed id and clears its tombstone", () => {
    const store = createTerminalStore();
    openTerminal(store, "term-1");
    store.remove("term-1");
    store.applyEvent(exit("term-1", 0));
    expect(store.getState().byId["term-1"]).toBeUndefined();
    // Tombstone cleared on exit: re-creating the same id is no longer shadowed.
    store.beginCreate("term-1", null);
    store.applyEvent(output("term-1", 1, "fresh\n"));
    expect(store.getState().byId["term-1"]?.lines.map((line) => line.data)).toEqual(["fresh\n"]);
  });

  it("still drops output between remove and the matching exit", () => {
    const store = createTerminalStore();
    openTerminal(store, "term-1");
    store.remove("term-1");
    store.applyEvent(output("term-1", 2, "a\n"));
    store.applyEvent(output("term-1", 3, "b\n"));
    store.applyEvent(exit("term-1", null));
    expect(store.getState().byId["term-1"]).toBeUndefined();
    expect(store.getState().order).not.toContain("term-1");
  });

  it("drops a terminal_started that lands after the tab was closed while starting", () => {
    const store = createTerminalStore();
    store.beginCreate("term-1", null);
    store.remove("term-1");
    store.applyEvent(started("term-1"));
    expect(store.getState().byId["term-1"]).toBeUndefined();
    expect(store.getState().order).not.toContain("term-1");
    // Recreating the same id clears its tombstone.
    store.beginCreate("term-1", null);
    store.applyEvent(started("term-1"));
    expect(store.getState().byId["term-1"]?.status).toBe("running");
  });

  it("skips tombstoned ids inside a terminal_list_result snapshot", () => {
    const store = createTerminalStore();
    openTerminal(store, "term-1");
    store.remove("term-1");
    store.applyEvent({
      event: "terminal_list_result",
      terminals: [{ terminal_id: "term-1", status: "running", shell: "/bin/bash", cwd: "/tmp/project" }],
    } as unknown as TerminalEvent);
    expect(store.getState().byId["term-1"]).toBeUndefined();
    expect(store.getState().order).not.toContain("term-1");
  });
});

describe("createTerminalStore error frames", () => {
  it("surfaces an error frame on a running terminal record", () => {
    const store = createTerminalStore();
    openTerminal(store, "term-1");
    store.applyEvent(detail("term-1", "invalid_terminal_data"));
    const record = store.getState().byId["term-1"];
    expect(record?.status).toBe("failed");
    expect(record?.message).toBe("invalid_terminal_data");
  });

  it("surfaces unknown_terminal errors on a running terminal too", () => {
    const store = createTerminalStore();
    openTerminal(store, "term-1");
    store.applyEvent(detail("term-1", "unknown_terminal"));
    expect(store.getState().byId["term-1"]).toMatchObject({
      status: "failed",
      message: "unknown_terminal",
    });
  });

  it("does not downgrade an exited record when a stray error arrives", () => {
    const store = createTerminalStore();
    openTerminal(store, "term-1");
    store.applyEvent(exit("term-1", 0));
    store.applyEvent(detail("term-1", "unknown_terminal"));
    const record = store.getState().byId["term-1"];
    expect(record?.status).toBe("exited");
    expect(record?.exitCode).toBe(0);
  });

  it("keeps failing starting records and drops errors without a live record", () => {
    const store = createTerminalStore();
    store.beginCreate("term-1", null);
    store.applyEvent(detail("term-1", "terminal_session_unavailable"));
    expect(store.getState().byId["term-1"]).toMatchObject({
      status: "failed",
      message: "terminal_session_unavailable",
    });
    store.beginCreate("term-2", null);
    store.remove("term-2");
    store.applyEvent(detail("term-2", "unknown_terminal"));
    expect(store.getState().byId["term-2"]).toBeUndefined();
  });
});

describe("createTerminalStore limit token", () => {
  it("stores the stable limit_reached token, not the backend prose message", () => {
    const store = createTerminalStore();
    store.beginCreate("term-1", null);
    // terminal_bridge.py sends message=f"terminal limit reached ({max})".
    store.applyEvent(started("term-1", "limit_reached", "terminal limit reached (8)"));
    const record = store.getState().byId["term-1"];
    expect(record?.status).toBe("failed");
    expect(record?.message).toBe("limit_reached");
  });

  it("keeps the raw message for plain start errors", () => {
    const store = createTerminalStore();
    store.beginCreate("term-1", null);
    store.applyEvent(started("term-1", "error", "terminal_start_failed"));
    expect(store.getState().byId["term-1"]?.message).toBe("terminal_start_failed");
  });
});
