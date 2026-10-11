import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { Plus, X } from "lucide-react";

import { useTerminalStore, type TerminalRecord } from "@/hooks/useNanobotStream";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";

// Mirrors the gateway limit from contract 2026-10-11-home-start-panel §1.1
// (terminal_bridge.py module constant DEFAULT_MAX_TERMINALS — a gateway-wide
// cap, not per chat); the server answers ``terminal_started status=limit_reached``
// when it is hit.
export const MAX_OPEN_TERMINALS = 8;

/** Minimal surface of an xterm instance used by this panel; injectable for tests. */
export interface TerminalEmulator {
  open(element: HTMLElement): void;
  write(data: string): void;
  scrollToBottom(): void;
  dispose(): void;
}

export interface TerminalSectionProps {
  /** ``websocket:<chat_id>`` key of the owning session; ``null``/omitted lets the
   * server resolve the default chat (home panel before a thread exists). */
  sessionKey?: string | null;
  /** DI hook: tests inject a recording fake instead of a real xterm. */
  createTerminalInstance?: (tabId: string) => TerminalEmulator;
  /** DI hook for deterministic terminal ids in tests. */
  generateTerminalId?: () => string;
}

// Contract §1.1 id shape: ``[A-Za-z0-9_-]{1,64}`` — the generated id stays inside it.
function defaultGenerateTerminalId(): string {
  return `term-${crypto.randomUUID()}`;
}

function createDefaultTerminalInstance(): TerminalEmulator {
  return new Terminal({
    // Read-only output surface: input happens in the one-line composer below,
    // so the terminal never needs stdin or a cursor of its own.
    disableStdin: true,
    // Pipe-mode shells emit bare ``\n``; convert so each line starts at column 0.
    convertEol: true,
    scrollback: 5000,
    fontSize: 12,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    cursorBlink: false,
    theme: { background: "transparent" },
  });
}

/** Full redraw prefix emitted before a replay after the store trimmed old lines. */
const CLEAR_SCREEN = "\x1b[2J\x1b[H";

interface TerminalOutputProps {
  record: TerminalRecord;
  createTerminalInstance: (tabId: string) => TerminalEmulator;
}

function TerminalOutput({ record, createTerminalInstance }: TerminalOutputProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<TerminalEmulator | null>(null);
  const writtenRef = useRef(0);
  // Keep the factory in a ref so an inline prop function never recreates the terminal.
  const factoryRef = useRef(createTerminalInstance);
  factoryRef.current = createTerminalInstance;

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const terminal = factoryRef.current(record.id);
    terminalRef.current = terminal;
    terminal.open(element);
    return () => {
      terminalRef.current = null;
      writtenRef.current = 0;
      terminal.dispose();
    };
  }, [record.id]);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return;
    const lines = record.lines;
    if (writtenRef.current > lines.length) {
      // The buffer trimmed: clear and redraw what remains.
      terminal.write(CLEAR_SCREEN);
      writtenRef.current = 0;
    }
    for (let index = writtenRef.current; index < lines.length; index += 1) {
      terminal.write(lines[index]?.data ?? "");
    }
    writtenRef.current = lines.length;
    terminal.scrollToBottom();
  }, [record.lines]);

  return (
    <div
      data-testid={`terminal-output-${record.id}`}
      ref={containerRef}
      className="h-72 w-full overflow-hidden rounded border border-border/50 bg-background [&_.xterm]:h-full [&_.xterm]:w-full"
    />
  );
}

interface TerminalInputProps {
  disabled: boolean;
  onSubmit: (line: string) => void;
}

function TerminalInput({ disabled, onSubmit }: TerminalInputProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const line = draft.replace(/\s+$/, "");
    if (!line) return;
    onSubmit(line);
    setHistory((entries) => [...entries, line]);
    setCursor(null);
    setDraft("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowUp") {
      if (history.length === 0) return;
      event.preventDefault();
      const next = cursor === null ? history.length - 1 : Math.max(0, cursor - 1);
      setCursor(next);
      setDraft(history[next] ?? "");
    } else if (event.key === "ArrowDown") {
      if (cursor === null) return;
      event.preventDefault();
      const next = cursor + 1;
      if (next >= history.length) {
        setCursor(null);
        setDraft("");
      } else {
        setCursor(next);
        setDraft(history[next] ?? "");
      }
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex items-center gap-1">
      <label className="sr-only" htmlFor="terminal-command-input">
        {t("startPanel.terminalInput", { defaultValue: "Command input" })}
      </label>
      <input
        id="terminal-command-input"
        data-testid="terminal-input"
        type="text"
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={handleKeyDown}
        spellCheck={false}
        autoComplete="off"
        placeholder={t("startPanel.terminalInputPlaceholder", {
          defaultValue: "Type a command and press Enter",
        })}
        className="h-6 min-w-0 flex-1 rounded border border-border/50 bg-background px-1.5 font-mono text-[11.5px] text-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring/60 disabled:opacity-50"
      />
    </form>
  );
}

function failureLabel(
  message: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (message === "terminal_session_unavailable") {
    return t("startPanel.openProjectFirst", {
      defaultValue: "Available after you pick a project",
    });
  }
  if (message === "limit_reached" || message === "terminal_limit_reached") {
    return t("startPanel.terminalLimit", { defaultValue: "Terminal limit reached (max 8)" });
  }
  // Stable detail tokens from the gateway (inbound_commands.py): running tabs
  // surface write-path failures here instead of silently swallowing the frame.
  if (message === "unknown_terminal" || message === "terminal_not_attached") {
    return t("startPanel.terminalGone", {
      defaultValue: "This terminal is no longer available. Open a new one.",
    });
  }
  if (
    message === "invalid_terminal_data"
    || message === "invalid_terminal_id"
    || message === "access_denied"
  ) {
    return t("startPanel.terminalCommandRejected", {
      defaultValue: "The command could not be sent to this terminal.",
    });
  }
  return message;
}

