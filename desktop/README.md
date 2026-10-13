# Mira 桌面版（desktop/）

Mira 是 Agent-With-Memory（nanobot 引擎）的桌面客户端品牌名：Electron 壳直接复用
`webui/` 的构建产物，不改动、不替代 WebUI —— 两者并存。

页面风格参考 Hope Agent（shiwenwen/hope-agent）：白底浅色、标准窗口边框、
1440×900 默认尺寸、左侧栏 + 会话流布局（布局由现有 WebUI 提供）。

## 与 Hope Agent 的差异（为什么用 Electron）

Hope Agent 用 Tauri（Rust）打包。本机没有 Rust 工具链时，Electron 是最快的
webui→桌面版路径：零 Rust 依赖、纯 JS 主进程、`electron-builder` 直接出
Windows NSIS 安装包。

## 目录

- `main.cjs` — 主进程：读取 `~/.nanobot/config.json`（websocket channel 的
  port/tokenIssueSecret），复用或拉起 `nanobot gateway`，等待 `/health` 就绪后
  加载 `http://127.0.0.1:<port>/#/?bootstrapSecret=…`
- `preload.cjs` — 暴露 `window.mira`（contextIsolation + sandbox）
- `boot.html` — 启动/错误页（Mira 品牌 splash，带重试）
- `assets/icon.png` — 应用图标（1024px，Mira 品牌）

## 使用

```bash
cd desktop
npm install            # 首次安装 electron（国内可设 ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/）
npm start              # 生产模式：自动拉起 gateway 并打开 Mira
npm run dev            # 开发模式：加载 vite dev server（默认 http://localhost:5173，可用 MIRA_WEBUI_URL 覆盖）
npm run smoke          # 无后端冒烟：只验证壳能启动
npm run dist           # 打 Windows NSIS 安装包（release/Mira-Setup-x.y.z.exe）
```

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `MIRA_GATEWAY_CMD` | 自定义 gateway 启动命令（默认 `nanobot gateway`，找不到则 `python -m nanobot gateway`） |
| `MIRA_WEBUI_URL` | dev 模式的 webui 地址 |
| `NANOBOT_HOME` | nanobot 配置目录（默认 `~/.nanobot`） |
| `MIRA_GATEWAY_TIMEOUT_MS` | 等待 gateway 就绪超时（默认 45000） |
| `MIRA_SMOKE` / `MIRA_DESKTOP_DEV` | 冒烟 / 开发模式开关 |
