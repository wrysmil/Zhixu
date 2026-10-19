// 单终端面板（批次 home-dock-pty-terminal-v1.1 / Task 7）：由 v1 TerminalSection
// 的 TerminalOutput / ActiveTerminal / TerminalInput 三个内聚块移植而来，去掉
// tab-strip / ＋ / close 容器逻辑（标签托管交给 StartDock，Task 9）。
// 双模式：``pty:true`` = xterm 直通交互（回显由宿主 PTY 负责，不发 echo）；
// ``pty`` 缺省/false = v1 行式降级（只读输出面 + 输入框提交整行 + 本地回显）。

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

import { CLEAR_SCREEN, failureLabel } from "@/components/workbench/terminal-shared";
import { useTerminalStore } from "@/hooks/useZhixuStream";
import { useClient } from "@/providers/ClientProvider";

/**
 * Minimal surface of an xterm instance used by this panel; injectable for tests.
 * ``onData`` / ``onResize`` / ``fit`` / ``size`` 只在 PTY 直通模式下需要，
 * 行式降级实例可以省略（组件不会订阅它们）。
 */
export interface TerminalEmulator {
  open(element: HTMLElement): void;
  write(data: string): void;
  scrollToBottom(): void;
  dispose(): void;
  onData?(callback: (data: string) => void): { dispose(): void } | void;
  onResize?(callback: (size: { cols: number; rows: number }) => void): { dispose(): void } | void;
  fit?(): void;
  size?: { cols: number; rows: number };
}

interface TerminalSurfaceProps {
  /** 本面板绑定的终端 id；开/关标签由宿主（StartDock）负责。 */
  terminalId: string;
  /** ``websocket:<chat_id>`` key of the owning session（透传自宿主，作验收/调试锚点）. */
  sessionKey?: string | null;
  /** DI hook: tests inject a recording fake instead of a real xterm. */
  createTerminalInstance?: (tabId: string) => TerminalEmulator;
  /** v1 TerminalSection 的 id 生成 prop 已归 dock 宿主；保留类型位拒绝误用。 */
  generateTerminalId?: never;
}

const TERMINAL_FONT_FAMILY = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

/** PTY 直通终端：xterm 接管 stdin，提示符/回显来自宿主 PTY。 */
function createInteractiveTerminal(): TerminalEmulator {
  const terminal = new Terminal({
    disableStdin: false,
    convertEol: false,
    scrollback: 5000,
    fontSize: 12,
    fontFamily: TERMINAL_FONT_FAMILY,
    cursorBlink: true,
    theme: { background: "transparent" },
  });
  const fitAddon = new FitAddon();
  terminal.loadAddon(fitAddon);
  return {
    open: (element) => {
      terminal.open(element);
      fitAddon.fit();
    },
    write: (data) => terminal.write(data),
    scrollToBottom: () => terminal.scrollToBottom(),
    dispose: () => terminal.dispose(),
    onData: (callback) => terminal.onData(callback),
    onResize: (callback) => terminal.onResize(callback),
    fit: () => fitAddon.fit(),
    get size() {
      return { cols: terminal.cols, rows: terminal.rows };
    },
  };
}