interface ActiveTerminalProps {
  record: TerminalRecord;
  createTerminalInstance: (tabId: string) => TerminalEmulator;
  onLine: (line: string) => void;
}

function ActiveTerminal({ record, createTerminalInstance, onLine }: ActiveTerminalProps) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-1.5">
      {record.status === "failed" ? (
        <p role="alert" className="text-[11.5px] text-destructive">
          {failureLabel(record.message ?? "terminal_error", t)}
        </p>
      ) : null}
      {record.status === "exited" ? (
        <p role="status" className="text-[11.5px] text-muted-foreground">
          {record.exitCode === null
            ? t("startPanel.terminalExpired", { defaultValue: "Session expired" })
            : t("startPanel.terminalExit", {
                defaultValue: "Exited with code {{code}}",
                code: record.exitCode,
              })}
        </p>
      ) : null}
      {record.status === "starting" ? (
        <p role="status" className="animate-pulse text-[11.5px] text-muted-foreground/70">
          {t("startPanel.terminalStarting", { defaultValue: "Starting terminal…" })}
        </p>
      ) : null}
      <TerminalOutput record={record} createTerminalInstance={createTerminalInstance} />
      <TerminalInput disabled={record.status !== "running"} onSubmit={onLine} />
    </div>
  );
}

/**
 * Multi-tab read-only terminal panel (contract 2026-10-11-home-start-panel §1.2).
 *
 * Output arrives on the WS ``terminal_*`` events and is buffered per terminal id
 * in the shared client store; each tab replays that buffer into a fresh xterm
 * view. Closing a tab sends ``terminal_kill``; unmounting the panel does not —
 * the gateway reclaims idle terminals on its own.
 */
export function TerminalSection({
  sessionKey,
  createTerminalInstance,
  generateTerminalId,
}: TerminalSectionProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const { store, terminals } = useTerminalStore();
  const [activeId, setActiveId] = useState<string | null>(null);

  useEffect(() => {
    client.terminalList();
  }, [client]);

  const factory = createTerminalInstance ?? createDefaultTerminalInstance;
  const order = terminals.order;
  const atLimit = order.length >= MAX_OPEN_TERMINALS;
  const resolvedRecord = activeId ? terminals.byId[activeId] ?? null : null;
  const currentRecord =
    resolvedRecord ?? (order.length > 0 ? terminals.byId[order[order.length - 1]!] ?? null : null);

  const createTerminal = () => {
    if (atLimit) return;
    const id = (generateTerminalId ?? defaultGenerateTerminalId)();
    const key = sessionKey ?? null;
    store.beginCreate(id, key);
    setActiveId(id);
    client.terminalStart(id, key);
  };

  const closeTerminal = (id: string) => {
    client.terminalKill(id);
    store.remove(id);
    if (activeId === id) setActiveId(null);
  };

  return (
    <div data-testid="terminal-section" className="flex flex-col gap-1.5">
      <div
        role="tablist"
        aria-label={t("startPanel.terminalTabs", { defaultValue: "Terminals" })}
        className="flex flex-wrap items-center gap-1"
      >
        {order.map((id, index) => {
          const isActive = currentRecord?.id === id;
          return (
            <span key={id} className="flex items-center gap-0.5">
              <button
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => setActiveId(id)}
                className={cn(
                  "rounded px-2 py-0.5 text-[11.5px] text-muted-foreground/80 enabled:hover:bg-muted/60 enabled:hover:text-foreground",
                  isActive && "bg-muted/70 font-medium text-foreground",
                )}
              >
                {t("startPanel.terminalTab", { defaultValue: "Terminal" })} {index + 1}
              </button>
              <button
                type="button"
                data-testid={`terminal-close-${id}`}
                aria-label={t("startPanel.terminalClose", { defaultValue: "Close terminal" })}
                onClick={() => closeTerminal(id)}
                className="grid h-4 w-4 shrink-0 place-items-center rounded text-muted-foreground/60 hover:bg-muted/60 hover:text-foreground"
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            </span>
          );
        })}
        <button
          type="button"
          data-testid="terminal-new-button"
          disabled={atLimit}
          aria-label={
            atLimit
              ? t("startPanel.terminalLimit", { defaultValue: "Terminal limit reached (max 8)" })
              : t("startPanel.terminal", { defaultValue: "New terminal" })
          }
          title={
            atLimit
              ? t("startPanel.terminalLimit", { defaultValue: "Terminal limit reached (max 8)" })
              : t("startPanel.terminal", { defaultValue: "New terminal" })
          }
          onClick={createTerminal}
          className="grid h-6 w-6 shrink-0 place-items-center rounded text-muted-foreground/75 enabled:hover:bg-muted/60 enabled:hover:text-foreground disabled:opacity-40"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>

      {currentRecord ? (
        <ActiveTerminal
          key={currentRecord.id}
          record={currentRecord}
          createTerminalInstance={factory}
          onLine={(line) => {
            client.terminalWrite(currentRecord.id, `${line}\n`);
            store.appendEcho(currentRecord.id, line);
          }}
        />
      ) : (
        <p className="text-[12px] text-muted-foreground/60">
          {t("startPanel.terminalPlaceholder", {
            defaultValue: "The command console will appear here.",
          })}
        </p>
      )}
    </div>
  );
}
