// 单终端面板（批次 home-dock-pty-terminal-v1.1 / Task 7）：由 v1 TerminalSection
// 的 TerminalOutput / ActiveTerminal / TerminalInput 三个内聚块移植而来，去掉
// tab-strip / ＋ / close 容器逻辑（标签托管交给 StartDock，Task 9）。
// 双模式：``pty:true`` = xterm 直通交互（回显由宿主 PTY 负责，不发 echo）；
// ``pty`` 缺省/false = v1 行式降级（只读输出面 + 输入框提交整行 + 本地回显）。

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import {
  ChevronRight,
  ClipboardPaste,
  Copy,
  CornerDownLeft,
  Eraser,
  Moon,
  SquareTerminal,
  Sun,
} from "lucide-react";
import "@xterm/xterm/css/xterm.css";

import { CLEAR_SCREEN, failureLabel } from "@/components/workbench/terminal-shared";
import { useTerminalStore } from "@/hooks/useZhixuStream";
import { APPEARANCE_APPLIED_EVENT } from "@/lib/appearance";
import { copyTextToClipboard, readTextFromClipboard } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
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
  updateAppearance?(theme: TerminalTheme): void;
  getSelection?(): string;
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
  /** False while the dock tab is hidden; stops stdin/resize work without unmounting. */
  active?: boolean;
}

const TERMINAL_FONT_FAMILY = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

export type TerminalTheme = "dark" | "light";

export const TERMINAL_THEME_STORAGE_KEY = "zhixu-webui.terminal-theme";

export const TERMINAL_DARK_BACKGROUND = "#0b0f19";

const TERMINAL_DARK_COLORS = {
  background: TERMINAL_DARK_BACKGROUND,
  foreground: "#e6e9ef",
  cursor: "#8fb0e8",
  cursorAccent: TERMINAL_DARK_BACKGROUND,
  selectionBackground: "#2b3a55",
  selectionForeground: "#e6e9ef",
};

