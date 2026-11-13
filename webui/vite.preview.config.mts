import { mergeConfig } from "vite";

import baseConfig from "./vite.config";

export default async (env: { command: string; mode: string }) => {
  const base = typeof baseConfig === "function" ? await baseConfig(env as never) : baseConfig;
  return mergeConfig(base, {
    build: {
      outDir: "../.preview-dist",
      emptyOutDir: true,
      manifest: false,
      rollupOptions: {
        input: { preview: "trajectory-preview.html" },
      },
    },
  });
};