/** 行式降级终端（v1 原样）：只读输出面，输入走下方单行 composer。 */
function createLineTerminal(): TerminalEmulator {
  const terminal = new Terminal({
    // Read-only output surface: input happens in the one-line composer below,
    // so the terminal never needs stdin or a cursor of its own.
    disableStdin: true,
    // Pipe-mode shells emit bare ``\n``; convert so each line starts at column 0.
    convertEol: true,
    scrollback: 5000,
    fontSize: 12,
    fontFamily: TERMINAL_FONT_FAMILY,
    cursorBlink: false,
    theme: { background: "transparent" },
  });
  return {
    open: (element) => terminal.open(element),
    write: (data) => terminal.write(data),
    scrollToBottom: () => terminal.scrollToBottom(),
    dispose: () => terminal.dispose(),
  };
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

/**
 * One terminal, two modes (contract v2 §4.1/§4.2):
 *
 * - ``record.pty === true``: the xterm instance wires ``onData`` straight to
 *   ``terminal_write`` (no local echo — the PTY echoes) and reports geometry
 *   via ``terminal_resize`` on mount, on xterm resize and on container resize.
 * - otherwise: the v1 line console is kept — read-only xterm plus the
 *   one-line composer that submits ``line + "\n"`` and echoes locally.
 *
 * The emulator instance is only created once ``terminal_started`` landed
 * (status !== "starting"), because that reply is what reveals the pty mode.
 */
export function TerminalSurface({
  terminalId,
  sessionKey = null,
  createTerminalInstance,
}: TerminalSurfaceProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const { store, terminals } = useTerminalStore();
  const record = terminals.byId[terminalId] ?? null;

  const containerRef = useRef<HTMLDivElement>(null);
  const emulatorRef = useRef<TerminalEmulator | null>(null);
  const writtenRef = useRef(0);
  // Keep the factory/record in refs so inline props never recreate the terminal.
  const factoryRef = useRef(createTerminalInstance);
  factoryRef.current = createTerminalInstance;
  const recordRef = useRef(record);
  recordRef.current = record;

  const modeKnown = record !== null && record.status !== "starting";
  const ptyMode = record?.pty === true;
  const interactive = ptyMode && record?.status === "running";

  // 创建 emulator：terminal_started 之后（pty 模式此时已知）；创建时把已缓存的
  // 行整体回放一次（含 starting 期间到达的输出）。
  useEffect(() => {
    const element = containerRef.current;
    if (!modeKnown || !element) return;
    const factory =
      factoryRef.current ?? (ptyMode ? createInteractiveTerminal : createLineTerminal);
    const terminal = factory(terminalId);
    emulatorRef.current = terminal;
    terminal.open(element);
    const lines = recordRef.current?.lines ?? [];
    for (const line of lines) terminal.write(line.data);
    writtenRef.current = lines.length;
    terminal.scrollToBottom();
    return () => {
      emulatorRef.current = null;
      writtenRef.current = 0;
      terminal.dispose();
    };
  }, [modeKnown, ptyMode, terminalId]);

  // 增量输出回放（迁移 v1 TerminalOutput）：缓冲被裁剪时 CLEAR_SCREEN 全量重绘。
  useEffect(() => {
    const terminal = emulatorRef.current;
    if (!terminal) return;
    const lines = record?.lines ?? [];
    if (writtenRef.current > lines.length) {
      terminal.write(CLEAR_SCREEN);
      writtenRef.current = 0;
    }
    for (let index = writtenRef.current; index < lines.length; index += 1) {
      terminal.write(lines[index]?.data ?? "");
    }
    writtenRef.current = lines.length;
    terminal.scrollToBottom();
  }, [record?.lines]);

  // PTY 直通：onData 原样写回宿主；fit + 容器/xterm 尺寸变化上报 terminal_resize。
  useEffect(() => {
    const emulator = emulatorRef.current;
    const container = containerRef.current;
    if (!interactive || !emulator || !container) return;
    const unsubData = emulator.onData?.((raw) => client.terminalWrite(terminalId, raw));
    const sendSize = () => {
      emulator.fit?.();
      const size = emulator.size;
      if (size) client.terminalResize(terminalId, size.cols, size.rows);
    };
    const unsubResize = emulator.onResize?.((size) =>
      client.terminalResize(terminalId, size.cols, size.rows),
    );
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(sendSize);
    observer?.observe(container);
    sendSize();
    return () => {
      if (unsubData && "dispose" in unsubData) unsubData.dispose();
      if (unsubResize && "dispose" in unsubResize) unsubResize.dispose();
      observer?.disconnect();
    };
  }, [interactive, terminalId, client]);

  const handleLine = (line: string) => {
    client.terminalWrite(terminalId, `${line}\n`);
    store.appendEcho(terminalId, line);
  };

  return (
    <div
      data-testid={`terminal-surface-${terminalId}`}
      data-session-key={sessionKey ?? undefined}
      className="flex min-h-0 flex-1 flex-col gap-1.5"
    >
      {record?.status === "failed" ? (
        <p role="alert" className="text-[12px] text-destructive">
          {failureLabel(record.message ?? "terminal_error", t)}
        </p>
      ) : null}
      {record?.status === "exited" ? (
        <p role="status" className="text-[12px] text-muted-foreground">
          {record.exitCode === null
            ? t("startPanel.terminalExpired", { defaultValue: "Session expired" })
            : t("startPanel.terminalExit", {
                defaultValue: "Exited with code {{code}}",
                code: record.exitCode,
              })}
        </p>
      ) : null}
      {!record || record.status === "starting" ? (
        <p role="status" className="animate-pulse text-[12px] text-muted-foreground/70">
          {t("startPanel.terminalStarting", { defaultValue: "Starting terminal…" })}
        </p>
      ) : null}
      <div
        ref={containerRef}
        data-testid={`terminal-output-${terminalId}`}
        className="min-h-0 w-full flex-1 overflow-hidden rounded border border-border/50 bg-background text-terminal [&_.xterm]:h-full [&_.xterm]:w-full"
      />
      {ptyMode ? null : (
        <TerminalInput disabled={!record || record.status !== "running"} onSubmit={handleLine} />
      )}
    </div>
  );
}
