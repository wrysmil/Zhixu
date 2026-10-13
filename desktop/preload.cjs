"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mira", {
  appName: "Mira",
  platform: process.platform,
  retryBoot: () => ipcRenderer.send("mira:retry"),
  pickFolder: () => ipcRenderer.invoke("mira:pick-folder"),
  revealInFolder: (targetPath) => ipcRenderer.invoke("mira:reveal-in-folder", targetPath),
  openExternal: (targetUrl) => ipcRenderer.invoke("mira:open-external", targetUrl),
});
