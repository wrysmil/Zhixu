"use strict";

const { contextBridge, ipcRenderer } = require("electron");

const windowStateListeners = new Set();

ipcRenderer.on("mira:window-state", (_event, state) => {
  for (const listener of windowStateListeners) {
    try {
      listener(state);
    } catch {
      // A broken renderer listener must not break the shell bridge.
    }
  }
});

contextBridge.exposeInMainWorld("mira", {
  appName: "知序",
  platform: process.platform,
  retryBoot: () => ipcRenderer.send("mira:retry"),
  pickFolder: () => ipcRenderer.invoke("mira:pick-folder"),
  revealInFolder: (targetPath) => ipcRenderer.invoke("mira:reveal-in-folder", targetPath),
  openExternal: (targetUrl) => ipcRenderer.invoke("mira:open-external", targetUrl),
  openBrowserWindow: (targetUrl) => ipcRenderer.invoke("mira:open-browser-window", targetUrl),
  // 自绘标题栏：窗口控制与菜单动作
  minimizeWindow: () => ipcRenderer.send("mira:window", "minimize"),
  toggleMaximizeWindow: () => ipcRenderer.send("mira:window", "toggle-maximize"),
  closeWindow: () => ipcRenderer.send("mira:window", "close"),
  getWindowState: () => ipcRenderer.invoke("mira:window-state"),
  onWindowState: (listener) => {
    if (typeof listener !== "function") return () => {};
    windowStateListeners.add(listener);
    return () => {
      windowStateListeners.delete(listener);
    };
  },
  runMenuAction: (action) => ipcRenderer.invoke("mira:menu-action", action),
});
