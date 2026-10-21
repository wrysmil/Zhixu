import path from "node:path";
import baseConfig from "C:/Users/Huangqh/.codex/worktrees/session-workbench/Zhixu/webui/vite.config";

export default async (env: {command: string; mode: string}) => {
  const base = typeof baseConfig === "function" ? await baseConfig(env) : baseConfig;
  return {
    ...base,
    root: "C:/Users/Huangqh/.codex/worktrees/session-workbench/Zhixu/webui",
    cacheDir: path.resolve(__dirname, "session-workbench-vite-cache"),
  };
};
