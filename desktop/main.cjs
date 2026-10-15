"use strict";

const { app, BrowserWindow, Menu, shell, ipcMain, dialog } = require("electron");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");

const APP_NAME = "知序";
const IS_DEV_SHELL = process.env.MIRA_DESKTOP_DEV === "1";
const SMOKE = process.env.MIRA_SMOKE === "1";
const WEBUI_DEV_URL = process.env.MIRA_WEBUI_URL || "http://localhost:5173";
const NANOBOT_HOME = process.env.NANOBOT_HOME || path.join(os.homedir(), ".nanobot");
const GATEWAY_WAIT_MS = Number(process.env.MIRA_GATEWAY_TIMEOUT_MS || 45000);

let gatewayProc = null;
let mainWindow = null;
let bootToken = 0;

function loadChannelConfig() {
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(NANOBOT_HOME, "config.json"), "utf8"));
    const ws = (cfg.channels && cfg.channels.websocket) || {};
    return {
      port: Number(ws.port) || 8765,
      secret: String(ws.tokenIssueSecret || ws.token || ""),
    };
  } catch {
    return { port: 8765, secret: "" };
  }
}

function httpOk(url, timeoutMs = 800) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      res.resume();
      resolve(res.statusCode >= 200 && res.statusCode < 400);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      resolve(false);
    });
  });
}

function splitCmdLine(line) {
  const parts = line.trim().split(/\s+/);
  return { cmd: parts[0], args: parts.slice(1) };
}

function repoVenvGatewayCmd(repoRoot) {
  const exe =
    process.platform === "win32"
      ? path.join(repoRoot, ".venv", "Scripts", "nanobot.exe")
      : path.join(repoRoot, ".venv", "bin", "nanobot");
  return fs.existsSync(exe) ? { cmd: exe, args: ["gateway"] } : null;
}

function defaultGatewayCmd(repoRoot) {
  const venv = repoVenvGatewayCmd(repoRoot);
  if (venv) return venv;
  try {
    execFileSync(process.platform === "win32" ? "where" : "which", ["nanobot"], { stdio: "ignore" });
    return { cmd: "nanobot", args: ["gateway"] };
  } catch {
    return {
      cmd: process.platform === "win32" ? "python" : "python3",
      args: ["-m", "nanobot", "gateway"],
    };
  }
}

function spawnGateway() {
  const repoRoot = app.isPackaged ? NANOBOT_HOME : path.join(__dirname, "..");
  const spec = process.env.MIRA_GATEWAY_CMD
    ? splitCmdLine(process.env.MIRA_GATEWAY_CMD)
    : defaultGatewayCmd(repoRoot);
  gatewayProc = spawn(spec.cmd, spec.args, {
    cwd: repoRoot,
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  gatewayProc.stdout.on("data", (d) => process.stdout.write(`[gateway] ${d}`));
  gatewayProc.stderr.on("data", (d) => process.stderr.write(`[gateway] ${d}`));
  gatewayProc.on("exit", (code) => {
    process.stderr.write(`[gateway] exited with code ${code}\n`);
    gatewayProc = null;
  });
}

function killGateway() {
  if (!gatewayProc) return;
  const pid = gatewayProc.pid;
  try {
    if (process.platform === "win32") {
      execFileSync("taskkill", ["/pid", String(pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      gatewayProc.kill("SIGTERM");
    }
  } catch {
    try { gatewayProc.kill("SIGKILL"); } catch {}
  }
  gatewayProc = null;
}

async function waitForGateway(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (await httpOk(`http://127.0.0.1:${port}/health`)) return true;
    if (gatewayProc && gatewayProc.exitCode !== null) return false;
    if (Date.now() > deadline) return false;
    await new Promise((r) => setTimeout(r, 400));
  }
}

function webuiUrl(port, secret) {
  const base = IS_DEV_SHELL ? WEBUI_DEV_URL : `http://127.0.0.1:${port}`;
  return secret ? `${base}/#/?bootstrapSecret=${encodeURIComponent(secret)}` : `${base}/`;
}

function showBoot(message, opts = {}) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow
    .loadFile(path.join(__dirname, "boot.html"), {
      query: { msg: message, retry: opts.retry ? "1" : "0" },
    })
    .catch(() => {});
}

async function boot() {
  const token = ++bootToken;
  const { port, secret } = loadChannelConfig();

  if (SMOKE) {
    showBoot("smoke", {});
    setTimeout(() => app.exit(0), 1500);
    return;
  }

  if (IS_DEV_SHELL) {
    await mainWindow.loadURL(webuiUrl(port, secret));
    return;
  }

  showBoot("正在启动本地 gateway…", {});
  let healthy = await httpOk(`http://127.0.0.1:${port}/health`);
  if (!healthy) {
    try {
      spawnGateway();
    } catch (err) {
      if (token === bootToken) showBoot(`无法启动 gateway：${err.message}`, { retry: true });
      return;
    }
    healthy = await waitForGateway(port, GATEWAY_WAIT_MS);
  }
  if (token !== bootToken || !mainWindow || mainWindow.isDestroyed()) return;
  if (!healthy) {
    showBoot(
      "gateway 未能就绪。可先运行 `nanobot gateway`，或设置 MIRA_GATEWAY_CMD 指定启动命令。",
      { retry: true },
    );
    return;
  }
  await mainWindow.loadURL(webuiUrl(port, secret));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 900,
    minHeight: 560,
    center: true,
    title: APP_NAME,
    backgroundColor: "#ffffff",
    autoHideMenuBar: true,
    icon: path.join(__dirname, "assets", "icon.png"),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.once("ready-to-show", () => mainWindow && mainWindow.show());
  mainWindow.setTitle(APP_NAME);
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function buildMenu() {
  const template = [
    { role: "appMenu" },
    { role: "fileMenu" },
    { role: "editMenu" },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    app.setName(APP_NAME);
    buildMenu();
    ipcMain.on("mira:retry", () => boot());
    ipcMain.handle("mira:pick-folder", async () => {
      if (!mainWindow) return null;
      const result = await dialog.showOpenDialog(mainWindow, {
        properties: ["openDirectory", "createDirectory"],
      });
      if (result.canceled || result.filePaths.length === 0) return null;
      return result.filePaths[0];
    });
    ipcMain.handle("mira:reveal-in-folder", (_event, targetPath) => {
      if (typeof targetPath !== "string" || !targetPath.trim()) return false;
      shell.showItemInFolder(path.resolve(targetPath));
      return true;
    });
    ipcMain.handle("mira:open-external", async (_event, targetUrl) => {
      if (typeof targetUrl !== "string" || !/^https?:\/\//i.test(targetUrl)) return false;
      // Defense in depth: the regex only checks the prefix, so also require a
      // full WHATWG parse with an http(s) protocol before handing to the OS.
      let parsed;
      try {
        parsed = new URL(targetUrl);
      } catch {
        return false;
      }
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
      await shell.openExternal(parsed.toString());
      return true;
    });
    createWindow();
    boot();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
        boot();
      }
    });
  });
}

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => killGateway());
