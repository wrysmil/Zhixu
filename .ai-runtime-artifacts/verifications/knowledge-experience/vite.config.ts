import base from "../../../webui/vite.config";
import path from "node:path";

export default (env: any) => {
  const config = (base as any)(env);
  return {
    ...config,
    cacheDir: path.resolve(__dirname, "../k-cache"),
    server: { ...config.server, port: 5186, strictPort: true },
  };
};
