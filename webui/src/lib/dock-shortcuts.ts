// dock 快捷键解析（批次 home-dock-pty-terminal-v1.1 Task 11 / spec B2）。
// ⌘P（或 Ctrl+P）= 开/聚焦文件标签，⌘T = 开/聚焦浏览器标签，
// ⌘J = 打开终端面板（入口与按钮一致）。
// 首页与会话页一致（不再「仅首页生效」），scope 由调用方（App）按 activeKey 传入。
// 纯函数，只判定修饰键语义，便于脱离 App 测试。
export type DockShortcutSection = "file" | "browser" | "terminal";

/** Pure shortcut resolver so the ⌘P/⌘T/⌘J mapping stays testable outside App. */
export function resolveDockShortcut(
  event: globalThis.KeyboardEvent,
): DockShortcutSection | null {
  if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return null;
  switch (event.key.toLowerCase()) {
    case "p":
      return "file";
    case "t":
      return "browser";
    case "j":
      return "terminal";
    default:
      return null;
  }
}
