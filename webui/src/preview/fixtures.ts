/**
 * Static sample content for the `?preview=1` shell. Nothing here comes from the
 * backend: the preview never issues a business request, so the rows below are
 * hardcoded illustrations for the chat column and per-page placeholders.
 */
import type { WorkspaceScopePayload } from "@/lib/types";

export const PREVIEW_DEFAULT_SCOPE: WorkspaceScopePayload = {
  project_path: "D:/workspace",
  access_mode: "restricted",
  restrict_to_workspace: true,
};

export interface PreviewSessionRow {
  key: string;
  title: string;
  preview: string;
}

export const previewSessions: PreviewSessionRow[] = [
  { key: "demo-1", title: "整理本周记忆", preview: "把散落的笔记归纳成一篇周回顾。" },
  { key: "demo-2", title: "重构登录流程", preview: "梳理 OAuth 与魔法链接的分支。" },
  { key: "demo-3", title: "阅读论文摘要", preview: "提炼方法部分要点并生成提纲。" },
];

export const previewCapabilityCopy: Record<
  "chat" | "creative" | "agents" | "skills" | "automations" | "apps" | "settings",
  { title: string; body: string }
> = {
  chat: {
    title: "助手（示例）",
    body: "这里将展示真实的对话流。界面预览模式下不会连接后端，消息与工具调用均为静态示例。",
  },
  creative: {
    title: "创作空间（示例）",
    body: "把碎片素材整理成一篇文章。预览模式仅演示布局，正文与素材都是占位内容。",
  },
  agents: {
    title: "智能体（示例）",
    body: "管理子智能体的定义与能力。预览模式展示静态卡片，不代表你的真实配置。",
  },
  skills: {
    title: "Skill（示例）",
    body: "查看与开关内置技能。预览模式仅呈现样式，技能状态为示意。",
  },
  automations: {
    title: "自动化（示例）",
    body: "定时任务与长时目标的管理入口。预览模式展示示例排期。",
  },
  apps: {
    title: "应用（示例）",
    body: "接入的第三方应用与渠道。预览模式展示示意列表。",
  },
  settings: {
    title: "设置（示例）",
    body: "外观、模型、记忆等偏好设置。预览模式展示分区结构，数值为占位。",
  },
};
