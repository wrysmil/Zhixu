// 终端面板的共享件：从 v1 TerminalSection 原样抽出，供 TerminalSurface（Task 7）
// 与 StartDock（Task 9）复用，避免待删文件与新组件互相 import。

/** Contract §1.1 id shape: ``[A-Za-z0-9_-]{1,64}`` — the generated id stays inside it. */
export function defaultGenerateTerminalId(): string {
  return `term-${crypto.randomUUID()}`;
}

/** Full redraw prefix emitted before a replay after the store trimmed old lines. */
export const CLEAR_SCREEN = "\x1b[2J\x1b[H";

export function failureLabel(
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
  if (message === "terminal_start_failed") {
    return t("startPanel.terminalStartFailed", {
      defaultValue: "The terminal could not start. Open a new one to retry.",
    });
  }
  if (message === "terminal_start_timeout") {
    return t("startPanel.terminalStartTimeout", {
      defaultValue: "Terminal startup timed out. Open a new one to retry.",
    });
  }
  if (message === "terminal_connection_lost") {
    return t("startPanel.terminalConnectionLost", {
      defaultValue: "Connection lost while starting the terminal. Reconnect and try again.",
    });
  }
  if (message === "shell_unavailable") {
    return t("startPanel.shellUnavailable", {
      defaultValue: "This shell is not installed on the host.",
    });
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
