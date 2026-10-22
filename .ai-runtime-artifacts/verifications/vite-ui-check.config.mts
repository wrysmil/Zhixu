import path from "node:path";

import baseConfig from "../../webui/vite.config";

const root = path.resolve(__dirname, "../../webui");

// 共享工作树里 node_modules/.vite 可能被其他会话的 dev server 占用，
// 本配置只为本轮视觉验证换一个缓存目录，其余与 webui/vite.config.ts 一致。
export default async (env: { command: string; mode: string }) => {
  const base = typeof baseConfig === "function" ? await baseConfig(env) : baseConfig;
  return {
    ...base,
    root,
    cacheDir: path.resolve(__dirname, "vite-ui-check-cache"),
  };
};