function readTerminalTheme(): TerminalTheme {
  try {
    return localStorage.getItem(TERMINAL_THEME_STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function updateTerminalAppearance(terminal: Terminal, theme: TerminalTheme) {
  const styles = getComputedStyle(document.documentElement);
  const color = (name: string, fallback: string) => {
    const value = styles.getPropertyValue(name).trim();
    return value ? `hsl(${value})` : fallback;
  };
  terminal.options.fontFamily = styles.getPropertyValue("--font-code").trim() || TERMINAL_FONT_FAMILY;
  terminal.options.theme = theme === "dark"
    ? { ...terminal.options.theme, ...TERMINAL_DARK_COLORS }
    : {
      ...terminal.options.theme,
      background: color("--background", "#ffffff"),
      foreground: color("--foreground", "#222222"),
      cursor: color("--primary", "#222222"),
      cursorAccent: color("--background", "#ffffff"),
      selectionBackground: color("--accent", "#b7c9bd"),
      selectionForeground: color("--accent-foreground", "#222222"),
    };
}

/** PTY 直通终端：xterm 接管 stdin，提示符/回显来自宿主 PTY。 */
function createInteractiveTerminal(theme: TerminalTheme): TerminalEmulator {
  const terminal = new Terminal({
    disableStdin: false,
    convertEol: false,
    scrollback: 5000,
    fontSize: 13,
    lineHeight: 1.55,
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
    updateAppearance: (nextTheme) => updateTerminalAppearance(terminal, nextTheme ?? theme),
    getSelection: () => terminal.getSelection(),
    get size() {
      return { cols: terminal.cols, rows: terminal.rows };
    },
  };
}

/** 行式降级终端（v1 原样）：只读输出面，输入走下方单行 composer。 */
function createLineTerminal(theme: TerminalTheme): TerminalEmulator {
  const terminal = new Terminal({
    // Read-only output surface: input happens in the one-line composer below,
    // so the terminal never needs stdin or a cursor of its own.
    disableStdin: true,
    // Pipe-mode shells emit bare ``\n``; convert so each line starts at column 0.
    convertEol: true,
    scrollback: 5000,
    fontSize: 13,
    lineHeight: 1.55,
    fontFamily: TERMINAL_FONT_FAMILY,
    cursorBlink: false,
    theme: { background: "transparent" },
  });
  const fitAddon = new FitAddon();
  terminal.loadAddon(fitAddon);
  return {
    open: (element) => terminal.open(element),
    write: (data) => terminal.write(data),
    scrollToBottom: () => terminal.scrollToBottom(),
    dispose: () => terminal.dispose(),
    updateAppearance: (nextTheme) => updateTerminalAppearance(terminal, nextTheme ?? theme),
    getSelection: () => terminal.getSelection(),
    fit: () => fitAddon.fit(),
  };
}

interface TerminalInputProps {
  disabled: boolean;
  theme: TerminalTheme;
  onSubmit: (line: string) => void;
}

function TerminalInput({ disabled, theme, onSubmit }: TerminalInputProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const dark = theme === "dark";

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
    <form
      onSubmit={handleSubmit}
      className={cn(
        "shrink-0 border-t p-2.5",
        dark ? "border-[#1b2333] bg-[#0b0f19]" : "border-border/50 bg-muted/20",
      )}
    >
      <div
        className={cn(
          "flex items-center gap-2 rounded-lg border px-2.5 shadow-sm transition-colors",
          dark
            ? "border-[#26314a] bg-[#111726] focus-within:border-[#3d5a8a] focus-within:ring-2 focus-within:ring-[#26314a]"
            : "border-border/70 bg-background focus-within:border-primary/45 focus-within:ring-2 focus-within:ring-primary/10",
        )}
      >
      <ChevronRight
        className={cn("h-3.5 w-3.5 shrink-0", dark ? "text-[#8fb0e8]" : "text-primary/80")}
        aria-hidden
      />
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
        className={cn(
          "h-9 min-w-0 flex-1 border-0 bg-transparent p-0 font-mono text-[12px] outline-none disabled:opacity-50",
          dark
            ? "text-[#e6e9ef] placeholder:text-[#7c8798]"
            : "text-foreground placeholder:text-muted-foreground/65",
        )}
      />
      <button
        type="submit"
        aria-label="Enter"
        title="Enter"
        disabled={disabled || !draft.trim()}
        className={cn(
          "grid h-6 w-6 shrink-0 place-items-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-30",
          dark
            ? "text-[#8b96a8] enabled:hover:bg-[#1b2333] enabled:hover:text-[#e6e9ef]"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        <CornerDownLeft className="h-3.5 w-3.5" aria-hidden />
      </button>
      </div>
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
  active = true,
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
  const interactive = ptyMode && record?.status === "running" && active;
  const shellName = record?.shell?.split(/[\\/]/).pop()?.replace(/\.exe$/i, "") || "Terminal";
  const [terminalTheme, setTerminalTheme] = useState<TerminalTheme>(readTerminalTheme);
  const terminalThemeRef = useRef(terminalTheme);
  terminalThemeRef.current = terminalTheme;

  useEffect(() => {
    try {
      localStorage.setItem(TERMINAL_THEME_STORAGE_KEY, terminalTheme);
    } catch {
      /* localStorage may be unavailable in private mode */
    }
    emulatorRef.current?.updateAppearance?.(terminalTheme);
  }, [terminalTheme]);

  // 创建 emulator：terminal_started 之后（pty 模式此时已知）；创建时把已缓存的
  // 行整体回放一次（含 starting 期间到达的输出）。
  useEffect(() => {
    const element = containerRef.current;
    if (!modeKnown || !element) return;
    const terminal = factoryRef.current
      ? factoryRef.current(terminalId)
      : ptyMode
        ? createInteractiveTerminal(terminalThemeRef.current)
        : createLineTerminal(terminalThemeRef.current);
    emulatorRef.current = terminal;
    terminal.open(element);
    const applyAppearance = () => {
      terminal.updateAppearance?.(terminalThemeRef.current);
      terminal.fit?.();
    };
    applyAppearance();
    window.addEventListener(APPEARANCE_APPLIED_EVENT, applyAppearance);
    const lines = recordRef.current?.lines ?? [];
    for (const line of lines) terminal.write(line.data);
    writtenRef.current = lines.length;
    terminal.scrollToBottom();
    return () => {
      window.removeEventListener(APPEARANCE_APPLIED_EVENT, applyAppearance);
      emulatorRef.current = null;
      writtenRef.current = 0;
      terminal.dispose();
    };
  }, [modeKnown, ptyMode, terminalId]);

  // 管道终端也需跟随面板尺寸重排，否则默认 80 列会撑出横向滚动条。
  useEffect(() => {
    const element = containerRef.current;
    if (!modeKnown || ptyMode || !active || !element) return;
    const fit = () => emulatorRef.current?.fit?.();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(fit);
    observer?.observe(element);
    fit();
    return () => observer?.disconnect();
  }, [modeKnown, ptyMode, active, terminalId]);

  const viewEpoch = record?.viewEpoch ?? 0;

  // Snapshot replay: a takeover snapshot replaces the local buffer wholesale.
  useEffect(() => {
    const terminal = emulatorRef.current;
    if (!terminal) return;
    terminal.write(CLEAR_SCREEN);
    const lines = recordRef.current?.lines ?? [];
    for (const line of lines) terminal.write(line.data);
    writtenRef.current = lines.length;
    terminal.scrollToBottom();
  }, [viewEpoch]);

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

  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 1200);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const copyOutput = async () => {
    const selected = emulatorRef.current?.getSelection?.() ?? "";
    const text = selected || (recordRef.current?.lines ?? []).map((line) => line.data).join("");
    if (!text) return;
    if (await copyTextToClipboard(text)) {
      setNotice(t("dock.copied", { defaultValue: "Copied" }));
    }
  };

  const pasteInput = async () => {
    const current = recordRef.current;
    if (!current || current.status !== "running") return;
    const text = await readTextFromClipboard();
    if (text === null) return;
    if (current.pty) {
      client.terminalWrite(terminalId, text);
    } else {
      client.terminalWrite(terminalId, `${text}\n`);
      store.appendEcho(terminalId, text);
    }
  };

  const handleLine = (line: string) => {
    client.terminalWrite(terminalId, `${line}\n`);
    store.appendEcho(terminalId, line);
  };

  return (
    <div
      data-testid={`terminal-surface-${terminalId}`}
      data-session-key={sessionKey ?? undefined}
      className="m-2 flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border/60 bg-background shadow-[0_2px_12px_-6px_hsl(var(--foreground)/0.12)]"
    >
      {record?.status === "failed" ? (
        <p role="alert" className="border-b border-destructive/15 bg-destructive/5 px-3 py-2 text-[12px] text-destructive">
          {failureLabel(record.message ?? "terminal_error", t)}
        </p>
      ) : null}
      {record?.status === "exited" ? (
        <p role="status" className="border-b border-border/50 bg-muted/30 px-3 py-2 text-[12px] text-muted-foreground">
          {record.exitCode === null
            ? t("startPanel.terminalExpired", { defaultValue: "Session expired" })
            : t("startPanel.terminalExit", {
                defaultValue: "Exited with code {{code}}",
                code: record.exitCode,
              })}
        </p>
      ) : null}
      <div className="flex shrink-0 items-center gap-2 border-b border-border/50 bg-muted/20 px-3 py-2.5">
        <SquareTerminal className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate font-mono text-[12px] font-medium text-foreground">{shellName}</span>
            <span
              aria-hidden
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${record?.status === "running" ? "bg-emerald-500" : record?.status === "failed" ? "bg-destructive" : "bg-muted-foreground/40"}`}
            />
          </div>
          {record?.cwd ? (
            <p title={record.cwd} className="mt-0.5 truncate font-mono text-[10px] leading-4 text-muted-foreground/75">{record.cwd}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-0.5 rounded-lg border border-border/40 bg-background/70 p-0.5">
        <button
          type="button"
          data-testid={`terminal-theme-${terminalId}`}
          aria-label={t("dock.terminalTheme", { defaultValue: "Toggle terminal theme" })}
          title={t("dock.terminalTheme", { defaultValue: "Toggle terminal theme" })}
          aria-pressed={terminalTheme === "dark"}
          onClick={() => setTerminalTheme((value) => (value === "dark" ? "light" : "dark"))}
          className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {terminalTheme === "dark" ? (
            <Sun className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <Moon className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
        <button
          type="button"
          data-testid={`terminal-copy-${terminalId}`}
          aria-label={t("dock.copyOutput", { defaultValue: "Copy output" })}
          title={t("dock.copyOutput", { defaultValue: "Copy output" })}
          onClick={() => void copyOutput()}
          className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Copy className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          data-testid={`terminal-clear-${terminalId}`}
          aria-label={t("dock.clearOutput", { defaultValue: "Clear terminal" })}
          title={t("dock.clearOutput", { defaultValue: "Clear terminal" })}
          onClick={() => store.clear(terminalId)}
          className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Eraser className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          data-testid={`terminal-paste-${terminalId}`}
          aria-label={t("dock.pasteInput", { defaultValue: "Paste" })}
          title={t("dock.pasteInput", { defaultValue: "Paste" })}
          disabled={!record || record.status !== "running"}
          onClick={() => void pasteInput()}
          className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground transition-colors enabled:hover:bg-muted enabled:hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-30"
        >
          <ClipboardPaste className="h-3.5 w-3.5" aria-hidden />
        </button>
        </div>
      </div>
      {notice ? <span role="status" className="px-3 pt-2 text-[11px] text-muted-foreground">{notice}</span> : null}
      {record?.historyTruncated ? (
        <p role="status" className="px-3 pt-2 text-[11.5px] text-amber-700 dark:text-amber-200">
          {t("dock.historyTruncated", {
            defaultValue: "Earlier output was truncated",
          })}
        </p>
      ) : null}
      {!record || record.status === "starting" ? (
        <p role="status" className="px-3 pt-3 text-[12px] text-muted-foreground/70">
          {t("startPanel.terminalStarting", { defaultValue: "Starting terminal…" })}
        </p>
      ) : null}
      <div
        ref={containerRef}
        data-testid={`terminal-output-${terminalId}`}
        data-terminal-theme={terminalTheme}
        className={cn(
          "min-h-0 w-full flex-1 overflow-hidden px-3 py-3 text-terminal [&_.xterm]:h-full [&_.xterm]:w-full [&_.xterm-viewport]:scrollbar-thin [&_.xterm-viewport]:!overflow-x-hidden",
          terminalTheme === "dark" ? "bg-[#0b0f19]" : "bg-background",
        )}
      />
      {ptyMode ? null : (
        <TerminalInput
          disabled={!record || record.status !== "running"}
          theme={terminalTheme}
          onSubmit={handleLine}
        />
      )}
    </div>
  );
}
