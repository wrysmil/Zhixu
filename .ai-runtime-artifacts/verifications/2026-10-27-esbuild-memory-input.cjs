// Build-only workaround for Windows denying esbuild's removal of temp input.
// esbuild's own fsAsync.writeFile path falls back to its in-memory protocol
// when writing a temporary input fails. Do not change dependencies or outputs.
const fs = require("node:fs");
const path = require("node:path");
const writeFile = fs.writeFile;
fs.writeFile = function (file, ...args) {
  const callback = args.at(-1);
  if (typeof file === "string" && /^esbuild-[0-9a-f]{64}$/.test(path.basename(file)) && typeof callback === "function") {
    const unavailable = new Error("Use esbuild's in-memory transform input on this Windows host");
    unavailable.code = "EACCES";
    queueMicrotask(() => callback(unavailable));
    return;
  }
  return writeFile.call(this, file, ...args);
};
