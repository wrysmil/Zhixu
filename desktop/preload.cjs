"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mira", {
  appName: "知序",
  platform: process.platform,
  retryBoot: () => ipcRenderer.send("mira:retry"),
  pickFolder: () => ipcRenderer.invoke("mira:pick-folder"),
  revealInFolder: (targetPath) => ipcRenderer.invoke("mira:reveal-in-folder", targetPath),
  openExternal: (targetUrl) => ipcRenderer.invoke("mira:open-external", targetUrl),
  openBrowserWindow: (targetUrl) => ipcRenderer.invoke("mira:open-browser-window", targetUrl),
});
