---
route: decision
date: 2026-10-09
topic: desktop-mira-rebrand-scope
---
# 决策：Mira 品牌重塑范围 = 用户可见层，不改 Python 包名

## 背景
用户要求"nanobot 也可以换换，换一个新的名字"。仓库 Python 包 `nanobot/`、CLI 命令
`nanobot`、配置目录 `~/.nanobot`、`NANOBOT_API_URL` 等是引擎级标识符。

## 决策
- **改**：所有用户可见品牌（WebUI i18n 10 语言 value、index.html 标题/描述/boot 文案、
  manifest、sw precache、Sidebar logo、新品牌资产、桌面壳产品名 Mira）。
- **不改**：Python 包名/模块路径、CLI 可执行名、`~/.nanobot` 配置目录、localStorage key
  （`nanobot-webui.theme`、`nanobot.locale`）、内部标识符（useNanobotStream、nanobot-client.ts）、
  i18n key（`nanobotFeatures.*`）、命令字面量 `` `nanobot gateway` ``。

## 理由
包级重命名牵涉 400+ 文件、wheel 发布名、用户既有配置，属独立大重构；本次目标是
"桌面版 + 新品牌外观"。CLI hint 保留字面量保证用户照抄可执行。

## 后果
未来若做引擎改名，另立 spec；届时 localStorage/i18n key 一并迁移。
