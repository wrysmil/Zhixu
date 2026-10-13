"use strict";

// Launch electron with the right env per mode: node scripts/run.cjs [start|dev|smoke]
const { spawnSync } = require("child_process");
const path = require("path");

const mode = process.argv[2] || "start";
const env = { ...process.env };
if (mode === "dev") env.MIRA_DESKTOP_DEV = "1";
if (mode === "smoke") env.MIRA_SMOKE = "1";

const electronBin = path.join(
  __dirname,
  "..",
  "node_modules",
  ".bin",
  process.platform === "win32" ? "electron.cmd" : "electron",
);

const r = spawnSync(electronBin, [path.join(__dirname, "..")], {
  stdio: "inherit",
  env,
  shell: process.platform === "win32",
});
process.exit(r.status == null ? 1 : r.status);
